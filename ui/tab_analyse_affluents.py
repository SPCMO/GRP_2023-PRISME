# -*- coding: utf-8 -*-
"""Onglet "Analyse crues affl." — pour chaque crue détectée à la station exutoire
(configurée en Configuration, ex. Moussoulens), superpose le Qobs de l'exutoire aux débits des stations affluentes
configurées par l'utilisateur (nom, surface de BV, temps de propagation P10/P50/P90,
fichier de débits), avec bilan volume/Qmax et bandes de propagation déduites des temps
saisis. Indépendant de tout calage GRP (contrairement à Dashboard > Détail par crue,
qui compare Qobs à une combinaison de calage) : ici on ne regarde que les observations.
"""

import math
import os
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import numpy as np
from matplotlib import dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from modules import affluents
from modules.criteres_perf import CriteresPerfError, parse_criteres_perf, parse_evenement_serie
from modules.grp_paths import construire_grp_paths
from modules.phyc_client import PhycAuthError, PhycClient
from modules.station_codes import CodeStationError, code_site_depuis_station
from ui.tab_config import LIBELLES_SEUILS_Q
from ui.widgets_common import (
    enregistrer_observateur_pdt, libelle_dernier_pdt, make_label, make_row,
    make_scrollable_tab, make_section, sauvegarder_dernier_pdt,
)

_COULEUR_OBS = "#1B4F72"
# Même palette que ui.tab_dashboard._PALETTE_COURBES, dupliquée ici pour la même
# raison que le reste du projet (voir modules/export_excel.py) : chaque couche évite
# de dépendre d'une constante privée d'un autre fichier de la même couche.
_PALETTE_AFFLUENTS = (
    "#CC5500", "#1D6A39", "#7B241C", "#7D3C98", "#117864", "#B7950B",
    "#2874A6", "#A93226", "#5D6D7E", "#943126",
)



def _nom_exutoire(app):
    """Nom de la station exutoire à afficher — lu depuis la config (identifiée via
    PHyC, onglet Configuration), repli générique si pas encore configuré. Évite de
    coder le nom de station en dur (l'outil se veut générique, voir main.py)."""
    nom = app.config_data.get("station", {}).get("nom_station", "").strip()
    return nom or "Station exutoire"


def _config_affluents(app):
    return app.config_data.setdefault("affluents", affluents.config_affluents_par_defaut())


def _liste_affluents(app):
    return [affluents.affluent_depuis_dict(d) for d in _config_affluents(app).get("liste", [])]


def _persister_affluents(app, liste_affluents):
    _config_affluents(app)["liste"] = [affluents.affluent_vers_dict(a) for a in liste_affluents]
    app.persist_config()


_COULEUR_LOCAL = "#BDC3C7"  # gris, réservé aux écoulements locaux non expliqués par un affluent suivi


_TEXTE_EXPLICATION_BARYCENTRE = (
    "Le barycentre de la pluie est l'instant moyen de l'épisode pluvieux, pondéré par "
    "la lame précipitée à chaque pas de temps (un pas de temps très pluvieux compte "
    "plus qu'un pas de temps à peine humide).\n\n"
    "Seules les pluies tombées AVANT l'horodatage du Qmax de la station exutoire sont "
    "prises en compte : une pluie tombée après le pic n'a physiquement pas pu "
    "contribuer à ce pic, l'inclure biaiserait le barycentre vers une date trop tardive."
)


def _barycentre_pluie(serie, date_limite=None):
    """`serie` : liste de (date, pobs, qobs) triée (voir modules.criteres_perf.
    parse_evenement_serie). Retourne l'horodatage barycentrique de la pluie —
    moyenne des horodatages pondérée par la lame Pobs de chaque pas de temps — ou
    None si la série ne contient aucune pluie (poids total nul) ou est vide.

    `date_limite` (typiquement l'horodatage du Qmax de l'exutoire, demandé
    explicitement) : si fourni, seules les pluies à cette date ou avant sont prises
    en compte — une pluie tombée APRÈS le pic n'a pas pu y contribuer."""
    points = [(d, p) for d, p, _q in serie
              if p is not None and p > 0 and (date_limite is None or d <= date_limite)]
    if not points:
        return None
    d0 = points[0][0]
    poids_total = sum(p for _d, p in points)
    if poids_total <= 0:
        return None
    t_bary_s = sum((d - d0).total_seconds() * p for d, p in points) / poids_total
    return d0 + timedelta(seconds=t_bary_s)


def _eclaircir(couleur_hex, facteur=0.55):
    """Éclaircit une couleur hex vers le blanc — simule un fond "semi-transparent"
    pour les lignes de Treeview colorées par série (Tkinter n'a pas d'alpha natif sur
    les couleurs de fond de widget, même principe que ui.tab_crues._eclaircir)."""
    couleur_hex = couleur_hex.lstrip("#")
    r, g, b = int(couleur_hex[0:2], 16), int(couleur_hex[2:4], 16), int(couleur_hex[4:6], 16)
    r = int(r + (255 - r) * facteur)
    g = int(g + (255 - g) * facteur)
    b = int(b + (255 - b) * facteur)
    return f"#{r:02X}{g:02X}{b:02X}"


def _prochaine_couleur(liste_affluents):
    """Première couleur de la palette pas encore utilisée par un autre affluent —
    évite deux affluents de la même couleur par défaut tant que la palette n'est pas
    épuisée (au-delà, elle boucle)."""
    couleurs_prises = {a.couleur for a in liste_affluents if a.couleur}
    for c in _PALETTE_AFFLUENTS:
        if c not in couleurs_prises:
            return c
    return _PALETTE_AFFLUENTS[len(liste_affluents) % len(_PALETTE_AFFLUENTS)]


def build_tab_analyse_affluents(tab_frame, app):
    frm = make_scrollable_tab(tab_frame)

    # ── Bandeau 1 — dossier d'import ────────────────────────────────────────────
    # Même couleur ("ocre") que le bandeau "Dossiers de travail" de l'onglet
    # Configuration — demandé explicitement, pour rester visuellement cohérent.
    inn1, bg1 = make_section(frm, "Dossier d'import des débits affluents", "ocre")
    r1 = make_row(inn1, bg1)
    make_label(r1, "Dossier :", bg1, width=30)
    var_dossier = tk.StringVar(value=_config_affluents(app).get("dossier_import", ""))
    ent_dossier = ttk.Entry(r1, textvariable=var_dossier, width=60)
    ent_dossier.pack(side=tk.LEFT, padx=(2, 4))

    def _valider_dossier(_evt=None):
        _config_affluents(app)["dossier_import"] = var_dossier.get().strip()
        app.persist_config()

    ent_dossier.bind("<FocusOut>", _valider_dossier)

    def _parcourir_dossier():
        dossier = filedialog.askdirectory(title="Dossier d'import des débits affluents")
        if dossier:
            var_dossier.set(dossier)
            _valider_dossier()

    ttk.Button(r1, text="Parcourir…", command=_parcourir_dossier).pack(side=tk.LEFT)

    # ── Bandeau 2 — gestion des stations affluentes ─────────────────────────────
    inn2, bg2 = make_section(
        frm, "Stations affluentes et temps de propagation à la station exutoire", "bleu")
    cadre_liste = tk.Frame(inn2, bg=bg2)
    cadre_liste.pack(fill=tk.X, expand=True)
    colonnes_affl = ("nom", "surface", "p10", "p50", "p90", "fichier")
    liste_affl = ttk.Treeview(cadre_liste, columns=colonnes_affl, show="headings", height=5)
    for col, libelle, largeur, ancre in (
        ("nom", "Nom de l'affluent", 160, "w"), ("surface", "Surface BV (km²)", 110, "center"),
        ("p10", "P10", 65, "center"), ("p50", "P50", 65, "center"), ("p90", "P90", 65, "center"),
        ("fichier", "Fichier de débits", 320, "w"),
    ):
        liste_affl.heading(col, text=libelle)
        liste_affl.column(col, width=largeur, anchor=ancre)
    ascenseur_affl = ttk.Scrollbar(cadre_liste, orient=tk.VERTICAL, command=liste_affl.yview)
    liste_affl.configure(yscrollcommand=ascenseur_affl.set)
    liste_affl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur_affl.pack(side=tk.RIGHT, fill=tk.Y)

    boutons_affl = tk.Frame(inn2, bg=bg2)
    boutons_affl.pack(fill=tk.X, pady=(4, 0))

    def _rafraichir_liste_affl(nom_a_selectionner=None):
        liste_affl.delete(*liste_affl.get_children())
        for a in _liste_affluents(app):
            tags = ()
            if a.couleur:
                # Fond éclairci ("semi-transparent") assorti à la couleur de la série,
                # texte en noir (demandé) pour rester lisible quelle que soit la teinte.
                liste_affl.tag_configure(a.couleur, background=_eclaircir(a.couleur),
                                           foreground="black")
                tags = (a.couleur,)
            iid = liste_affl.insert("", tk.END, values=(
                a.nom, f"{a.surface_bv_km2:.1f}" if a.surface_bv_km2 is not None else "—",
                affluents.minutes_vers_hhmm(a.p10_min) or "—",
                affluents.minutes_vers_hhmm(a.p50_min) or "—",
                affluents.minutes_vers_hhmm(a.p90_min) or "—",
                os.path.basename(a.fichier) if a.fichier else "—",
            ), tags=tags)
            if a.nom == nom_a_selectionner:
                liste_affl.selection_set(iid)

    def _affluent_selectionne():
        sel = liste_affl.selection()
        if not sel:
            return None
        index = liste_affl.index(sel[0])
        liste = _liste_affluents(app)
        return liste[index] if 0 <= index < len(liste) else None

    def _ouvrir_formulaire(affluent_existant):
        fen = tk.Toplevel(tab_frame)
        fen.title("Ajouter un affluent" if affluent_existant is None
                   else f"Modifier — {affluent_existant.nom}")
        fen.transient(tab_frame.winfo_toplevel())
        fen.grab_set()
        cadre = tk.Frame(fen, padx=14, pady=12)
        cadre.pack(fill=tk.BOTH, expand=True)

        def _ligne(texte, largeur_label=34):
            f = tk.Frame(cadre)
            f.pack(fill=tk.X, pady=3)
            tk.Label(f, text=texte, width=largeur_label, anchor="w").pack(side=tk.LEFT)
            return f

        f_code_station = _ligne("Code station (ex. Y161202001) :")
        var_code_station = tk.StringVar(
            value=affluent_existant.code_station if affluent_existant
            and affluent_existant.code_station else "")
        ttk.Entry(f_code_station, textvariable=var_code_station, width=16).pack(
            side=tk.LEFT, padx=(0, 4))

        var_erreur_phyc = tk.StringVar(value="")
        var_nom = tk.StringVar(value=affluent_existant.nom if affluent_existant else "")

        def _recuperer_infos_phyc():
            try:
                code_site = code_site_depuis_station(var_code_station.get())
            except CodeStationError as e:
                var_erreur_phyc.set(str(e))
                return
            code_station = var_code_station.get().strip().upper()

            phyc_cfg = app.config_data.get("phyc", {})
            idcontact = phyc_cfg.get("idcontact", "").strip()
            motdepasse = phyc_cfg.get("motdepasse", "").strip()
            if not idcontact or not motdepasse:
                idcontact = simpledialog.askstring(
                    "Identifiants PHyC", "Identifiant PHyC (idcontact) :",
                    initialvalue=idcontact, parent=fen)
                if idcontact is None:
                    return
                motdepasse = simpledialog.askstring(
                    "Identifiants PHyC", "Mot de passe PHyC :", show="*", parent=fen)
                if motdepasse is None:
                    return
                app.config_data.setdefault("phyc", {})["idcontact"] = idcontact.strip()
                app.config_data["phyc"]["motdepasse"] = motdepasse.strip()
                app.persist_config()

            var_erreur_phyc.set("Connexion à PHyC en cours…")
            btn_phyc.config(state="disabled")
            fen.update_idletasks()

            client = PhycClient(wsdl_url=phyc_cfg.get(
                "url", "http://services.schapi.e2.rie.gouv.fr/phycop/bdtrv21.wsdl"))
            try:
                client.login(idcontact, motdepasse)
                infos_site = client.get_infos_site(code_site)
            except PhycAuthError as e:
                var_erreur_phyc.set(f"Échec d'authentification PHyC : {e}")
                return
            except Exception as e:
                var_erreur_phyc.set(
                    f"Échec de la récupération des informations pour le code site "
                    f"{code_site!r} (dérivé du code station {code_station!r}) : {e}")
                return
            finally:
                client.logout()
                btn_phyc.config(state="normal")

            if infos_site.libelle_usuel_site is None and infos_site.surface_bv_km2 is None:
                var_erreur_phyc.set(
                    f"PHyC n'a retourné ni nom ni surface de BV pour le code site "
                    f"{code_site!r} — vérifiez que le code station est correct.")
                return

            var_code_station.set(code_station)
            if infos_site.libelle_usuel_site:
                var_nom.set(infos_site.libelle_usuel_site)
            if infos_site.surface_bv_km2 is not None:
                var_surface.set(f"{infos_site.surface_bv_km2:g}")
            message = "Infos PHyC récupérées (nom et surface restent modifiables)."
            if infos_site.surface_est_approximative:
                message += (" Surface approximative (BassinVersantSiteHydro absent de "
                             "PHyC pour ce site — repli sur la surface de la BNBV, "
                             "SurfBNBV, un périmètre légèrement différent).")
            var_erreur_phyc.set(message)

        btn_phyc = ttk.Button(f_code_station, text="Récupérer infos PHyC",
                               command=_recuperer_infos_phyc)
        btn_phyc.pack(side=tk.LEFT)

        tk.Label(cadre, textvariable=var_erreur_phyc, fg="#6b7280", wraplength=420,
                 justify=tk.LEFT, font=("TkDefaultFont", 8, "italic")).pack(
            anchor="w", pady=(0, 4))

        f_nom = _ligne("Nom de l'affluent :")
        ttk.Entry(f_nom, textvariable=var_nom, width=30).pack(side=tk.LEFT)

        f_surface = _ligne("Surface de BV (km²) :")
        var_surface = tk.StringVar(
            value=f"{affluent_existant.surface_bv_km2:g}"
            if affluent_existant and affluent_existant.surface_bv_km2 is not None else "")
        ttk.Entry(f_surface, textvariable=var_surface, width=12).pack(side=tk.LEFT)

        tk.Label(cadre, text=f"Temps de propagation jusqu'à l'exutoire ({_nom_exutoire(app)}), "
                              "format hh:mm :", font=("TkDefaultFont", 9, "italic")).pack(
            anchor="w", pady=(10, 2))

        f_p10 = _ligne("P10 (facultatif) :")
        var_p10 = tk.StringVar(value=affluents.minutes_vers_hhmm(
            affluent_existant.p10_min) if affluent_existant else "")
        ttk.Entry(f_p10, textvariable=var_p10, width=10).pack(side=tk.LEFT)

        # P50 en gras et plus grand (demandé) — seul temps de propagation réellement
        # obligatoire, les autres n'étant qu'une fourchette d'incertitude autour de lui.
        f_p50 = _ligne("P50 :")
        var_p50 = tk.StringVar(value=affluents.minutes_vers_hhmm(
            affluent_existant.p50_min) if affluent_existant else "")
        ttk.Entry(f_p50, textvariable=var_p50, width=10,
                  font=("TkDefaultFont", 12, "bold")).pack(side=tk.LEFT)

        f_p90 = _ligne("P90 (facultatif) :")
        var_p90 = tk.StringVar(value=affluents.minutes_vers_hhmm(
            affluent_existant.p90_min) if affluent_existant else "")
        ttk.Entry(f_p90, textvariable=var_p90, width=10).pack(side=tk.LEFT)

        f_couleur = _ligne("Couleur de la courbe :")
        var_couleur = tk.StringVar(
            value=(affluent_existant.couleur if affluent_existant and affluent_existant.couleur
                   else _prochaine_couleur(_liste_affluents(app))))

        def _maj_apercu_couleur():
            btn_couleur.configure(bg=var_couleur.get(), activebackground=var_couleur.get())

        def _choisir_couleur():
            _, hex_choisi = colorchooser.askcolor(
                color=var_couleur.get(), title="Couleur de la courbe", parent=fen)
            if hex_choisi:
                var_couleur.set(hex_choisi)
                _maj_apercu_couleur()

        btn_couleur = tk.Button(f_couleur, text="Choisir…", command=_choisir_couleur,
                                  bg=var_couleur.get(), activebackground=var_couleur.get(),
                                  fg="white", relief=tk.RAISED)
        btn_couleur.pack(side=tk.LEFT)

        f_fichier = _ligne("Fichier de débits :")
        var_fichier = tk.StringVar(value=affluent_existant.fichier if affluent_existant else "")
        ttk.Entry(f_fichier, textvariable=var_fichier, width=38).pack(side=tk.LEFT, padx=(0, 4))

        def _parcourir_fichier():
            chemin = filedialog.askopenfilename(
                title="Fichier de débits de l'affluent",
                initialdir=(var_dossier.get().strip() or None),
                filetypes=[("CSV", "*.csv"), ("Tous les fichiers", "*.*")], parent=fen)
            if chemin:
                var_fichier.set(chemin)

        ttk.Button(f_fichier, text="Parcourir…", command=_parcourir_fichier).pack(side=tk.LEFT)

        var_erreur = tk.StringVar(value="")
        tk.Label(cadre, textvariable=var_erreur, fg="#A93226", wraplength=420,
                 justify=tk.LEFT).pack(anchor="w", pady=(8, 0))

        def _valider():
            nom = var_nom.get().strip()
            if not nom:
                var_erreur.set("Le nom de l'affluent est obligatoire.")
                return
            liste_actuelle = _liste_affluents(app)
            noms_autres = {a.nom for a in liste_actuelle
                            if affluent_existant is None or a.nom != affluent_existant.nom}
            if nom in noms_autres:
                var_erreur.set(f"Un affluent nommé \"{nom}\" existe déjà.")
                return
            try:
                surface = (float(var_surface.get().replace(",", "."))
                           if var_surface.get().strip() else None)
            except ValueError:
                var_erreur.set("Surface de BV invalide (nombre attendu).")
                return
            try:
                p10 = affluents.hhmm_vers_minutes(var_p10.get())
                p50 = affluents.hhmm_vers_minutes(var_p50.get())
                p90 = affluents.hhmm_vers_minutes(var_p90.get())
            except ValueError as e:
                var_erreur.set(str(e))
                return
            if p50 is None:
                var_erreur.set("Le temps de propagation P50 est obligatoire.")
                return

            fichier = var_fichier.get().strip() or None
            couleur = var_couleur.get()
            code_station = var_code_station.get().strip().upper() or None
            if affluent_existant is None:
                liste_actuelle.append(affluents.Affluent(
                    nom=nom, code_station=code_station, surface_bv_km2=surface,
                    p10_min=p10, p50_min=p50, p90_min=p90,
                    fichier=fichier, couleur=couleur))
            else:
                for a in liste_actuelle:
                    if a.nom == affluent_existant.nom:
                        a.nom, a.code_station, a.surface_bv_km2 = nom, code_station, surface
                        a.p10_min, a.p50_min, a.p90_min = p10, p50, p90
                        a.fichier, a.couleur = fichier, couleur
                        break

            _persister_affluents(app, liste_actuelle)
            _rafraichir_liste_affl(nom_a_selectionner=nom)
            _rafraichir_graphique()
            fen.destroy()

        cadre_boutons = tk.Frame(cadre)
        cadre_boutons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(cadre_boutons, text="Enregistrer", command=_valider).pack(side=tk.LEFT)
        ttk.Button(cadre_boutons, text="Annuler", command=fen.destroy).pack(side=tk.LEFT, padx=(6, 0))

    def _ajouter():
        _ouvrir_formulaire(None)

    def _modifier():
        a = _affluent_selectionne()
        if a is None:
            messagebox.showinfo("Modifier", "Sélectionnez un affluent dans la liste.")
            return
        _ouvrir_formulaire(a)

    def _supprimer():
        a = _affluent_selectionne()
        if a is None:
            messagebox.showinfo("Supprimer", "Sélectionnez un affluent dans la liste.")
            return
        if not messagebox.askyesno("Supprimer", f"Supprimer l'affluent \"{a.nom}\" ?"):
            return
        _persister_affluents(app, [x for x in _liste_affluents(app) if x.nom != a.nom])
        _rafraichir_liste_affl()
        _rafraichir_graphique()

    ttk.Button(boutons_affl, text="Ajouter…", command=_ajouter).pack(side=tk.LEFT)
    ttk.Button(boutons_affl, text="Modifier…", command=_modifier).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Button(boutons_affl, text="Supprimer", command=_supprimer).pack(side=tk.LEFT, padx=(6, 0))

    # ── Sélection de la crue affichée ────────────────────────────────────────────
    barre_sel, bg_sel = make_section(frm, "Crue affichée", "gris")
    r_sel = make_row(barre_sel, bg_sel)
    make_label(r_sel, "Pas de temps :", bg_sel, width=14)
    var_pdt = tk.StringVar()
    combo_pdt = ttk.Combobox(r_sel, textvariable=var_pdt, state="readonly", width=14)
    combo_pdt.pack(side=tk.LEFT, padx=(2, 12))

    make_label(r_sel, "Crue :", bg_sel, width=8)
    var_crue = tk.StringVar()
    combo_crue = ttk.Combobox(r_sel, textvariable=var_crue, state="readonly", width=22)
    combo_crue.pack(side=tk.LEFT, padx=(2, 2))
    ttk.Button(r_sel, text="◀", width=3, command=lambda: _changer_crue(-1)).pack(side=tk.LEFT)
    ttk.Button(r_sel, text="▶", width=3, command=lambda: _changer_crue(1)).pack(
        side=tk.LEFT, padx=(0, 12))

    var_vigilance = tk.BooleanVar(value=True)
    ttk.Checkbutton(r_sel, text="Afficher les seuils de vigilance", variable=var_vigilance,
                     command=lambda: _rafraichir_graphique()).pack(side=tk.LEFT, padx=(12, 0))

    var_statut = tk.StringVar(value="")
    tk.Label(frm, textvariable=var_statut, fg="#555555", wraplength=1000, justify=tk.LEFT).pack(
        anchor="w", padx=10, pady=(2, 0))

    # ── Graphique + panneau Qmax (à droite, sous la légende — demandé pour gagner de
    # la place plutôt qu'une bande de vignettes pleine largeur sous le graphique) ────
    cadre_graphique = tk.Frame(frm)
    cadre_graphique.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

    fig = Figure(figsize=(15, 5.5), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    # Hyétogramme (pluie de bassin observée à l'exutoire) en axe jumeau, inversé,
    # cantonné au quart supérieur — même convention que Dashboard > Détail par crue
    # (ui.tab_dashboard._build_detail). Créé UNE FOIS ici (comme là-bas) ; nettoyé et
    # repositionné à chaque tracé (voir _rafraichir_graphique).
    ax_pluie = ax.twinx()
    ax_pluie.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    canvas = FigureCanvasTkAgg(fig, master=cadre_graphique)
    canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    # Camembert de contribution au pic exutoire, recréé à chaque tracé (sa position/
    # taille dépend de la légende, elle-même variable avec le nombre d'affluents) —
    # voir _rafraichir_graphique. Icône "i" à côté de l'étiquette du barycentre pluie
    # (fig.text + pick_event, même principe que ui.tab_dashboard._icone_info_axe) :
    # supprimée/reconnectée à chaque tracé, sa position dépend de la date affichée.
    etat_graphique = {"ax_pie": None}
    etat_icone_bary = {"artist": None, "cid": None}
    # Textes P10/P50/P90 (voir _rafraichir_graphique) : fig.text(), pas ax.text(),
    # donc PAS supprimés par ax.clear() — à retirer manuellement à chaque tracé,
    # même principe que l'icône du barycentre ci-dessus.
    etat_percentiles_tr = {"artists": []}

    cadre_vignettes = tk.Frame(cadre_graphique, width=190)
    cadre_vignettes.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 0))
    cadre_vignettes.pack_propagate(False)
    # La légende matplotlib est ancrée en haut du même axe (bbox_to_anchor y=0.98, voir
    # plus bas) — l'espace au-dessus de ce label est recalé à chaque tracé sur la
    # hauteur RÉELLE de la légende (voir _rafraichir_graphique), pour ne pas gâcher de
    # place avec une marge fixe généreuse (l'ancienne valeur fixe, 58 px, laissait
    # trop de vide dès qu'il y avait peu d'affluents, et coupait le camembert de
    # surfaces en bas du panneau — signalé par l'utilisateur).
    label_titre_vignettes = tk.Label(
        cadre_vignettes, text="Qmax observé par courbe :",
        font=("TkDefaultFont", 8, "italic"), fg="#555555")
    label_titre_vignettes.pack(anchor="w", pady=(2, 2))

    def _vider_vignettes():
        for w in cadre_vignettes.winfo_children()[1:]:  # [0] = le libellé ci-dessus, conservé
            w.destroy()

    def _ajouter_vignette(nom, couleur, qmax, date_qmax, surface=None):
        v = tk.Frame(cadre_vignettes, bg=couleur, relief=tk.RIDGE, borderwidth=2)
        v.pack(fill=tk.X, pady=3)
        tk.Label(v, text=nom, bg=couleur, fg="white", font=("TkDefaultFont", 8, "bold"),
                  wraplength=175, justify=tk.LEFT).pack(anchor="w", padx=5, pady=(3, 0))
        if surface is not None:
            tk.Label(v, text=f"BV {surface:.0f} km²", bg=couleur, fg="white",
                     font=("TkDefaultFont", 8)).pack(anchor="w", padx=5)
        tk.Label(v, text=f"Qmax {qmax:.1f} m³/s" if qmax is not None else "Qmax indisponible",
                 bg=couleur, fg="white", font=("TkDefaultFont", 8)).pack(anchor="w", padx=5)
        if date_qmax is not None:
            tk.Label(v, text=f"{date_qmax:%d/%m/%Y %H:%M}", bg=couleur, fg="white",
                      font=("TkDefaultFont", 8)).pack(anchor="w", padx=5, pady=(0, 3))

    def _dessiner_camembert_surfaces(surface_exutoire, items):
        """items : liste de (nom, couleur, surface_km2) des affluents tracés. Camembert
        Tk (le panneau vignettes est en Tk pur, pas matplotlib) du prorata de surface de
        BV suivie par un affluent par rapport à la surface totale du BV exutoire — même
        principe que le camembert matplotlib de contribution au pic (écrêtage au prorata
        si le total dépasse 100 %, part grise _COULEUR_LOCAL pour le reste non suivi),
        mais en surface plutôt qu'en débit. Placé sous les vignettes Qmax (demandé),
        largeur bornée par celle du panneau (cadre_vignettes, 190 px)."""
        if surface_exutoire is None or surface_exutoire <= 0:
            tk.Label(cadre_vignettes, text="Surface de BV suivie :",
                      font=("TkDefaultFont", 8, "italic"), fg="#555555").pack(
                anchor="w", pady=(4, 2))
            tk.Label(cadre_vignettes, text="Surface exutoire inconnue (Configuration).",
                      font=("TkDefaultFont", 7), fg="#888888", wraplength=175,
                      justify=tk.LEFT).pack(anchor="w")
            return
        # Total (donc le %age de titre) calculé AVANT écrêtage éventuel des parts —
        # le titre reflète ainsi la vraie somme des % de BV affluents renseignés
        # (demandé), même dans le cas rare où elle dépasse 100 % (surfaces qui se
        # chevauchent, écrêtées ensuite pour le dessin du camembert lui-même).
        parts_brutes = [(n, c, s) for n, c, s in items if s]
        pct_titre = sum(s for _n, _c, s in parts_brutes) / surface_exutoire * 100
        tk.Label(cadre_vignettes, text=f"Surface de BV suivie : {pct_titre:.0f} %",
                  font=("TkDefaultFont", 8, "italic"), fg="#555555").pack(
            anchor="w", pady=(4, 2))
        diametre, marge = 100, 4
        taille = diametre + marge * 2
        canvas = tk.Canvas(cadre_vignettes, width=taille, height=taille,
                            highlightthickness=0, bg=cadre_vignettes.cget("bg"))
        canvas.pack(pady=(2, 2))
        parts = list(parts_brutes)
        total_suivi = sum(s for _n, _c, s in parts)
        if total_suivi > surface_exutoire:
            facteur = surface_exutoire / total_suivi
            parts = [(n, c, s * facteur) for n, c, s in parts]
            total_suivi = surface_exutoire
        reste = surface_exutoire - total_suivi
        if reste > 0.5:
            parts = parts + [("Écoulements locaux", _COULEUR_LOCAL, reste)]
        cx = cy = taille / 2
        rayon_texte = diametre / 2 * 0.6
        bbox = (marge, marge, marge + diametre, marge + diametre)
        angle = 90.0
        for _nom, couleur, surface in parts:
            extent = -360.0 * (surface / surface_exutoire)
            # Couleur éclaircie ("semi-transparente", demandé — Tkinter n'a pas
            # d'alpha natif sur un fond de canvas, même principe que _eclaircir()
            # déjà utilisé pour les listes) — texte en noir en conséquence, plus
            # lisible qu'en blanc sur un fond éclairci.
            canvas.create_arc(bbox, start=angle, extent=extent, fill=_eclaircir(couleur),
                                outline="white", width=1)
            pct = surface / surface_exutoire * 100
            if pct >= 5:
                angle_median = math.radians(angle + extent / 2)
                canvas.create_text(
                    cx + rayon_texte * math.cos(angle_median),
                    cy - rayon_texte * math.sin(angle_median),
                    text=f"{pct:.0f}%", font=("TkDefaultFont", 6, "bold"), fill="black")
            angle += extent
        pct_suivi = total_suivi / surface_exutoire * 100
        tk.Label(cadre_vignettes,
                  text=f"{pct_suivi:.0f} % suivi ({total_suivi:.0f} / {surface_exutoire:.0f} km²)",
                  font=("TkDefaultFont", 7), fg="#555555", wraplength=175,
                  justify=tk.LEFT).pack(anchor="w", pady=(0, 4))

    # Association handle de légende (proxy Line2D) -> Affluent, pour permettre de
    # changer la couleur d'une courbe affluente en cliquant directement dans la
    # légende (demandé — alternative au bouton "Couleur" du tableau de gestion).
    etat_legende = {"mapping": {}}

    # Survol à la souris pour lire une valeur de débit directement sur la courbe
    # (comme OPALE v2, demandé) — même principe que Dashboard > Détail par crue
    # (ui.tab_dashboard._build_detail._survol_courbes) : recherche du point le plus
    # proche du curseur EN PIXELS parmi toutes les courbes tracées, pas juste la plus
    # proche en X, pour rester correct quand plusieurs courbes se croisent.
    etat_courbes = {"liste": []}
    etat_survol = {"artist": None}

    def _survol_courbes(event):
        artist = etat_survol.get("artist")
        courbes = etat_courbes["liste"]
        if artist is None:
            return
        if event.inaxes is not ax or event.xdata is None or not courbes:
            if artist.get_visible():
                artist.set_visible(False)
                canvas.draw_idle()
            return
        SEUIL_PX = 20
        meilleure, meilleure_dist = None, None
        for c in courbes:
            xs = c["x"]
            if not xs:
                continue
            idx = min(range(len(xs)), key=lambda i: abs(xs[i] - event.xdata))
            yi = c["y"][idx]
            if yi is None:
                continue
            xpix, ypix = ax.transData.transform((xs[idx], yi))
            dist = ((xpix - event.x) ** 2 + (ypix - event.y) ** 2) ** 0.5
            if meilleure_dist is None or dist < meilleure_dist:
                meilleure_dist, meilleure = dist, (c, xs[idx], yi)
        if meilleure is None or meilleure_dist > SEUIL_PX:
            if artist.get_visible():
                artist.set_visible(False)
                canvas.draw_idle()
            return
        c, xi, yi = meilleure
        date_i = mdates.num2date(xi).replace(tzinfo=None)
        artist.set_text(f"{c['label']}\n{date_i:%d/%m %H:%M} — {yi:.1f} m³/s")
        artist.xy = (xi, yi)
        artist.get_bbox_patch().set_edgecolor(c["couleur"])
        artist.set_visible(True)
        canvas.draw_idle()

    canvas.mpl_connect("motion_notify_event", _survol_courbes)

    def _clic_legende(event):
        aff = etat_legende["mapping"].get(id(event.artist))
        if aff is None:
            return
        _, hex_choisi = colorchooser.askcolor(
            color=aff.couleur or "#7D3C98", title=f"Couleur — {aff.nom}",
            parent=tab_frame.winfo_toplevel())
        if not hex_choisi:
            return
        # `aff` vient de _liste_affluents(app), qui reconstruit des objets Affluent à
        # chaque appel (pas des références vers la config persistée) — le retrouver
        # PAR NOM dans une liste fraîchement relue avant de le modifier, plutôt que de
        # muter `aff` directement (perdu au prochain _liste_affluents(app), qui
        # reconstruirait un objet identique à l'ANCIENNE couleur).
        liste_actuelle = _liste_affluents(app)
        for a in liste_actuelle:
            if a.nom == aff.nom:
                a.couleur = hex_choisi
                break
        _persister_affluents(app, liste_actuelle)
        _rafraichir_liste_affl(nom_a_selectionner=aff.nom)
        _rafraichir_graphique()

    canvas.mpl_connect("pick_event", _clic_legende)

    # ── Tableau bilan volume/Q — exutoire vs affluents ──────────────────────────
    inn_tab, bg_tab = make_section(
        frm, "Volumes transités et Q - exutoire et affluents", "gris")
    cadre_tab = tk.Frame(inn_tab, bg=bg_tab)
    cadre_tab.pack(fill=tk.BOTH, expand=True)
    tableau_bilan = ttk.Treeview(
        cadre_tab, columns=("station", "volume", "pct_volume", "qmax", "pct_qmax"),
        show="headings", height=6)
    for col, libelle, largeur in (
        ("station", "Station", 200), ("volume", "Volume transité (hm³)", 170),
        ("pct_volume", "% du volume exutoire", 150),
        ("qmax", "Q à Qmax exutoire (m³/s)  ⓘ", 190), ("pct_qmax", "% du Qmax exutoire", 150),
    ):
        tableau_bilan.heading(col, text=libelle)
        tableau_bilan.column(col, width=largeur, anchor="center" if col != "station" else "w")
    # Clic sur l'en-tête de la colonne "Q à Qmax exutoire" pour expliquer le principe
    # (demandé) — ttk.Treeview ne permet pas d'insérer une icône DANS l'en-tête, le
    # "ⓘ" fait donc partie du texte du libellé, tout l'en-tête déclenche l'explication.
    tableau_bilan.heading("qmax", command=lambda: messagebox.showinfo(
        "Q à Qmax exutoire — principe",
        "Ce n'est PAS le Qmax propre de chaque affluent, mais son débit RÉTROPROPAGÉ "
        "au pic de l'exutoire :\n\n"
        f"1. On part de l'horodatage du Qmax observé à la station exutoire "
        f"({_nom_exutoire(app)}).\n"
        "2. Pour chaque affluent, on recule de la médiane (P50) de son temps de "
        "propagation jusqu'à l'exutoire.\n"
        "3. On lit le débit de l'affluent à cet instant reculé.\n\n"
        "Résultat : la contribution de chaque affluent AU MOMENT PRÉCIS du pic de "
        "l'exutoire, tous alignés sur un même instant de référence — plutôt que des "
        "Qmax propres à chaque affluent, qui ne se produisent pas forcément en même "
        "temps. La colonne suivante (% du Qmax exutoire) exprime cette contribution "
        "en pourcentage du débit de pointe de l'exutoire.",
        parent=tab_frame.winfo_toplevel()))
    tableau_bilan.pack(fill=tk.BOTH, expand=True)

    # ── Logique de sélection / tracé ─────────────────────────────────────────────

    def _pas_de_temps_courant():
        for p in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if p.get("libelle") == var_pdt.get():
                return p.get("code")
        return None

    def _crue_iso_courante():
        libelles = list(combo_crue["values"])
        valeurs = getattr(combo_crue, "_valeurs", [])
        if var_crue.get() not in libelles or len(valeurs) != len(libelles):
            return None
        return valeurs[libelles.index(var_crue.get())][1]

    def _tr_crues_selectionnees(paths, code_pdt, evenements):
        """Liste des temps de réponse (Tr, en minutes) calculés sur les crues
        actuellement sélectionnées pour la campagne (app.config_data
        ["crues_selectionnees"], même liste que l'onglet Crues) — un Tr par crue
        sélectionnée pour laquelle le calcul est possible (Qmax exutoire ET
        barycentre pluie connus, même méthode que pour la crue affichée)."""
        isos_selectionnes = set(app.config_data.get("crues_selectionnees", []))
        if not isos_selectionnes:
            return []
        trs = []
        for e in evenements:
            if e.date_deb.isoformat() not in isos_selectionnes:
                continue
            chemin = os.path.join(paths.evenements_dir(code_pdt),
                                   f"{paths.code_site}-EV{e.num_evt:04d}.DAT")
            try:
                serie_e = parse_evenement_serie(chemin)
            except (FileNotFoundError, CriteresPerfError):
                continue
            points_q = [(p[0], p[2]) for p in serie_e]
            _qmax_e, date_qmax_e = affluents.qmax_et_horodatage(points_q)
            if date_qmax_e is None:
                continue
            date_bary_e = _barycentre_pluie(serie_e, date_limite=date_qmax_e)
            if date_bary_e is None:
                continue
            trs.append((date_qmax_e - date_bary_e).total_seconds() / 60)
        return trs

    def _rafraichir_crues(*_evt):
        paths, _manquants = construire_grp_paths(app)
        code_pdt = _pas_de_temps_courant()
        entrees = []
        if paths is not None and code_pdt:
            try:
                evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
                entrees = [(e.num_evt, e.date_deb.isoformat())
                           for e in evenements if e.typ_evt == "Q"]
            except (FileNotFoundError, CriteresPerfError):
                entrees = []
        entrees.sort(key=lambda t: (t[0] is None, t[0]))
        libelles = [f"#{n} - {datetime.fromisoformat(iso):%d/%m/%Y}" for n, iso in entrees]
        combo_crue["values"] = libelles
        combo_crue._valeurs = entrees
        if libelles and var_crue.get() not in libelles:
            var_crue.set(libelles[0])
        elif not libelles:
            var_crue.set("")
        _rafraichir_graphique()

    def _changer_crue(delta):
        dates = list(combo_crue["values"])
        if not dates or var_crue.get() not in dates:
            return
        nouvel_index = list(dates).index(var_crue.get()) + delta
        if not (0 <= nouvel_index < len(dates)):
            return
        var_crue.set(dates[nouvel_index])
        _rafraichir_graphique()

    def _rafraichir_graphique():
        ax.clear()
        ax_pluie.clear()
        # ax.clear() recrée le patch de fond (le rend visible par défaut) : à refaire à
        # chaque tracé, pas seulement à la création, sinon les barres de pluie
        # repassent au-dessus des courbes de débit dès le 2e tracé (voir
        # ui.tab_dashboard._build_detail, même remarque).
        ax.patch.set_visible(False)
        if etat_graphique["ax_pie"] is not None:
            try:
                fig.delaxes(etat_graphique["ax_pie"])
            except Exception:
                pass
            etat_graphique["ax_pie"] = None
        if etat_icone_bary["artist"] is not None:
            try:
                etat_icone_bary["artist"].remove()
            except Exception:
                pass
            canvas.mpl_disconnect(etat_icone_bary["cid"])
            etat_icone_bary["artist"] = None
        for artiste in etat_percentiles_tr["artists"]:
            try:
                artiste.remove()
            except Exception:
                pass
        etat_percentiles_tr["artists"] = []
        etat_courbes["liste"] = []
        etat_survol["artist"] = ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points", fontsize=7.5,
            zorder=25, visible=False,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#555555", linewidth=1.0, alpha=0.92),
        )
        _vider_vignettes()
        tableau_bilan.delete(*tableau_bilan.get_children())
        etat_legende["mapping"] = {}

        paths, _manquants = construire_grp_paths(app)
        code_pdt = _pas_de_temps_courant()
        crue_iso = _crue_iso_courante()
        if paths is None or not code_pdt or not crue_iso:
            var_statut.set("Sélectionnez un pas de temps et une crue (nécessite la Configuration "
                            "et la détection des crues — voir onglet Crues).")
            canvas.draw_idle()
            return

        try:
            evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
        except (FileNotFoundError, CriteresPerfError) as e:
            var_statut.set(f"Impossible de charger les événements : {e}")
            canvas.draw_idle()
            return
        evt = next((e for e in evenements if e.date_deb.isoformat() == crue_iso), None)
        if evt is None:
            var_statut.set("Crue introuvable dans CRITERES_PERF.DAT pour ce pas de temps.")
            canvas.draw_idle()
            return

        chemin_serie = os.path.join(paths.evenements_dir(code_pdt),
                                     f"{paths.code_site}-EV{evt.num_evt:04d}.DAT")
        try:
            serie_exutoire = parse_evenement_serie(chemin_serie)
        except (FileNotFoundError, CriteresPerfError) as e:
            var_statut.set(f"Série observée de l'exutoire indisponible : {e}")
            serie_exutoire = []

        lignes_bilan = []
        qmax_exutoire, volume_exutoire, date_qmax_exutoire = None, None, None
        y_max_visible = None
        nom_exutoire = _nom_exutoire(app)
        label_exutoire = f"Q observé — {nom_exutoire} (exutoire)"
        if serie_exutoire:
            points_exutoire = [(p[0], p[2]) for p in serie_exutoire]
            ax.plot([d for d, _v in points_exutoire], [v for _d, v in points_exutoire],
                     color=_COULEUR_OBS, lw=1.8, label=label_exutoire)
            etat_courbes["liste"].append({
                "label": label_exutoire, "couleur": _COULEUR_OBS,
                "x": [mdates.date2num(d) for d, _v in points_exutoire],
                "y": [v for _d, v in points_exutoire],
            })
            qmax_exutoire, date_qmax_exutoire = affluents.qmax_et_horodatage(points_exutoire)
            volume_exutoire = affluents.volume_m3(points_exutoire)
            if qmax_exutoire is not None:
                # Point coloré sur le pic de chaque courbe (demandé), couleur propre
                # à la série — même principe pour les affluents plus bas.
                ax.plot([date_qmax_exutoire], [qmax_exutoire], marker="o", markersize=7,
                         color=_COULEUR_OBS, markeredgecolor="white", markeredgewidth=0.8,
                         zorder=10)
            _ajouter_vignette(
                f"{nom_exutoire} (exutoire)", _COULEUR_OBS, qmax_exutoire, date_qmax_exutoire,
                surface=app.config_data.get("station", {}).get("surface_bv_km2"))
            # Au moment de son propre Qmax, l'exutoire "contribue" à 100 % de son
            # propre débit — sert de référence aux % de contribution des affluents
            # ci-dessous (mêmes colonnes, même instant de référence).
            lignes_bilan.append((f"{nom_exutoire} (exutoire)", volume_exutoire, None,
                                   qmax_exutoire, 100.0 if qmax_exutoire is not None else None,
                                   _COULEUR_OBS))

            # Échelle Y calée sur Qmax exutoire (+15 %, un peu de visibilité au-dessus
            # du pic), PAS sur les valeurs des seuils de vigilance — demandé, pour ne
            # plus que l'axe s'étire jusqu'au seuil "Rouge" même quand la crue reste
            # bien en dessous. set_ylim() explicite ICI, avant le tracé des seuils plus
            # bas : matplotlib ne réétire alors plus l'axe pour les faire rentrer.
            if qmax_exutoire is not None and qmax_exutoire > 0:
                y_max_visible = qmax_exutoire * 1.15
                ax.set_ylim(0, y_max_visible)

            # -- Hyétogramme (pluie de bassin observée à l'exutoire) -------------------
            # Même source/convention que Dashboard > Détail par crue : Pobs déjà en mm
            # par pas de temps dans <code_site>-EVxxxx.DAT, utilisée SANS conversion
            # (voir modules.criteres_perf.parse_evenement_serie). Aucune pluie propre
            # aux affluents dans les fichiers de débits (colonnes "date;res" seules).
            if len(serie_exutoire) >= 2:
                intervalle_minutes = (serie_exutoire[1][0] - serie_exutoire[0][0]).total_seconds() / 60
                if intervalle_minutes > 0:
                    dates_pluie = [p[0] for p in serie_exutoire]
                    profondeurs = [p[1] for p in serie_exutoire]
                    largeur_jours = (intervalle_minutes / (24 * 60)) * 0.8
                    ax_pluie.bar(dates_pluie, profondeurs, width=largeur_jours,
                                  color="#5DADE2", edgecolor="#2E86AB", linewidth=0.3,
                                  alpha=0.75, zorder=1, label="Pluie de bassin (exutoire)")
                    plafond = max(max(profondeurs, default=0) * 4, 1)
                    ax_pluie.set_ylim(plafond, 0)  # inversé : la pluie "tombe" depuis le haut
                    # ax_pluie.clear() (ci-dessus) réinitialise la position du label à
                    # "left" à CHAQUE rafraîchissement malgré twinx() — à rappeler
                    # explicitement (même bug que ui.tab_dashboard._build_detail et
                    # ui.tab_dashboard._build_variation_crues, déjà rencontré et corrigé).
                    ax_pluie.yaxis.set_label_position("right")
                    ax_pluie.set_ylabel("Pluie (mm / pas de temps)", fontsize=7.5,
                                         color="#2E86AB", labelpad=14)
                    ax_pluie.tick_params(axis="y", labelsize=7, colors="#2E86AB")

        liste_affl = _liste_affluents(app)
        affluents_traces = []
        contributions_pie = []  # (nom, couleur, % du Qmax exutoire) — alimente le camembert
        for a in liste_affl:
            if not a.fichier:
                continue
            try:
                serie_a = affluents.charger_serie_affluent(a.fichier, evt.date_deb, evt.date_fin)
            except (FileNotFoundError, ValueError) as e:
                var_statut.set(f"{a.nom} : {e}")
                continue
            if not serie_a:
                continue
            couleur = a.couleur or "#7D3C98"
            ax.plot([d for d, _v in serie_a], [v for _d, v in serie_a],
                     color=couleur, lw=1.4, ls="--", label=a.nom)
            etat_courbes["liste"].append({
                "label": a.nom, "couleur": couleur,
                "x": [mdates.date2num(d) for d, _v in serie_a],
                "y": [v for _d, v in serie_a],
            })
            affluents_traces.append(a)
            qmax_a, date_qmax_a = affluents.qmax_et_horodatage(serie_a)
            volume_a = affluents.volume_m3(serie_a)
            if qmax_a is not None:
                ax.plot([date_qmax_a], [qmax_a], marker="o", markersize=7, color=couleur,
                         markeredgecolor="white", markeredgewidth=0.8, zorder=10)
            _ajouter_vignette(a.nom, couleur, qmax_a, date_qmax_a, surface=a.surface_bv_km2)
            pct_volume = (volume_a / volume_exutoire * 100
                          if volume_a is not None and volume_exutoire else None)

            # Q rétropropagé (demandé) : PAS le Qmax propre de l'affluent, mais son
            # débit au moment où l'eau qu'il fournissait atteignait (en théorie) le pic
            # de l'exutoire — càd à (horodatage du Qmax exutoire − P50 de CET
            # affluent). Permet de lire, pour un instant de référence commun (le pic à
            # l'exutoire), la contribution de chaque affluent à ce pic précis.
            q_retropropage = None
            if date_qmax_exutoire is not None and a.p50_min is not None:
                date_lookup = date_qmax_exutoire - timedelta(minutes=a.p50_min)
                q_retropropage, _date_trouvee = affluents.valeur_au_plus_proche(serie_a, date_lookup)
            pct_qmax = (q_retropropage / qmax_exutoire * 100
                        if q_retropropage is not None and qmax_exutoire else None)
            lignes_bilan.append((a.nom, volume_a, pct_volume, q_retropropage, pct_qmax, couleur))
            if pct_qmax is not None and pct_qmax > 0:
                contributions_pie.append((a.nom, couleur, pct_qmax))

            # Bande de propagation P10-P90 (très transparente) + trait épais à P50, à
            # partir du pic de CET affluent (sur la fenêtre de la crue), propagé sur
            # l'axe temporel de l'hydrogramme de l'exutoire (demandé explicitement) —
            # sens INVERSE de la rétropropagation ci-dessus (ici : pic affluent -> pic
            # exutoire ; ci-dessus : pic exutoire -> instant correspondant chez
            # l'affluent). Les deux usent le même P50, dans des directions opposées.
            date_p10, date_p50, date_p90 = affluents.bornes_bande_propagation(date_qmax_a, a)
            if date_p10 is not None and date_p90 is not None:
                ax.axvspan(date_p10, date_p90, color=couleur, alpha=0.12, zorder=0)
            if date_p50 is not None:
                ax.axvline(date_p50, color=couleur, lw=2.4, alpha=0.75, zorder=2)

        _dessiner_camembert_surfaces(
            app.config_data.get("station", {}).get("surface_bv_km2"),
            [(a.nom, a.couleur or "#7D3C98", a.surface_bv_km2) for a in affluents_traces],
        )

        if var_vigilance.get():
            seuils = app.config_data.get("seuils_q", {})
            for cle, libelle, couleur in LIBELLES_SEUILS_Q:
                val = seuils.get(cle)
                if val is None:
                    continue
                # Un seuil au-delà de l'échelle Y actuellement affichée (voir ci-dessus)
                # est ignoré entièrement — ax.text() n'est PAS clippé par défaut par les
                # bornes de l'axe (contrairement à axhline), il flottait sinon au-dessus
                # du cadre du graphique au lieu de disparaître (bug constaté).
                if y_max_visible is not None and val > y_max_visible:
                    continue
                est_zt = cle.startswith("zt_")
                ax.axhline(val, color=couleur, lw=1.0 if est_zt else 1.3,
                            ls=":" if est_zt else "-", alpha=0.85)
                ax.text(0.002, val, f" {libelle} {val:.0f} m³/s", va="bottom", fontsize=6.5,
                         color=couleur, transform=ax.get_yaxis_transform())

        ax.set_ylabel("Débit (m³/s)")
        ax.grid(True, alpha=0.3)
        # Marge droite resserrée (0.68 -> 0.78) : la légende/le camembert n'occupaient
        # qu'une partie de cette marge, le reste restait une zone vide inutile —
        # récupérée ici pour la courbe Qobs elle-même (demandé), plutôt qu'élargir
        # toute la figure (qui n'agrandissait que cette zone vide, pas la courbe).
        fig.subplots_adjust(right=0.78)
        # Fusionnée avec la pluie (sur ax_pluie, absente de ax.legend() par défaut) —
        # ajoutée en DERNIER pour ne pas décaler l'association affluent -> handle
        # ci-dessous (indices 1..N après Q observé, qu'il y ait ou non de la pluie).
        lignes_ax, labels_ax = ax.get_legend_handles_labels()
        lignes_pluie, labels_pluie = ax_pluie.get_legend_handles_labels()
        # Ancrée en HAUT de la marge droite (pas centrée verticalement comme avant) :
        # les vignettes Qmax du panneau Tk juste à droite du graphique sont
        # positionnées pour tomber sous elle (espaceur calé sur cette même hauteur).
        legende = ax.legend(lignes_ax + lignes_pluie, labels_ax + labels_pluie,
                              loc="upper left", bbox_to_anchor=(1.02, 0.98), fontsize=7.5)
        if legende is not None:
            for handle, a in zip(legende.legend_handles[1:], affluents_traces):
                handle.set_picker(6)
                etat_legende["mapping"][id(handle)] = a

        # -- Barycentre de la pluie (marqueur + étiquette + icône d'explication) et
        # temps de réponse (Tr), affiché dans le TITRE de la légende (demandé). --
        if serie_exutoire:
            date_bary = _barycentre_pluie(serie_exutoire, date_limite=date_qmax_exutoire)
            if date_bary is not None:
                x_bary = mdates.date2num(date_bary)
                # Marqueur du barycentre des pluies, sur le bord HAUT du graphique
                # (demandé) — placé en coordonnées mixtes (x = date, en données ;
                # y = 1, en fraction des axes) via get_xaxis_transform(), donc collé au
                # bord supérieur quelle que soit l'échelle Y réellement affichée.
                ax.plot([date_bary], [1], marker="o", markersize=8, color="#0B1F4B",
                         markeredgecolor="white", markeredgewidth=0.8, zorder=15,
                         clip_on=False, transform=ax.get_xaxis_transform())
                # Titre et date en 2 annotations séparées (plutôt qu'un seul texte
                # multi-lignes) : nécessaire pour mesurer la largeur du seul titre et y
                # accoler l'icône "i" juste après "pluie" (demandé). date2num()
                # manuel : ax.annotate() avec un xycoords "transform" personnalisé ne
                # passe pas par le convertisseur d'unités de l'axe (contrairement à
                # ax.plot()), sinon TypeError au rendu (datetime brut transmis à une
                # transformation affine qui attend un float).
                txt_titre = ax.annotate(
                    "Barycentre pluie", xy=(x_bary, 1), xycoords=ax.get_xaxis_transform(),
                    xytext=(0, 17), textcoords="offset points", ha="center", va="bottom",
                    fontsize=6.5, color="#0B1F4B", clip_on=False)
                # Temps de réponse (Tr) calculé ICI (avant l'annotation de l'horodatage,
                # pas seulement dans le titre de la légende plus bas) pour l'accoler
                # directement à l'horodatage du barycentre (demandé) — même calcul,
                # réutilisé pour le titre de légende ci-dessous.
                texte_tr = ""
                if date_qmax_exutoire is not None:
                    minutes_tr = round((date_qmax_exutoire - date_bary).total_seconds() / 60)
                    signe = "-" if minutes_tr < 0 else ""
                    h_tr, m_tr = divmod(abs(minutes_tr), 60)
                    texte_tr = f" -> Tr = {signe}{h_tr}h {m_tr:02d}min"
                ax.annotate(
                    f"{date_bary:%d/%m %H:%M}{texte_tr}", xy=(x_bary, 1),
                    xycoords=ax.get_xaxis_transform(),
                    xytext=(0, 8), textcoords="offset points", ha="center", va="bottom",
                    fontsize=6.5, color="#0B1F4B", clip_on=False)

                # Icône "i" cliquable juste après "pluie" — précise que seules les
                # pluies antérieures au Qmax exutoire sont prises en compte (demandé).
                # Position mesurée sur le rendu réel du titre (bbox), pas devinée.
                fig.canvas.draw()
                bbox_titre = txt_titre.get_window_extent(renderer=fig.canvas.get_renderer())
                x_icone, y_icone = fig.transFigure.inverted().transform(
                    (bbox_titre.x1 + 9, (bbox_titre.y0 + bbox_titre.y1) / 2))
                icone = fig.text(x_icone, y_icone, "i", fontsize=6, color="white",
                                   fontweight="bold", fontstyle="italic", ha="center",
                                   va="center", picker=True, clip_on=False,
                                   bbox=dict(boxstyle="circle,pad=0.25", fc="#0B1F4B",
                                              ec="#0B1F4B", lw=0.6))

                def _clic_icone_bary(event):
                    if event.artist is icone:
                        messagebox.showinfo("Barycentre de la pluie",
                                              _TEXTE_EXPLICATION_BARYCENTRE,
                                              parent=tab_frame.winfo_toplevel())

                etat_icone_bary["artist"] = icone
                etat_icone_bary["cid"] = canvas.mpl_connect("pick_event", _clic_icone_bary)

                if date_qmax_exutoire is not None and legende is not None:
                    trs_selection = _tr_crues_selectionnees(paths, code_pdt, evenements)
                    texte_titre_legende = f"Temps de réponse (Tr) : {signe}{h_tr} h {m_tr:02d} min"
                    if trs_selection:
                        # Ligne vide supplémentaire : réserve la hauteur d'une 2e ligne
                        # dans le titre (agrandit la légende en conséquence, repousse
                        # ses entrées vers le bas) — les P10/P50/P90 y sont posés
                        # PAR-DESSUS juste après, en 3 textes séparés (voir plus bas) :
                        # set_title() n'admet qu'une seule police pour tout son texte,
                        # impossible d'avoir P10/P90 petits et P50 normal-gras sinon.
                        texte_titre_legende += "\n "
                    legende.set_title(texte_titre_legende, prop={"size": 7.5, "weight": "bold"})
                    if trs_selection:
                        def _fmt_tr(minutes):
                            s = "-" if minutes < 0 else ""
                            hh, mm = divmod(round(abs(minutes)), 60)
                            return f"{s}{hh}h {mm:02d}min"

                        def _poser_percentiles():
                            """(Ré)affiche P10/P50/P90, centrés sous le titre du Tr. Isolé
                            en fonction pour pouvoir être rappelé après un éventuel
                            élargissement du cadre de la légende (voir plus bas)."""
                            fig.canvas.draw()
                            renderer = fig.canvas.get_renderer()
                            bbox_titre = legende.get_title().get_window_extent(
                                renderer=renderer).transformed(fig.transFigure.inverted())
                            # La 2e ligne (vide, réservée ci-dessus) occupe le quart
                            # inférieur de la bbox du titre 2 lignes (même interligne).
                            y_ligne2 = bbox_titre.y0 + (bbox_titre.y1 - bbox_titre.y0) * 0.22
                            x_centre = (bbox_titre.x0 + bbox_titre.x1) / 2
                            txt_p50 = fig.text(
                                x_centre, y_ligne2, f"P50 = {_fmt_tr(p50_tr)}",
                                ha="center", va="center", fontsize=6.5, fontweight="bold",
                                color="#0B1F4B")
                            fig.canvas.draw()
                            bbox_p50 = txt_p50.get_window_extent(
                                renderer=fig.canvas.get_renderer()).transformed(fig.transFigure.inverted())
                            # Séparateur "/" inclus dans le texte (plutôt qu'un grand
                            # espace vide entre les segments) pour resserrer l'ensemble —
                            # collé à P50 des deux côtés (pas d'espace entre "/" et le
                            # libellé P50), espace conservé côté P10/P90 uniquement.
                            txt_p10 = fig.text(
                                bbox_p50.x0, y_ligne2, f"P10 = {_fmt_tr(p10_tr)} /",
                                ha="right", va="center", fontsize=5.5, color="#0B1F4B")
                            txt_p90 = fig.text(
                                bbox_p50.x1, y_ligne2, f"/ P90 = {_fmt_tr(p90_tr)}",
                                ha="left", va="center", fontsize=5.5, color="#0B1F4B")
                            return txt_p50, txt_p10, txt_p90

                        p10_tr, p50_tr, p90_tr = np.percentile(trs_selection, [10, 50, 90])

                        # Bande de propagation du Tr — même principe que les bandes
                        # P10-P90 par affluent plus haut, mais projetée depuis le
                        # barycentre de la pluie de LA CRUE AFFICHÉE avec le Tr
                        # statistique des crues sélectionnées (demandé) : estime où
                        # pourrait tomber le pic de cette crue. Couleur du hyétogramme
                        # (#2E86AB, même teinte que son axe Y) pour la distinguer des
                        # bandes de propagation des affluents (couleur propre à chacun) ;
                        # trait P50 plus fin (1.2 contre 2.4) — estimation statistique,
                        # pas une mesure directe, moins de poids visuel voulu.
                        date_p10_tr = date_bary + timedelta(minutes=p10_tr)
                        date_p50_tr = date_bary + timedelta(minutes=p50_tr)
                        date_p90_tr = date_bary + timedelta(minutes=p90_tr)
                        ax.axvspan(date_p10_tr, date_p90_tr, color="#2E86AB", alpha=0.12,
                                    zorder=0)
                        ax.axvline(date_p50_tr, color="#2E86AB", lw=1.2, alpha=0.85,
                                    zorder=2)
                        ax.annotate(
                            "Pic de crue estimé\nà partir du Tr",
                            xy=(mdates.date2num(date_p50_tr), 1),
                            xycoords=ax.get_xaxis_transform(), xytext=(0, 8),
                            textcoords="offset points", ha="center", va="bottom",
                            fontsize=6.5, color="#2E86AB", clip_on=False)

                        artistes = _poser_percentiles()
                        fig.canvas.draw()
                        renderer = fig.canvas.get_renderer()
                        bbox_cadre_legende = legende.get_window_extent(
                            renderer=renderer).transformed(fig.transFigure.inverted())
                        bbox_p90 = artistes[2].get_window_extent(
                            renderer=renderer).transformed(fig.transFigure.inverted())
                        # P10/P50/P90 débordent parfois du cadre de la légende (plus
                        # large que le titre du Tr seul, largeur déterminée par ses
                        # entrées + titre — pas par ces textes posés par-dessus, en
                        # dehors de son système de mise en page). Élargit le cadre en
                        # ajoutant des espaces au titre (seul levier disponible pour une
                        # Legend matplotlib) et repositionne — demandé. Marge de sécurité
                        # (8 px) ajoutée au manque mesuré : un rendu réel (police système,
                        # DPI) peut légèrement différer du rendu de test, mieux vaut
                        # élargir un peu trop que pas assez.
                        marge_securite_px = 15
                        if bbox_p90.x1 > bbox_cadre_legende.x1 - marge_securite_px / fig.bbox.width:
                            for w in artistes:
                                w.remove()
                            manque_px = ((bbox_p90.x1 - bbox_cadre_legende.x1) * fig.bbox.width
                                          + marge_securite_px)
                            txt_espace = fig.text(0, 0, "  ", fontsize=7.5, fontweight="bold")
                            fig.canvas.draw()
                            largeur_espace_px = txt_espace.get_window_extent(
                                renderer=fig.canvas.get_renderer()).width / 2
                            txt_espace.remove()
                            nb_espaces = int(manque_px / max(largeur_espace_px, 1e-6)) + 5
                            # Espaces répartis de part et d'autre (pas tous à la fin) pour
                            # garder le texte "Temps de réponse..." centré dans le cadre
                            # élargi, plutôt que de le faire glisser visuellement à gauche.
                            demi = nb_espaces // 2
                            texte_elargi = (
                                f"{' ' * demi}Temps de réponse (Tr) : {signe}{h_tr} h "
                                f"{m_tr:02d} min{' ' * (nb_espaces - demi)}\n ")
                            legende.set_title(texte_elargi, prop={"size": 7.5, "weight": "bold"})
                            artistes = _poser_percentiles()
                        etat_percentiles_tr["artists"] = list(artistes)

        # Position réelle du bas de la légende (mesurée sur le rendu effectif, pas
        # devinée) — sert au camembert de contribution ci-dessous. Le panneau
        # vignettes Tk, lui, N'EST PLUS aligné sous la légende (essai précédent :
        # s'aligner exactement dessous demandait ~180 px de marge en haut du panneau,
        # bien plus que les 58 px fixes d'avant — les deux widgets sont côte à côte,
        # pas superposés, un vrai chevauchement est donc impossible ; l'alignement
        # esthétique coûtait plus de place qu'il n'en valait la peine). Marge fixe
        # réduite à 12 px (voir label_titre_vignettes) pour remonter tout le panneau
        # (vignettes + camembert de surfaces) et ne plus le couper en bas — demandé.
        bbox_legende_fig = None
        if legende is not None:
            fig.canvas.draw()
            bbox_legende_fig = legende.get_window_extent(
                renderer=fig.canvas.get_renderer()).transformed(fig.transFigure.inverted())

        # -- Camembert de contribution au pic exutoire, sous la légende, pas plus
        # large qu'elle (demandé) — construit à partir des % de Q rétropropagé de
        # chaque affluent (colonne "% du Qmax exutoire" du tableau ci-dessous). Un
        # dépassement de 100 % (affluents suivis fournissant, sur le papier, plus que
        # le débit de pointe observé — incohérence de mesure/temps de propagation)
        # est écrêté au prorata ; un total sous 100 % laisse la part manquante à une
        # part grise "Écoulements locaux" (bassin versant non instrumenté par un
        # affluent suivi). Position/largeur mesurées sur la légende déjà rendue.
        if contributions_pie and bbox_legende_fig is not None:
            total_pct = sum(p for _n, _c, p in contributions_pie)
            if total_pct > 100:
                facteur = 100 / total_pct
                parts = [(n, c, p * facteur) for n, c, p in contributions_pie]
            else:
                parts = list(contributions_pie)
                reste = 100 - total_pct
                if reste > 0.5:
                    parts.append(("Écoulements locaux", _COULEUR_LOCAL, reste))
            # Bord bas aligné sur le bord bas du graphique principal (demandé) — haut
            # calé juste sous la légende comme avant, hauteur déduite des deux.
            y0_pie = ax.get_position().y0
            y1_pie = bbox_legende_fig.y0 - 0.06
            hauteur_pie = max(y1_pie - y0_pie, 0.08)
            ax_pie = fig.add_axes([bbox_legende_fig.x0, y0_pie,
                                     bbox_legende_fig.width, hauteur_pie])
            ax_pie.pie([p for _n, _c, p in parts], colors=[_c for _n, _c, p in parts],
                        autopct=lambda v: f"{v:.0f}%" if v >= 5 else "",
                        textprops={"fontsize": 6}, wedgeprops={"edgecolor": "white", "linewidth": 0.6})
            # Titre sur 2 lignes (demandé) — 2 annotations séparées ancrées en offset
            # POINTS (pas en fraction d'axe, indépendant de la taille du camembert qui
            # varie avec le nombre d'affluents) pour garder un écart fixe et éviter
            # tout chevauchement entre les 2 lignes.
            ax_pie.annotate("Contribution au pic exutoire", xy=(0.5, 1.0),
                              xycoords="axes fraction", xytext=(0, 15),
                              textcoords="offset points", ha="center", va="bottom",
                              fontsize=6.5, color="black", clip_on=False)
            # %age total sur sa propre ligne pour pouvoir le mettre en gras rouge si le
            # total dépasse 100 % (affluents suivis fournissant, sur le papier, plus
            # que le débit de pointe observé — signal d'alerte visuel, demandé) sans
            # changer le style du reste du titre.
            couleur_pct = "#C0392B" if total_pct > 100 else "black"
            poids_pct = "bold" if total_pct > 100 else "normal"
            ax_pie.annotate(f"{total_pct:.0f} %", xy=(0.5, 1.0), xycoords="axes fraction",
                              xytext=(0, 2), textcoords="offset points", ha="center",
                              va="bottom", fontsize=7, color=couleur_pct,
                              fontweight=poids_pct, clip_on=False)
            # ax.pie() applique un aspect 1:1 (adjustable="box") : matplotlib RÉDUIT et
            # RECENTRE verticalement la boîte donnée pour garder un cercle parfait, le
            # bord bas ne tombe donc PAS sur y0_pie tel quel. On mesure la taille
            # réelle obtenue après ce recalage, puis on la replace avec le bon y0.
            fig.canvas.draw()
            pos_reelle = ax_pie.get_position()
            ax_pie.set_position([pos_reelle.x0, y0_pie, pos_reelle.width, pos_reelle.height])
            etat_graphique["ax_pie"] = ax_pie

        fig.autofmt_xdate()
        canvas.draw_idle()

        for station, volume, pct_volume, qmax, pct_qmax, couleur in lignes_bilan:
            # Même principe que la liste "Stations affluentes" : fond éclairci
            # ("semi-transparent") assorti à la couleur de la série, texte en noir.
            tableau_bilan.tag_configure(couleur, background=_eclaircir(couleur),
                                          foreground="black")
            tableau_bilan.insert("", tk.END, values=(
                station,
                f"{volume / 1e6:.3f}" if volume is not None else "—",
                f"{pct_volume:.1f} %" if pct_volume is not None else "—",
                f"{qmax:.1f}" if qmax is not None else "—",
                f"{pct_qmax:.1f} %" if pct_qmax is not None else "—",
            ), tags=(couleur,))

        var_statut.set(
            f"Crue #{evt.num_evt} ({evt.date_deb:%d/%m/%Y %H:%M}) — {len(affluents_traces)} "
            f"affluent(s) tracé(s) sur {len(liste_affl)} configuré(s).")

    def _on_pdt_change(*_evt):
        sauvegarder_dernier_pdt(app, _pas_de_temps_courant(), source=_pdt_change_externe)
        _rafraichir_crues()

    def _pdt_change_externe(code_pdt):
        # Le pas de temps a été changé dans un AUTRE onglet (Dashboard ou Crues) —
        # aligne ce combo sur le même choix sans re-notifier (déjà fait par la source).
        pdt_list_actuelle = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        libelle = next((p["libelle"] for p in pdt_list_actuelle if p["code"] == code_pdt), None)
        if libelle and var_pdt.get() != libelle:
            var_pdt.set(libelle)
            _rafraichir_crues()

    combo_pdt.bind("<<ComboboxSelected>>", _on_pdt_change)
    combo_crue.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_graphique())
    enregistrer_observateur_pdt(app, _pdt_change_externe)

    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
    combo_pdt["values"] = [p["libelle"] for p in pdt_list]
    libelle_init = libelle_dernier_pdt(app, pdt_list)
    if libelle_init:
        var_pdt.set(libelle_init)

    _rafraichir_liste_affl()
    _rafraichir_crues()
