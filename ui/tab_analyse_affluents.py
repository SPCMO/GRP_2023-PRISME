# -*- coding: utf-8 -*-
"""Onglet "Analyse crues affl." — pour chaque crue détectée à la station exutoire
(Moussoulens), superpose le Qobs de l'exutoire aux débits des stations affluentes
configurées par l'utilisateur (nom, surface de BV, temps de propagation P10/P50/P90,
fichier de débits), avec bilan volume/Qmax et bandes de propagation déduites des temps
saisis. Indépendant de tout calage GRP (contrairement à Dashboard > Détail par crue,
qui compare Qobs à une combinaison de calage) : ici on ne regarde que les observations.
"""

import os
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import colorchooser, filedialog, messagebox, ttk

from matplotlib import dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from modules import affluents
from modules.criteres_perf import CriteresPerfError, parse_criteres_perf, parse_evenement_serie
from modules.grp_paths import GrpPaths
from ui.tab_config import LIBELLES_SEUILS_Q
from ui.widgets_common import make_label, make_row, make_scrollable_tab, make_section

_COULEUR_OBS = "#1B4F72"
# Même palette que ui.tab_dashboard._PALETTE_COURBES, dupliquée ici pour la même
# raison que le reste du projet (voir modules/export_excel.py) : chaque couche évite
# de dépendre d'une constante privée d'un autre fichier de la même couche.
_PALETTE_AFFLUENTS = (
    "#CC5500", "#1D6A39", "#7B241C", "#7D3C98", "#117864", "#B7950B",
    "#2874A6", "#A93226", "#5D6D7E", "#943126",
)


def _construire_grp_paths(app):
    chemins = app.config_data.get("chemins", {})
    station = app.config_data.get("station", {})
    if not chemins.get("dossier_resultats") or not station.get("code_site"):
        return None
    return GrpPaths(
        dossier_grp=chemins.get("dossier_grp", ""), dossier_donnees=chemins.get("dossier_donnees", ""),
        dossier_bddtr=chemins.get("dossier_bddtr", ""), dossier_resultats=chemins["dossier_resultats"],
        code_site=station["code_site"],
    )


def _config_affluents(app):
    return app.config_data.setdefault("affluents", affluents.config_affluents_par_defaut())


def _liste_affluents(app):
    return [affluents.affluent_depuis_dict(d) for d in _config_affluents(app).get("liste", [])]


def _persister_affluents(app, liste_affluents):
    _config_affluents(app)["liste"] = [affluents.affluent_vers_dict(a) for a in liste_affluents]
    app.persist_config()


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
    inn2, bg2 = make_section(frm, "Stations affluentes", "bleu")
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
                liste_affl.tag_configure(a.couleur, background=a.couleur, foreground="white")
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

        f_nom = _ligne("Nom de l'affluent :")
        var_nom = tk.StringVar(value=affluent_existant.nom if affluent_existant else "")
        ttk.Entry(f_nom, textvariable=var_nom, width=30).pack(side=tk.LEFT)

        f_surface = _ligne("Surface de BV (km²) :")
        var_surface = tk.StringVar(
            value=f"{affluent_existant.surface_bv_km2:g}"
            if affluent_existant and affluent_existant.surface_bv_km2 is not None else "")
        ttk.Entry(f_surface, textvariable=var_surface, width=12).pack(side=tk.LEFT)

        tk.Label(cadre, text="Temps de propagation jusqu'à l'exutoire (Moussoulens), "
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
            if affluent_existant is None:
                liste_actuelle.append(affluents.Affluent(
                    nom=nom, surface_bv_km2=surface, p10_min=p10, p50_min=p50, p90_min=p90,
                    fichier=fichier, couleur=_prochaine_couleur(liste_actuelle)))
            else:
                for a in liste_actuelle:
                    if a.nom == affluent_existant.nom:
                        a.nom, a.surface_bv_km2 = nom, surface
                        a.p10_min, a.p50_min, a.p90_min = p10, p50, p90
                        a.fichier = fichier
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

    fig = Figure(figsize=(11, 5), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    canvas = FigureCanvasTkAgg(fig, master=cadre_graphique)
    canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    cadre_vignettes = tk.Frame(cadre_graphique, width=190)
    cadre_vignettes.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 0))
    cadre_vignettes.pack_propagate(False)
    # Espaceur : la légende matplotlib est ancrée en haut du même axe (bbox_to_anchor
    # y=0.98, voir plus bas) — ce Label vide pousse les vignettes visuellement SOUS
    # elle plutôt que de les faire chevaucher, sans dépendre d'un calcul de pixels
    # fragile (la hauteur réelle de la légende varie avec le nombre d'affluents).
    tk.Label(cadre_vignettes, text="Qmax observé par courbe :",
              font=("TkDefaultFont", 8, "italic"), fg="#555555").pack(anchor="w", pady=(58, 4))

    def _vider_vignettes():
        for w in cadre_vignettes.winfo_children()[1:]:  # [0] = le libellé ci-dessus, conservé
            w.destroy()

    def _ajouter_vignette(nom, couleur, qmax, date_qmax):
        v = tk.Frame(cadre_vignettes, bg=couleur, relief=tk.RIDGE, borderwidth=2)
        v.pack(fill=tk.X, pady=3)
        tk.Label(v, text=nom, bg=couleur, fg="white", font=("TkDefaultFont", 8, "bold"),
                  wraplength=175, justify=tk.LEFT).pack(anchor="w", padx=5, pady=(3, 0))
        tk.Label(v, text=f"Qmax {qmax:.1f} m³/s" if qmax is not None else "Qmax indisponible",
                 bg=couleur, fg="white", font=("TkDefaultFont", 8)).pack(anchor="w", padx=5)
        if date_qmax is not None:
            tk.Label(v, text=f"{date_qmax:%d/%m/%Y %H:%M}", bg=couleur, fg="white",
                      font=("TkDefaultFont", 8)).pack(anchor="w", padx=5, pady=(0, 3))

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
        "1. On part de l'horodatage du Qmax observé à la station exutoire "
        "(Moussoulens).\n"
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

    def _rafraichir_crues(*_evt):
        paths = _construire_grp_paths(app)
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
        etat_courbes["liste"] = []
        etat_survol["artist"] = ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points", fontsize=7.5,
            zorder=25, visible=False,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#555555", linewidth=1.0, alpha=0.92),
        )
        _vider_vignettes()
        tableau_bilan.delete(*tableau_bilan.get_children())
        etat_legende["mapping"] = {}

        paths = _construire_grp_paths(app)
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
        if serie_exutoire:
            points_exutoire = [(p[0], p[2]) for p in serie_exutoire]
            ax.plot([d for d, _v in points_exutoire], [v for _d, v in points_exutoire],
                     color=_COULEUR_OBS, lw=1.8, label="Q observé — Moussoulens (exutoire)")
            etat_courbes["liste"].append({
                "label": "Q observé — Moussoulens (exutoire)", "couleur": _COULEUR_OBS,
                "x": [mdates.date2num(d) for d, _v in points_exutoire],
                "y": [v for _d, v in points_exutoire],
            })
            qmax_exutoire, date_qmax_exutoire = affluents.qmax_et_horodatage(points_exutoire)
            volume_exutoire = affluents.volume_m3(points_exutoire)
            _ajouter_vignette("Moussoulens (exutoire)", _COULEUR_OBS,
                               qmax_exutoire, date_qmax_exutoire)
            # Au moment de son propre Qmax, l'exutoire "contribue" à 100 % de son
            # propre débit — sert de référence aux % de contribution des affluents
            # ci-dessous (mêmes colonnes, même instant de référence).
            lignes_bilan.append(("Moussoulens (exutoire)", volume_exutoire, None,
                                   qmax_exutoire, 100.0 if qmax_exutoire is not None else None))

        liste_affl = _liste_affluents(app)
        affluents_traces = []
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
            _ajouter_vignette(a.nom, couleur, qmax_a, date_qmax_a)
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
            lignes_bilan.append((a.nom, volume_a, pct_volume, q_retropropage, pct_qmax))

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

        if var_vigilance.get():
            seuils = app.config_data.get("seuils_q", {})
            for cle, libelle, couleur in LIBELLES_SEUILS_Q:
                val = seuils.get(cle)
                if val is None:
                    continue
                est_zt = cle.startswith("zt_")
                ax.axhline(val, color=couleur, lw=1.0 if est_zt else 1.3,
                            ls=":" if est_zt else "-", alpha=0.85)
                ax.text(0.002, val, f" {libelle} {val:.0f} m³/s", va="bottom", fontsize=6.5,
                         color=couleur, transform=ax.get_yaxis_transform())

        ax.set_ylabel("Débit (m³/s)")
        ax.grid(True, alpha=0.3)
        fig.subplots_adjust(right=0.72)
        # Ancrée en HAUT de la marge droite (pas centrée verticalement comme avant) :
        # les vignettes Qmax du panneau Tk juste à droite du graphique sont
        # positionnées pour tomber sous elle (espaceur calé sur cette même hauteur).
        legende = ax.legend(loc="upper left", bbox_to_anchor=(1.02, 0.98), fontsize=7.5)
        if legende is not None:
            for handle, a in zip(legende.legend_handles[1:], affluents_traces):
                handle.set_picker(6)
                etat_legende["mapping"][id(handle)] = a
        fig.autofmt_xdate()
        canvas.draw_idle()

        for station, volume, pct_volume, qmax, pct_qmax in lignes_bilan:
            tableau_bilan.insert("", tk.END, values=(
                station,
                f"{volume / 1e6:.3f}" if volume is not None else "—",
                f"{pct_volume:.1f} %" if pct_volume is not None else "—",
                f"{qmax:.1f}" if qmax is not None else "—",
                f"{pct_qmax:.1f} %" if pct_qmax is not None else "—",
            ))

        var_statut.set(
            f"Crue #{evt.num_evt} ({evt.date_deb:%d/%m/%Y %H:%M}) — {len(affluents_traces)} "
            f"affluent(s) tracé(s) sur {len(liste_affl)} configuré(s).")

    combo_pdt.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_crues())
    combo_crue.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_graphique())

    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
    combo_pdt["values"] = [p["libelle"] for p in pdt_list]
    if pdt_list:
        var_pdt.set(pdt_list[0]["libelle"])

    _rafraichir_liste_affl()
    _rafraichir_crues()
