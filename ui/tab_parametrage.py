# -*- coding: utf-8 -*-
"""Onglet Paramétrage — bloc 3 : pas de temps + horizons de calage à tester, seuils de
calage (SeuilC1), méthode(s) de correction (Tangara/RNA).

La liste des pas de temps possibles et, pour chacun, la liste des horizons de calage
possibles, sont entièrement éditables via l'écran Paramètres (bouton dédié) — ajout,
modification, suppression, réorganisation — plutôt que codées en dur dans le script,
conformément à la demande initiale. Les horizons "15 min" sont déjà connus ; "30 min" et
"1 h" sont vides en attendant les valeurs métier (voir Aide.html > Suivi des correctifs).
"""

import re
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from ui.widgets_common import build_liste_reordonnable, make_label, make_row, make_scrollable_tab, make_section

_MOTIF_DUREE_GRP = re.compile(r"^\d{2}J\d{2}H\d{2}M$")

NOTE_STRATEGIE = (
    "Conseil : une campagne complète (tous les horizons × tous les seuils × toutes les "
    "crues) peut être très longue. Il est recommandé de lancer d'abord une campagne sur "
    "quelques horizons et seuils espacés (grille grossière), puis d'affiner avec une "
    "seconde campagne resserrée autour de la meilleure zone repérée dans le Dashboard — "
    "les résultats des deux campagnes se cumulent automatiquement."
)


def _valider_duree_grp(valeur):
    """Valide le format xxJxxHxxM (horizons et codes de pas de temps GRP). Lève
    ValueError explicite plutôt que de laisser passer une valeur qui ferait échouer
    l'écriture de LISTE_BASSINS.DAT bien plus tard, loin de la saisie fautive."""
    valeur = valeur.strip().upper()
    if not _MOTIF_DUREE_GRP.match(valeur):
        raise ValueError(
            f"Format invalide : {valeur!r} — attendu xxJxxHxxM (ex. 02J12H00M pour "
            "2 jours 12 heures)."
        )
    return valeur


def build_tab_parametrage(tab_frame, app):
    frm = make_scrollable_tab(tab_frame)
    parametrage = app.config_data.setdefault("parametrage", {})
    parametrage.setdefault("pas_de_temps", [])
    parametrage.setdefault("horizons_par_pdt", {})
    parametrage.setdefault("horizons_selectionnes", {})
    parametrage.setdefault("seuils_calage", [])
    parametrage.setdefault("methodes_selectionnees", [])

    tk.Label(frm, text=NOTE_STRATEGIE, wraplength=820, justify=tk.LEFT,
             fg="#555555", font=("TkDefaultFont", 8, "italic")).pack(
        anchor="w", padx=14, pady=(6, 0))

    # ── Pas de temps + horizons ──────────────────────────────────────────────────
    inn, bg = make_section(frm, "Horizons de calage à tester", "vert")

    r = make_row(inn, bg)
    make_label(r, "Pas de temps de calage :", bg, width=22)
    var_pdt_libelle = tk.StringVar()
    combo_pdt = ttk.Combobox(r, textvariable=var_pdt_libelle, state="readonly", width=18)
    combo_pdt.pack(side=tk.LEFT, padx=(2, 8))
    ttk.Button(r, text="Paramètres…",
               command=lambda: _ouvrir_parametres(app, _rafraichir_combo_pdt)).pack(side=tk.LEFT)

    cadre_horizons = tk.Frame(inn, bg=bg)
    cadre_horizons.pack(fill=tk.X, pady=(6, 2))
    horizon_vars = {}  # horizon (str) -> BooleanVar, pour le pas de temps actuellement affiché

    def _code_pdt_courant():
        for p in parametrage["pas_de_temps"]:
            if p["libelle"] == var_pdt_libelle.get():
                return p["code"]
        return None

    def _sauver_selection_horizons():
        code_pdt = _code_pdt_courant()
        if not code_pdt:
            return
        parametrage["horizons_selectionnes"][code_pdt] = [
            h for h, v in horizon_vars.items() if v.get()
        ]
        app.persist_config()

    def _rafraichir_horizons(*_evt):
        for w in cadre_horizons.winfo_children():
            w.destroy()
        horizon_vars.clear()
        code_pdt = _code_pdt_courant()
        if not code_pdt:
            return
        horizons = parametrage["horizons_par_pdt"].get(code_pdt, [])
        if not horizons:
            tk.Label(cadre_horizons, bg=bg, fg="#a94442",
                     text="Aucun horizon défini pour ce pas de temps — bouton Paramètres "
                          "pour en ajouter.").pack(anchor="w")
            return

        deja_selectionnes = set(parametrage["horizons_selectionnes"].get(code_pdt, []))
        ligne_boutons = tk.Frame(cadre_horizons, bg=bg)
        ligne_boutons.pack(anchor="w")
        ttk.Button(ligne_boutons, text="Tout sélectionner",
                   command=lambda: _tout_horizons(True)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ligne_boutons, text="Tout désélectionner",
                   command=lambda: _tout_horizons(False)).pack(side=tk.LEFT)

        grille = tk.Frame(cadre_horizons, bg=bg)
        grille.pack(anchor="w", pady=(4, 0))
        for i, h in enumerate(horizons):
            v = tk.BooleanVar(value=h in deja_selectionnes)
            horizon_vars[h] = v
            tk.Checkbutton(grille, text=h, variable=v, bg=bg,
                           command=_sauver_selection_horizons).grid(
                row=i // 5, column=i % 5, sticky="w", padx=4, pady=2)

    def _tout_horizons(valeur):
        for v in horizon_vars.values():
            v.set(valeur)
        _sauver_selection_horizons()

    def _rafraichir_combo_pdt():
        combo_pdt["values"] = [p["libelle"] for p in parametrage["pas_de_temps"]]
        if not parametrage["pas_de_temps"]:
            var_pdt_libelle.set("")
        elif var_pdt_libelle.get() not in combo_pdt["values"]:
            var_pdt_libelle.set(parametrage["pas_de_temps"][0]["libelle"])
        _rafraichir_horizons()

    combo_pdt.bind("<<ComboboxSelected>>", _rafraichir_horizons)
    _rafraichir_combo_pdt()

    # ── Seuils de calage ─────────────────────────────────────────────────────────
    inn2, bg2 = make_section(frm, "Seuils de calage à tester (SeuilC1, m3/s)", "ocre")
    tk.Label(inn2, bg=bg2, fg="#555555", font=("TkDefaultFont", 8),
              text="Débit minimal au-dessus duquel le calage est effectué — évite de "
                   "perturber le modèle en basses eaux (batillage).").pack(anchor="w")

    def _obtenir_seuils():
        return parametrage["seuils_calage"]

    def _definir_seuils(nouvelle_liste):
        parametrage["seuils_calage"] = nouvelle_liste
        app.persist_config()

    def _saisir_seuil(valeur_initiale=None):
        texte = simpledialog.askstring(
            "Seuil de calage", "Valeur du seuil (m3/s, ex. 5.00) :",
            initialvalue=f"{valeur_initiale:.2f}" if valeur_initiale is not None else "0.00",
            parent=app,
        )
        if texte is None:
            return None
        try:
            return round(float(texte.strip().replace(",", ".")), 2)
        except ValueError:
            messagebox.showerror("Seuil de calage", f"Valeur non numérique : {texte!r}")
            return None

    build_liste_reordonnable(
        inn2, _obtenir_seuils, _definir_seuils, formatter=lambda v: f"{v:.2f}",
        on_ajouter=_saisir_seuil, on_modifier=_saisir_seuil, hauteur=4, largeur=20,
    ).pack(anchor="w", pady=4)

    # ── Méthode(s) de correction ─────────────────────────────────────────────────
    inn3, bg3 = make_section(frm, "Méthode(s) de correction des sorties", "teal")
    r = make_row(inn3, bg3)
    methodes_actuelles = set(parametrage["methodes_selectionnees"])
    var_t = tk.BooleanVar(value="T" in methodes_actuelles)
    var_r = tk.BooleanVar(value="R" in methodes_actuelles)

    def _sauver_methodes():
        methodes = []
        if var_t.get():
            methodes.append("T")
        if var_r.get():
            methodes.append("R")
        parametrage["methodes_selectionnees"] = methodes
        app.persist_config()

    tk.Checkbutton(r, text="Tangara (T)", variable=var_t, bg=bg3,
                   command=_sauver_methodes).pack(side=tk.LEFT, padx=(0, 12))
    tk.Checkbutton(r, text="Réseau de neurones artificiels (R)", variable=var_r, bg=bg3,
                   command=_sauver_methodes).pack(side=tk.LEFT)
    tk.Label(inn3, bg=bg3, fg="#555555", font=("TkDefaultFont", 8),
             text="Les deux cochées : la campagne lance les runs pour T puis pour R "
                  "successivement (GRP n'accepte qu'une seule méthode par run en mode BDTR).").pack(
        anchor="w", pady=(2, 0))


# ═══════════════════════════════════════════════════════════════════════════════════
# Écran Paramètres — édition des pas de temps et de leurs horizons possibles
# ═══════════════════════════════════════════════════════════════════════════════════

def _ouvrir_parametres(app, on_fermeture):
    parametrage = app.config_data["parametrage"]

    fenetre = tk.Toplevel(app)
    fenetre.title("Paramètres — pas de temps et horizons de calage")
    fenetre.geometry("760x420")
    fenetre.transient(app)
    fenetre.grab_set()

    conteneur = tk.Frame(fenetre)
    conteneur.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # -- Colonne gauche : pas de temps --------------------------------------------
    colonne_gauche = tk.LabelFrame(conteneur, text="Pas de temps")
    colonne_gauche.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

    def _obtenir_pdt():
        return parametrage["pas_de_temps"]

    def _definir_pdt(nouvelle_liste):
        parametrage["pas_de_temps"] = nouvelle_liste
        app.persist_config()
        _rafraichir_horizons_pdt()

    def _saisir_pdt(valeur_initiale=None):
        code_init = valeur_initiale["code"] if valeur_initiale else "00J00H15M"
        libelle_init = valeur_initiale["libelle"] if valeur_initiale else ""
        code = simpledialog.askstring(
            "Pas de temps", "Code GRP (format xxJxxHxxM, ex. 00J00H15M) :",
            initialvalue=code_init, parent=fenetre)
        if code is None:
            return None
        try:
            code = _valider_duree_grp(code)
        except ValueError as e:
            messagebox.showerror("Pas de temps", str(e))
            return None
        libelle = simpledialog.askstring(
            "Pas de temps", "Libellé affiché (ex. 15 min) :",
            initialvalue=libelle_init, parent=fenetre)
        if not libelle:
            return None
        return {"code": code, "libelle": libelle.strip()}

    liste_pdt = build_liste_reordonnable(
        colonne_gauche, _obtenir_pdt, _definir_pdt,
        formatter=lambda p: f"{p['libelle']}  ({p['code']})",
        on_ajouter=_saisir_pdt, on_modifier=_saisir_pdt, hauteur=12, largeur=28,
    )
    liste_pdt.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
    liste_pdt_box = liste_pdt.winfo_children()[0]  # le Listbox lui-même

    # -- Colonne droite : horizons du pas de temps sélectionné à gauche ------------
    colonne_droite = tk.LabelFrame(conteneur, text="Horizons possibles pour ce pas de temps")
    colonne_droite.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
    cadre_liste_horizons = tk.Frame(colonne_droite)
    cadre_liste_horizons.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

    def _pdt_selectionne():
        sel = liste_pdt_box.curselection()
        if not sel:
            return None
        return parametrage["pas_de_temps"][sel[0]]

    def _obtenir_horizons():
        pdt = _pdt_selectionne()
        if not pdt:
            return []
        return parametrage["horizons_par_pdt"].setdefault(pdt["code"], [])

    def _definir_horizons(nouvelle_liste):
        pdt = _pdt_selectionne()
        if not pdt:
            return
        parametrage["horizons_par_pdt"][pdt["code"]] = nouvelle_liste
        app.persist_config()

    def _saisir_horizon(valeur_initiale=None):
        texte = simpledialog.askstring(
            "Horizon de calage", "Horizon (format xxJxxHxxM, ex. 02J12H00M) :",
            initialvalue=valeur_initiale or "00J01H00M", parent=fenetre)
        if texte is None:
            return None
        try:
            return _valider_duree_grp(texte)
        except ValueError as e:
            messagebox.showerror("Horizon de calage", str(e))
            return None

    def _rafraichir_horizons_pdt(*_evt):
        for w in cadre_liste_horizons.winfo_children():
            w.destroy()
        if _pdt_selectionne() is None:
            tk.Label(cadre_liste_horizons,
                     text="Sélectionnez un pas de temps à gauche.").pack(anchor="w")
            return
        build_liste_reordonnable(
            cadre_liste_horizons, _obtenir_horizons, _definir_horizons,
            formatter=str, on_ajouter=_saisir_horizon, on_modifier=_saisir_horizon,
            hauteur=12, largeur=20,
        ).pack(fill=tk.BOTH, expand=True)

    liste_pdt_box.bind("<<ListboxSelect>>", _rafraichir_horizons_pdt)
    _rafraichir_horizons_pdt()

    def _fermer():
        fenetre.destroy()
        on_fermeture()

    ttk.Button(fenetre, text="Fermer", command=_fermer).pack(pady=(0, 10))
    fenetre.protocol("WM_DELETE_WINDOW", _fermer)
