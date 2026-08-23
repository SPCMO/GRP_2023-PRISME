# -*- coding: utf-8 -*-
"""Onglet Crues — bloc 4 : détection automatique des crues à partir des fichiers déjà
générés par GRP (CRITERES_PERF.DAT / SELECTION_EVT.DAT, voir modules.criteres_perf),
affichées en vignettes avec les seuils de vigilance PHyC (bloc 2) et les indicateurs de
performance de référence. L'utilisateur coche les crues à inclure dans la campagne
(bloc 5, onglet Campagne).
"""

import os
import tkinter as tk
from tkinter import messagebox, ttk

from modules.criteres_perf import CriteresPerfError, parse_criteres_perf, parse_evenement_serie
from modules.grp_paths import GrpPaths
from ui.widgets_common import bouton_enregistrer, bouton_info, make_label, make_row, make_scrollable_tab, make_section

TEXTE_INFO_TYPEVT_P = (
    "GRP détecte deux types d'épisodes dans CRITERES_PERF.DAT :\n\n"
    "• les crues (TypEvt=Q) : pic de débit significatif ;\n"
    "• les épisodes de pluie (TypEvt=P) : pluie notable qui n'a pas forcément "
    "généré un pic de débit marquant.\n\n"
    "Par défaut, seules les crues (Q) sont affichées puisque c'est ce qui intéresse "
    "le calage. Cocher cette case ajoute aussi les épisodes de pluie — utile pour "
    "évaluer le comportement du modèle sur de la pluie sans crue (moins de faux "
    "positifs, par exemple)."
)

VIGNETTES_PAR_LIGNE = 3


def _construire_grp_paths(app):
    """Construit un GrpPaths depuis la config actuelle — retourne None (avec message
    explicite) si la configuration n'est pas encore complète, plutôt que de lever une
    exception qui casserait l'onglet."""
    chemins = app.config_data.get("chemins", {})
    station = app.config_data.get("station", {})
    manquants = []
    if not chemins.get("dossier_resultats"):
        manquants.append("dossier 00_Resultats_<station> (onglet Configuration)")
    if not station.get("code_site"):
        manquants.append("code site (identifiez la station via PHyC, onglet Configuration)")
    if manquants:
        return None, manquants
    return GrpPaths(
        dossier_grp=chemins.get("dossier_grp", ""),
        dossier_donnees=chemins.get("dossier_donnees", ""),
        dossier_bddtr=chemins.get("dossier_bddtr", ""),
        dossier_resultats=chemins["dossier_resultats"],
        code_site=station["code_site"],
    ), []


def build_tab_crues(tab_frame, app):
    frm = make_scrollable_tab(tab_frame)
    app.config_data.setdefault("crues_selectionnees", [])

    inn, bg = make_section(frm, "Détection automatique des crues (GRP)", "violet")

    r = make_row(inn, bg)
    make_label(r, "Pas de temps (calage en place) :", bg, width=28)
    var_pdt_libelle = tk.StringVar()
    combo_pdt = ttk.Combobox(r, textvariable=var_pdt_libelle, state="readonly", width=18)
    combo_pdt.pack(side=tk.LEFT, padx=(2, 8))
    # Persisté dans config.json (pas seulement en mémoire) — sans ça, le réglage
    # repartait à "décoché" à chaque relance de l'outil (constaté par l'utilisateur).
    var_inclure_pluie = tk.BooleanVar(value=app.config_data.get("crues_inclure_pluie", False))

    def _sauver_inclure_pluie():
        app.config_data["crues_inclure_pluie"] = var_inclure_pluie.get()
        app.persist_config()
        _rafraichir()

    tk.Checkbutton(r, text="Inclure aussi les événements de pluie (TypEvt=P)",
                   variable=var_inclure_pluie, bg=bg,
                   command=_sauver_inclure_pluie).pack(side=tk.LEFT, padx=(12, 0))
    bouton_info(r, "Événements de pluie (TypEvt=P)", TEXTE_INFO_TYPEVT_P, bg=bg).pack(
        side=tk.LEFT, padx=(2, 0))
    ttk.Button(r, text="Rafraîchir", command=lambda: _rafraichir()).pack(side=tk.LEFT, padx=(12, 0))

    r = make_row(inn, bg)
    ttk.Button(r, text="Tout sélectionner", command=lambda: _tout(True)).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(r, text="Tout désélectionner", command=lambda: _tout(False)).pack(side=tk.LEFT)

    var_statut = tk.StringVar(value="")
    tk.Label(inn, textvariable=var_statut, bg=bg, fg="#a94442",
             font=("TkDefaultFont", 9, "italic")).pack(anchor="w", pady=(2, 0))

    cadre_vignettes = tk.Frame(inn, bg=bg)
    cadre_vignettes.pack(fill=tk.BOTH, expand=True, pady=6)

    vignette_vars = {}  # crue_date_iso -> BooleanVar

    def _code_pdt_courant():
        for p in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if p["libelle"] == var_pdt_libelle.get():
                return p["code"]
        return None

    def _sauver_selection():
        app.config_data["crues_selectionnees"] = [
            iso for iso, v in vignette_vars.items() if v.get()
        ]
        app.persist_config()

    def _tout(valeur):
        for v in vignette_vars.values():
            v.set(valeur)
        _sauver_selection()

    def _couleur_vigilance(qmax):
        """Couleur de fond de la vignette selon le débit max de la crue comparé aux
        seuils PHyC (bloc 2) — repère visuel rapide, indépendant des SeuilV1/V2/V3
        internes à LISTE_BASSINS.DAT (voir Aide.html, les deux ne doivent pas être
        confondus)."""
        seuils = app.config_data.get("seuils_q", {})
        if qmax is None:
            return "#EAECEE"
        paliers = [
            (seuils.get("rouge"), "#F5B7B1"), (seuils.get("orange"), "#FAD7A0"),
            (seuils.get("jaune"), "#F9E79F"),
        ]
        for seuil, couleur in paliers:
            if seuil is not None and qmax >= seuil:
                return couleur
        return "#D5F5E3"

    def _rafraichir():
        for w in cadre_vignettes.winfo_children():
            w.destroy()
        vignette_vars.clear()
        var_statut.set("")

        paths, manquants = _construire_grp_paths(app)
        if paths is None:
            var_statut.set("Configuration incomplète : " + " ; ".join(manquants))
            return

        code_pdt = _code_pdt_courant()
        if not code_pdt:
            var_statut.set("Sélectionnez un pas de temps (défini dans l'onglet Paramétrage).")
            return

        chemin_criteres = paths.criteres_perf_dat(code_pdt)
        try:
            evenements = parse_criteres_perf(chemin_criteres)
        except FileNotFoundError:
            var_statut.set(
                f"Aucun CRITERES_PERF.DAT trouvé pour ce pas de temps ({chemin_criteres}) — "
                "lancez d'abord un calage complet (exe 01 + exe 03) pour ce pas de temps."
            )
            return
        except CriteresPerfError as e:
            var_statut.set(f"Erreur de lecture de CRITERES_PERF.DAT : {e}")
            return

        if not var_inclure_pluie.get():
            evenements = [e for e in evenements if e.est_crue]

        if not evenements:
            var_statut.set("Aucun événement trouvé dans CRITERES_PERF.DAT pour ce filtre.")
            return

        deja_selectionnes = set(app.config_data.get("crues_selectionnees", []))

        def _cumul_pluie(evt):
            """Cumul de pluie de bassin sur l'épisode (mm) sur toute la fenêtre
            DateDeb-DateFin de l'événement, lue depuis <code_site>-EVxxxx.DAT (même
            fichier que le tracé de la crue dans Dashboard > Détail par crue).
            Pobs y est déjà en mm par pas de temps — sommée directement, SANS
            conversion. L'en-tête du fichier ("Pobs(mm/h)") est trompeur : une
            interprétation en intensité mm/h (÷ durée du pas de temps) avait d'abord
            été tentée, mais donnait des cumuls trop faibles (33 mm sur la crue #1,
            l'épisode historique de l'Aude d'octobre 2018 à Qmax=1648 m³/s) comparés
            aux cumuls réellement observés pour cet événement (150-300 mm, Météo-
            France) — confirmé par l'utilisateur : c'est la somme brute qui est juste.
            None si le fichier est absent, illisible, ou vide — n'empêche jamais
            l'affichage du reste de la vignette, juste ce champ précis."""
            chemin_serie = os.path.join(
                paths.evenements_dir(code_pdt), f"{paths.code_site}-EV{evt.num_evt:04d}.DAT")
            try:
                serie = parse_evenement_serie(chemin_serie)
            except (FileNotFoundError, CriteresPerfError):
                return None
            if not serie:
                return None
            return sum(p for _d, p, _q in serie)

        for i, evt in enumerate(evenements):
            ligne, colonne = divmod(i, VIGNETTES_PAR_LIGNE)
            couleur = _couleur_vigilance(evt.qmax)
            vignette = tk.Frame(cadre_vignettes, bg=couleur, relief=tk.RIDGE, borderwidth=2)
            vignette.grid(row=ligne, column=colonne, padx=5, pady=5, sticky="nsew")
            cadre_vignettes.grid_columnconfigure(colonne, weight=1)

            iso = evt.date_deb.isoformat()
            var = tk.BooleanVar(value=iso in deja_selectionnes)
            vignette_vars[iso] = var

            tk.Checkbutton(
                vignette, bg=couleur,
                text=f"#{evt.num_evt} — {evt.date_deb:%d/%m/%Y %H:%M}",
                variable=var, command=_sauver_selection,
                font=("TkDefaultFont", 9, "bold"),
            ).pack(anchor="w")
            cumul_pluie = _cumul_pluie(evt)
            texte_pluie = f"{cumul_pluie:.1f} mm" if cumul_pluie is not None else "indisponible"
            tk.Label(vignette, bg=couleur, font=("TkDefaultFont", 8),
                     text=f"Qmax : {evt.qmax:.1f} m³/s   (pic le {evt.date_qmax:%d/%m %H:%M})").pack(anchor="w")
            tk.Label(vignette, bg=couleur, font=("TkDefaultFont", 8),
                     text=f"Cumul pluie de l'épisode : {texte_pluie}").pack(anchor="w")

            texte_perf = f"dQP {evt.dqp}%  dTP {evt.dtp}  VE {evt.ve}%  KGE {evt.kge}"
            if evt.suspects:
                texte_perf += "  ⚠ suspect : " + ", ".join(evt.suspects)
            tk.Label(vignette, bg=couleur, font=("TkDefaultFont", 8),
                     fg="#7B241C" if evt.suspects else "#333333", text=texte_perf,
                     wraplength=200, justify=tk.LEFT).pack(anchor="w")

        var_statut.set(f"{len(evenements)} événement(s) trouvé(s).")

    def _rafraichir_combo_pdt():
        pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        combo_pdt["values"] = [p["libelle"] for p in pdt_list]
        if pdt_list and var_pdt_libelle.get() not in combo_pdt["values"]:
            var_pdt_libelle.set(pdt_list[0]["libelle"])
        _rafraichir()

    combo_pdt.bind("<<ComboboxSelected>>", lambda *_: _rafraichir())
    _rafraichir_combo_pdt()

    # ── Bouton Enregistrer ───────────────────────────────────────────────────────
    bouton_enregistrer(frm, app).pack(fill=tk.X, padx=12, pady=14)
