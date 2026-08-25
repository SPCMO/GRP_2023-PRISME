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
from modules.grp_paths import construire_grp_paths
from ui.widgets_common import (
    bouton_enregistrer, bouton_info, enregistrer_observateur_pdt, libelle_dernier_pdt,
    make_label, make_row, make_scrollable_tab, make_section, sauvegarder_dernier_pdt,
)

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

VIGNETTES_PAR_LIGNE = 4

# Ordre croissant de sévérité — utilisé pour trier les vignettes par niveau de
# vigilance max atteint (demandé explicitement, plutôt que par date/numéro seul).
_ORDRE_NIVEAUX_VIGILANCE = ("Rouge", "ZT rouge", "Orange", "ZT orange", "Jaune", "ZT jaune", "Vert")


def _eclaircir(couleur_hex, facteur=0.45):
    """Éclaircit une couleur hex vers le blanc (facteur 0=inchangé, 1=blanc pur) — sert
    à simuler une transparence pour les niveaux ZT (Tkinter n'a pas d'alpha réel sur
    les couleurs de fond des widgets)."""
    couleur_hex = couleur_hex.lstrip("#")
    r, g, b = int(couleur_hex[0:2], 16), int(couleur_hex[2:4], 16), int(couleur_hex[4:6], 16)
    r = int(r + (255 - r) * facteur)
    g = int(g + (255 - g) * facteur)
    b = int(b + (255 - b) * facteur)
    return f"#{r:02X}{g:02X}{b:02X}"



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

    # Ascenseur horizontal "au cas où" (demandé) : avec 4 vignettes par ligne plutôt
    # que 3, et le texte dQP/dTP/VE/KGE tenu sur une seule ligne (voir plus bas), la
    # grille peut devenir plus large que la fenêtre visible — un Canvas dédié avec son
    # propre défilement horizontal permet d'y accéder sans dépendre du seul
    # redimensionnement de la fenêtre. Le défilement vertical de l'onglet reste géré
    # par make_scrollable_tab (ce canvas-ci n'a donc pas vocation à défiler
    # verticalement — sa hauteur est recalée sur son contenu à chaque rafraîchissement).
    cadre_canvas = tk.Frame(inn, bg=bg)
    cadre_canvas.pack(fill=tk.BOTH, expand=True, pady=6)
    canvas_vignettes = tk.Canvas(cadre_canvas, bg=bg, highlightthickness=0)
    ascenseur_h = ttk.Scrollbar(cadre_canvas, orient=tk.HORIZONTAL, command=canvas_vignettes.xview)
    canvas_vignettes.configure(xscrollcommand=ascenseur_h.set)
    canvas_vignettes.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    ascenseur_h.pack(side=tk.TOP, fill=tk.X)
    cadre_vignettes = tk.Frame(canvas_vignettes, bg=bg)
    fenetre_vignettes = canvas_vignettes.create_window((0, 0), window=cadre_vignettes, anchor="nw")

    def _molette_horizontale(e):
        canvas_vignettes.xview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas_vignettes.bind("<Enter>", lambda e: canvas_vignettes.bind_all("<Shift-MouseWheel>", _molette_horizontale))
    canvas_vignettes.bind("<Leave>", lambda e: canvas_vignettes.unbind_all("<Shift-MouseWheel>"))

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

    def _niveau_et_couleur_vigilance(qmax):
        """Niveau de vigilance PHyC (7 classes : Vert/ZT jaune/Jaune/ZT orange/Orange/
        ZT rouge/Rouge, comparaison du débit max de la crue aux seuils du bloc 2,
        indépendant des SeuilV1/V2/V3 internes à LISTE_BASSINS.DAT — voir Aide.html,
        les deux ne doivent pas être confondus) et sa couleur de fond associée. Les ZT
        (zones de transition) utilisent une version éclaircie de la couleur pleine du
        palier suivant (Tkinter ne supporte pas une vraie transparence alpha sur une
        couleur de fond — l'éclaircissement en tient lieu visuellement)."""
        seuils = app.config_data.get("seuils_q", {})
        if qmax is None:
            return "Vert", "#EAECEE"
        paliers = [
            ("rouge", "Rouge", "#F5B7B1"), ("zt_rouge", "ZT rouge", _eclaircir("#F5B7B1")),
            ("orange", "Orange", "#FAD7A0"), ("zt_orange", "ZT orange", _eclaircir("#FAD7A0")),
            ("jaune", "Jaune", "#F9E79F"), ("zt_jaune", "ZT jaune", _eclaircir("#F9E79F")),
        ]
        for cle, niveau, couleur in paliers:
            seuil = seuils.get(cle)
            if seuil is not None and qmax >= seuil:
                return niveau, couleur
        return "Vert", "#D5F5E3"

    def _rafraichir():
        for w in cadre_vignettes.winfo_children():
            w.destroy()
        vignette_vars.clear()
        var_statut.set("")

        paths, manquants = construire_grp_paths(app)
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

        # Groupées par niveau de vigilance max atteint (Rouge -> ... -> Vert, demandé
        # explicitement), et au sein d'un même niveau triées par n° d'événement
        # croissant (#1 en premier) — pas par date ni par Qmax.
        evenements_avec_niveau = [(e, *_niveau_et_couleur_vigilance(e.qmax)) for e in evenements]
        evenements_avec_niveau.sort(
            key=lambda t: (_ORDRE_NIVEAUX_VIGILANCE.index(t[1]), t[0].num_evt))

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

        ligne, colonne = 0, 0
        niveau_precedent = None
        for evt, niveau, couleur in evenements_avec_niveau:
            # Changement de niveau de vigilance -> nouvelle ligne, même si la ligne
            # courante n'est pas pleine (demandé explicitement, pour que chaque ligne
            # de vignettes reste homogène en niveau de vigilance).
            if niveau_precedent is not None and niveau != niveau_precedent:
                ligne += 1
                colonne = 0
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
            # Pas de wraplength ici (contrairement à avant) : dQP/dTP/VE/KGE doivent
            # tenir sur UNE seule ligne (demandé — KGE passait à la ligne suivante et
            # faisait perdre de la place) ; la vignette est assez large (largeur fixe
            # ci-dessous) pour ça, et l'ascenseur horizontal prend le relai si la
            # fenêtre est trop étroite pour afficher les 4 colonnes en entier.
            tk.Label(vignette, bg=couleur, font=("TkDefaultFont", 8),
                     fg="#7B241C" if evt.suspects else "#333333", text=texte_perf,
                     justify=tk.LEFT).pack(anchor="w")

            niveau_precedent = niveau
            colonne += 1
            if colonne >= VIGNETTES_PAR_LIGNE:
                colonne = 0
                ligne += 1

        cadre_vignettes.update_idletasks()
        canvas_vignettes.itemconfig(fenetre_vignettes, width=max(
            cadre_vignettes.winfo_reqwidth(), canvas_vignettes.winfo_width()))
        canvas_vignettes.configure(
            height=cadre_vignettes.winfo_reqheight(),
            scrollregion=canvas_vignettes.bbox("all"),
        )
        var_statut.set(f"{len(evenements)} événement(s) trouvé(s).")

    def _rafraichir_combo_pdt():
        pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        combo_pdt["values"] = [p["libelle"] for p in pdt_list]
        if pdt_list and var_pdt_libelle.get() not in combo_pdt["values"]:
            libelle_init = libelle_dernier_pdt(app, pdt_list)
            if libelle_init:
                var_pdt_libelle.set(libelle_init)
        _rafraichir()

    def _on_pdt_change(*_evt):
        sauvegarder_dernier_pdt(app, _code_pdt_courant(), source=_pdt_change_externe)
        _rafraichir()

    def _pdt_change_externe(code_pdt):
        # Le pas de temps a été changé dans un AUTRE onglet (Dashboard ou Analyse
        # crues affl.) — aligne ce combo sans re-notifier (déjà fait par la source).
        pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        libelle = next((p["libelle"] for p in pdt_list if p["code"] == code_pdt), None)
        if libelle and var_pdt_libelle.get() != libelle:
            var_pdt_libelle.set(libelle)
            _rafraichir()

    combo_pdt.bind("<<ComboboxSelected>>", _on_pdt_change)
    enregistrer_observateur_pdt(app, _pdt_change_externe)
    _rafraichir_combo_pdt()

    # ── Bouton Enregistrer ───────────────────────────────────────────────────────
    bouton_enregistrer(frm, app).pack(fill=tk.X, padx=12, pady=14)
