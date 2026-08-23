# -*- coding: utf-8 -*-
"""Onglet Dashboard — bloc 6 : synthèse des résultats de campagne (results_store),
remplace l'unique graphique Excel du script d'origine par 3 vues complémentaires :

  1. Vue synthèse : heatmap horizon × seuil (score composite), classement des
     meilleures combinaisons, dispersion de |dQP| par horizon.
  2. Détail par crue : courbe Qobs (+ Qsimulé si disponible) avec seuils de vigilance
     PHyC superposés, indicateurs dQP/dTP/VE/KGE de la combinaison choisie.
  3. Sensibilité au seuil de calage : score/KGE moyen en fonction de SeuilC1, à horizon
     et méthode fixés.
"""

import os
import re
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib import colormaps
from matplotlib import dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 — nécessaire pour projection="3d"

from modules import export_excel, results_store
from modules.criteres_perf import CriteresPerfError, parse_evenement_serie, parse_criteres_perf
from modules.grp_paths import GrpPaths
from modules.score import (
    PROFILS_PONDERATION, calculer_scores, config_ponderation_par_defaut,
    explication_score, filtrer_par_crues, resoudre_ponderation,
)
from ui.tab_config import LIBELLES_SEUILS_Q
from ui.widgets_common import bouton_info, make_label, make_row, make_section

# Couleur de la courbe Q observé (Détail par crue) — bleu net, distinct des couleurs
# de _PALETTE_COURBES ci-dessous pour ne jamais être confondu avec une courbe simulée.
_COULEUR_OBS = "#1B4F72"

# Palette qualitative pour différencier les courbes simulées superposées (Q observé
# garde toujours sa propre couleur fixe, jamais piochée ici, pour rester reconnaissable
# quel que soit le nombre de combinaisons sélectionnées).
_PALETTE_COURBES = (
    "#CC5500", "#1D6A39", "#7B241C", "#7D3C98", "#117864", "#B7950B",
    "#2874A6", "#A93226", "#5D6D7E", "#943126",
)


def _icone_info_axe(fig, canvas, etat, cle, x, y, titre, texte):
    """Dessine un repère "i" cliquable (rond bleu) directement DANS la figure
    matplotlib, à la position figure-relative (x, y) — contrairement au bouton "ⓘ"
    Tkinter classique (ui.widgets_common.bouton_info, utilisé pour les icônes du
    bandeau/légende), celui-ci peut se coller précisément à un label d'axe ou de
    colorbar qui fait partie du rendu matplotlib, pas des widgets Tkinter.

    ⚠️ Le caractère "ⓘ" (U+24D8) n'existe pas dans la police par défaut de matplotlib
    (DejaVu Sans) — il s'affichait comme un glyphe manquant (rectangle vide) une fois
    réellement rendu, constaté en testant le rendu réel de la figure. D'où le simple
    "i" italique sur fond rond bleu ci-dessous plutôt que le caractère Unicode dédié
    (qui, lui, s'affiche correctement dans les boutons Tkinter classiques, rendus par
    une police système différente).

    `etat` est un dict partagé entre les appels successifs de la fonction de tracé du
    même graphique (clé `cle` dédiée si plusieurs icônes sur la même figure) : un
    ax.clear()/fig.clear() ne supprime PAS les éléments ajoutés directement sur la
    figure (fig.text), donc sans ce nettoyage explicite chaque rafraîchissement
    empilerait un nouveau marqueur par-dessus les précédents.
    """
    ancien = etat.get(cle)
    if ancien is not None:
        marqueur_prec, cid_prec = ancien
        try:
            marqueur_prec.remove()
        except Exception:
            pass
        canvas.mpl_disconnect(cid_prec)

    marqueur = fig.text(x, y, "i", fontsize=10, color="white", fontweight="bold",
                         fontstyle="italic", ha="center", va="center", picker=True,
                         bbox=dict(boxstyle="circle,pad=0.3", fc="#1A5276", ec="#0B2C40", lw=0.8))

    def _au_clic(event):
        if event.artist is marqueur:
            messagebox.showinfo(titre, texte)

    cid = canvas.mpl_connect("pick_event", _au_clic)
    etat[cle] = (marqueur, cid)

_MOTIF_HORIZON = re.compile(r"(\d{2})J(\d{2})H(\d{2})M")


def _horizon_en_minutes(horizon):
    """Convertit 'ddJhhHmmM' en minutes, pour trier/positionner numériquement les
    horizons sur les graphiques (l'ordre alphabétique de la chaîne n'est pas l'ordre
    chronologique : '02J...' < '10J...' textuellement mais pas numériquement à 2 chiffres,
    ceci dit ici toujours sur 2 chiffres donc surtout utile pour l'axe X continu)."""
    m = _MOTIF_HORIZON.match(horizon)
    if not m:
        return 0
    j, h, mn = (int(g) for g in m.groups())
    return j * 1440 + h * 60 + mn


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


_LIBELLES_PROFIL = {
    "egal": PROFILS_PONDERATION["egal"]["libelle"],
    "metier": PROFILS_PONDERATION["metier"]["libelle"],
    "personnalise": "Personnalisé (bouton Réglages…)",
}


def build_tab_dashboard(tab_frame, app):
    # ── Bandeau partagé : choix de la pondération du score composite ─────────────
    # Un seul sélecteur pour les 3 vues qui affichent un score (Vue synthèse,
    # Sensibilité, Vue 3D) plutôt que de le dupliquer 3 fois — elles doivent toujours
    # utiliser la MÊME pondération, jamais 3 réglages indépendants qui pourraient
    # diverger silencieusement.
    barre_pondération = tk.Frame(tab_frame)
    barre_pondération.pack(fill=tk.X, padx=8, pady=(6, 0))
    tk.Label(barre_pondération, text="Pondération du score composite :").pack(side=tk.LEFT)
    var_profil = tk.StringVar()
    combo_profil = ttk.Combobox(barre_pondération, textvariable=var_profil, state="readonly",
                                 width=38, values=list(_LIBELLES_PROFIL.values()))
    combo_profil.pack(side=tk.LEFT, padx=(6, 6))
    ttk.Button(barre_pondération, text="Réglages…",
               command=lambda: _ouvrir_reglages_score(
                   app, lambda: _apres_reglages_personnalises())).pack(side=tk.LEFT)

    # Sélecteur de crues incluses dans le score — même principe que la pondération
    # ci-dessus (un seul réglage partagé par les 3 vues qui affichent un score) :
    # permet de recalculer le score sur un sous-ensemble de crues sans reprendre aucun
    # calage/rejeu GRP (demandé explicitement).
    tk.Label(barre_pondération, text="   Crues dans le score :").pack(side=tk.LEFT)
    var_crues_score = tk.StringVar(value="")
    tk.Label(barre_pondération, textvariable=var_crues_score, font=("TkDefaultFont", 9, "bold")).pack(
        side=tk.LEFT, padx=(4, 6))
    ttk.Button(barre_pondération, text="Choisir…",
               command=lambda: _ouvrir_selecteur_crues_score(
                   app, lambda: _apres_choix_crues_score())).pack(side=tk.LEFT)

    def _maj_label_crues_score():
        total = len(_lister_crues_pour_score(app))
        incluses = _crues_incluses_score(app)
        nb_incluses = total if incluses is None else len(incluses)
        var_crues_score.set(f"{nb_incluses}/{total}")

    def _apres_choix_crues_score():
        _maj_label_crues_score()
        _rafraichir_toutes_les_vues_du_score()

    sous_notebook = ttk.Notebook(tab_frame)
    sous_notebook.pack(fill=tk.BOTH, expand=True)

    onglet_synthese = ttk.Frame(sous_notebook)
    onglet_detail = ttk.Frame(sous_notebook)
    onglet_sensibilite = ttk.Frame(sous_notebook)
    onglet_3d = ttk.Frame(sous_notebook)
    sous_notebook.add(onglet_synthese, text="Vue synthèse")
    sous_notebook.add(onglet_detail, text="Détail par crue")
    sous_notebook.add(onglet_sensibilite, text="Sensibilité au seuil")
    sous_notebook.add(onglet_3d, text="Vue 3D")

    _rafraichir_synthese = _build_synthese(onglet_synthese, app)
    _build_detail(onglet_detail, app)
    _rafraichir_sensibilite = _build_sensibilite(onglet_sensibilite, app)
    _rafraichir_vue3d = _build_vue3d(onglet_3d, app)

    def _rafraichir_toutes_les_vues_du_score():
        # Détail par crue n'affiche pas de score composite (dQP/dTP/VE/KGE de
        # référence seulement) — volontairement absent de cette liste.
        _rafraichir_synthese()
        _rafraichir_sensibilite()
        _rafraichir_vue3d()

    def _appliquer_profil(*_evt):
        cfg = _config_score(app)
        profil_selectionne = next((cle for cle, libelle in _LIBELLES_PROFIL.items()
                                    if libelle == var_profil.get()), "egal")
        cfg["profil"] = profil_selectionne
        app.persist_config()
        _rafraichir_toutes_les_vues_du_score()

    def _apres_reglages_personnalises():
        """Rappelée par la fenêtre "Réglages…" une fois les valeurs personnalisées
        enregistrées (voir _ouvrir_reglages_score, qui a déjà mis à jour
        app.config_data["score"] et appelé app.persist_config()) : met juste à jour
        l'affichage du sélecteur et retrace les 3 vues concernées."""
        var_profil.set(_LIBELLES_PROFIL["personnalise"])
        _rafraichir_toutes_les_vues_du_score()

    combo_profil.bind("<<ComboboxSelected>>", _appliquer_profil)
    var_profil.set(_LIBELLES_PROFIL.get(_config_score(app).get("profil", "egal"),
                                          _LIBELLES_PROFIL["egal"]))
    _maj_label_crues_score()


_CHAMPS_POIDS = (("dqp", "Poids |dQP|"), ("dtp", "Poids |dTP|"),
                  ("ve", "Poids |VE|"), ("kge", "Poids (1−KGE)"))
_CHAMPS_ASYMETRIE = (("retard", "Facteur retard (dTP > 0)"), ("avance", "Facteur avance (dTP < 0)"))


def _ouvrir_reglages_score(app, apres_enregistrement):
    """Fenêtre d'édition libre de la pondération du score composite — poids des 4
    indicateurs et facteurs d'asymétrie sur dTP (retard vs avance), en plus des 2
    profils prédéfinis (Poids égaux / Pondération métier) proposés dans le sélecteur
    principal. Pré-rempli avec la pondération ACTUELLEMENT active (quel que soit le
    profil en cours), pour éditer à partir de là plutôt que de repartir de valeurs
    figées sans rapport avec ce qui est affiché à l'instant."""
    poids_actuels, asymetrie_actuelle, _libelle = resoudre_ponderation(_config_score(app))

    fenetre = tk.Toplevel(app)
    fenetre.title("Réglages de la pondération du score composite")
    fenetre.geometry("420x360")
    fenetre.transient(app)
    fenetre.grab_set()

    tk.Label(fenetre, text="Poids des 4 indicateurs (valeurs relatives — seul le "
                            "RAPPORT entre elles compte, pas leur échelle absolue) :",
             wraplength=390, justify=tk.LEFT).pack(anchor="w", padx=10, pady=(10, 4))

    variables_poids = {}
    for cle, libelle in _CHAMPS_POIDS:
        ligne = tk.Frame(fenetre)
        ligne.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(ligne, text=libelle, width=22, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=f"{poids_actuels.get(cle, 1.0):.2f}")
        variables_poids[cle] = var
        tk.Entry(ligne, textvariable=var, width=8).pack(side=tk.LEFT)

    tk.Label(fenetre, text="Asymétrie sur dTP — un dTP positif (retard) est multiplié "
                            "par le premier facteur, un dTP négatif (avance) par le "
                            "second, avant normalisation. 1.00/1.00 = symétrique "
                            "(comportement d'origine).",
             wraplength=390, justify=tk.LEFT).pack(anchor="w", padx=10, pady=(12, 4))

    variables_asymetrie = {}
    for cle, libelle in _CHAMPS_ASYMETRIE:
        ligne = tk.Frame(fenetre)
        ligne.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(ligne, text=libelle, width=22, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=f"{asymetrie_actuelle.get(cle, 1.0):.2f}")
        variables_asymetrie[cle] = var
        tk.Entry(ligne, textvariable=var, width=8).pack(side=tk.LEFT)

    var_erreur = tk.StringVar(value="")
    tk.Label(fenetre, textvariable=var_erreur, fg="#A93226", wraplength=390,
             justify=tk.LEFT).pack(anchor="w", padx=10, pady=(8, 0))

    def _enregistrer():
        try:
            nouveaux_poids = {cle: float(var.get().strip().replace(",", "."))
                               for cle, var in variables_poids.items()}
            nouvelle_asymetrie = {cle: float(var.get().strip().replace(",", "."))
                                   for cle, var in variables_asymetrie.items()}
        except ValueError as e:
            var_erreur.set(f"Valeur non numérique : {e}")
            return
        if any(v < 0 for v in nouveaux_poids.values()) or any(v <= 0 for v in nouvelle_asymetrie.values()):
            var_erreur.set("Les poids doivent être ≥ 0 et les facteurs d'asymétrie > 0.")
            return
        if sum(nouveaux_poids.values()) == 0:
            var_erreur.set("Au moins un poids doit être strictement positif.")
            return

        cfg = _config_score(app)
        cfg["profil"] = "personnalise"
        cfg["poids_personnalise"] = nouveaux_poids
        cfg["asymetrie_personnalisee"] = nouvelle_asymetrie
        app.persist_config()
        fenetre.destroy()
        apres_enregistrement()

    barre_boutons = tk.Frame(fenetre)
    barre_boutons.pack(pady=(14, 10))
    ttk.Button(barre_boutons, text="Enregistrer", command=_enregistrer).pack(side=tk.LEFT, padx=4)
    ttk.Button(barre_boutons, text="Annuler", command=fenetre.destroy).pack(side=tk.LEFT, padx=4)


def _charger_resultats(app):
    """Retourne la liste des lignes (dict) results_store.list_resultats_avec_combinaison,
    ou [] avec un message d'erreur explicite si la base n'est pas accessible."""
    try:
        with results_store.db_session() as conn:
            return [dict(r) for r in results_store.list_resultats_avec_combinaison(conn)], None
    except Exception as e:
        return [], f"Impossible de lire les résultats : {e}"


def _config_score(app):
    """État persisté du choix de pondération (onglet Dashboard, sélecteur partagé —
    voir build_tab_dashboard). "egal" reste le comportement d'origine, jamais modifié
    par défaut (demande explicite de l'utilisateur : ne pas changer le score existant
    sans qu'il le décide)."""
    return app.config_data.setdefault("score", config_ponderation_par_defaut())


def _poids_actifs(app):
    """Résout la pondération RÉELLEMENT active à cet instant (poids, asymetrie_dtp,
    libellé) — lue par les 3 vues du Dashboard qui calculent un score composite, pour
    qu'elles utilisent toutes la même pondération que celle affichée dans le
    sélecteur, sans jamais la dupliquer en dur."""
    return resoudre_ponderation(_config_score(app))


def _crues_incluses_score(app):
    """Liste ISO des crues actuellement incluses dans le calcul du score composite (voir
    _ouvrir_selecteur_crues_score), ou None si toutes les crues disponibles sont
    incluses — réglage par défaut, comportement d'origine inchangé. Un ensemble vide
    stocké en config est traité comme "toutes" plutôt que "aucune" : un score sur zéro
    crue n'a pas de sens et une liste vide ne peut venir que d'un import/config corrompu,
    jamais du sélecteur lui-même (qui empêche d'enregistrer une sélection vide)."""
    valeur = _config_score(app).get("crues_incluses")
    return valeur if valeur else None


def _filtrer_lignes_score(app, lignes_ok):
    return filtrer_par_crues(lignes_ok, _crues_incluses_score(app))


def _lister_crues_pour_score(app):
    """Liste (iso, libelle) de toutes les crues ayant au moins un résultat réussi en
    base, triées par n° d'événement (CRITERES_PERF.DAT, "#N - date") puis par date pour
    celles non numérotées — même principe de libellé que Dashboard > Détail par crue.
    Alimente le sélecteur de crues du score (voir _ouvrir_selecteur_crues_score)."""
    lignes, _erreur = _charger_resultats(app)
    dates_disponibles = sorted({l["crue_date"] for l in lignes if l["statut_crue"] == "success"})
    if not dates_disponibles:
        return []

    entrees = []
    restants = set(dates_disponibles)
    paths = _construire_grp_paths(app)
    if paths is not None:
        for pdt in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if not restants:
                break
            try:
                evenements = parse_criteres_perf(paths.criteres_perf_dat(pdt["code"]))
            except (FileNotFoundError, CriteresPerfError):
                continue
            for e in evenements:
                iso = e.date_deb.isoformat()
                if iso in restants:
                    entrees.append((e.num_evt, iso, e.date_deb))
                    restants.discard(iso)
    for iso in sorted(restants):
        entrees.append((None, iso, datetime.fromisoformat(iso)))
    entrees.sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0))

    return [(iso, f"{f'#{num}' if num is not None else '?'} - {d:%d/%m/%Y %H:%M}")
            for num, iso, d in entrees]


def _ouvrir_selecteur_crues_score(app, apres_enregistrement):
    """Fenêtre de sélection des crues incluses dans le calcul du score composite —
    permet de recalculer le score sur un sous-ensemble de crues (ex. exclure un épisode
    atypique) sans reprendre aucun calage/rejeu GRP, voir modules.score.filtrer_par_crues.
    Pré-cochée sur la sélection ACTUELLEMENT active (toutes par défaut)."""
    crues = _lister_crues_pour_score(app)
    incluses_actuelles = _crues_incluses_score(app)

    fenetre = tk.Toplevel(app)
    fenetre.title("Crues incluses dans le score composite")
    fenetre.geometry("420x480")
    fenetre.transient(app)
    fenetre.grab_set()

    tk.Label(fenetre, text="Décocher une crue l'exclut du calcul du score composite "
                           "(Vue synthèse, Sensibilité au seuil, Vue 3D, et la fenêtre "
                           "\"Combinaisons déjà réalisées\" de l'onglet Campagne) — sans "
                           "reprendre aucun calage ni rejeu GRP.",
             wraplength=390, justify=tk.LEFT).pack(anchor="w", padx=10, pady=(10, 6))

    if not crues:
        tk.Label(fenetre, text="Aucune crue avec résultat en base pour l'instant.",
                 fg="#a94442").pack(anchor="w", padx=10)

    cadre_liste = tk.Frame(fenetre)
    cadre_liste.pack(fill=tk.BOTH, expand=True, padx=10)
    liste = tk.Listbox(cadre_liste, selectmode=tk.EXTENDED, exportselection=False)
    ascenseur = ttk.Scrollbar(cadre_liste, orient=tk.VERTICAL, command=liste.yview)
    liste.configure(yscrollcommand=ascenseur.set)
    liste.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur.pack(side=tk.RIGHT, fill=tk.Y)
    for _iso, libelle in crues:
        liste.insert(tk.END, libelle)
    isos_deja_incluses = set(incluses_actuelles) if incluses_actuelles is not None else None
    for i, (iso, _libelle) in enumerate(crues):
        if isos_deja_incluses is None or iso in isos_deja_incluses:
            liste.selection_set(i)

    barre_rapide = tk.Frame(fenetre)
    barre_rapide.pack(pady=(6, 0))
    ttk.Button(barre_rapide, text="Toutes",
               command=lambda: liste.selection_set(0, tk.END)).pack(side=tk.LEFT, padx=4)
    ttk.Button(barre_rapide, text="Aucune",
               command=lambda: liste.selection_clear(0, tk.END)).pack(side=tk.LEFT, padx=4)

    var_erreur = tk.StringVar(value="")
    tk.Label(fenetre, textvariable=var_erreur, fg="#A93226", wraplength=390,
             justify=tk.LEFT).pack(anchor="w", padx=10, pady=(6, 0))

    def _enregistrer():
        selection = liste.curselection()
        if not selection:
            var_erreur.set("Sélectionnez au moins une crue (un score sur aucune crue "
                            "n'a pas de sens).")
            return
        cfg = _config_score(app)
        if len(selection) == len(crues):
            cfg["crues_incluses"] = None  # "toutes" — suit dynamiquement les futures crues
        else:
            cfg["crues_incluses"] = [crues[i][0] for i in selection]
        app.persist_config()
        fenetre.destroy()
        apres_enregistrement()

    barre_boutons = tk.Frame(fenetre)
    barre_boutons.pack(pady=(10, 10))
    ttk.Button(barre_boutons, text="Enregistrer", command=_enregistrer).pack(side=tk.LEFT, padx=4)
    ttk.Button(barre_boutons, text="Annuler", command=fenetre.destroy).pack(side=tk.LEFT, padx=4)


# ══════════════════════════════════════════════════════════════════════════════════
# 1. Vue synthèse
# ══════════════════════════════════════════════════════════════════════════════════

_TEXTE_VALEURS_EXTREMES = (
    "Une valeur est considérée \"extrême\" si elle dépasse 1,5 fois l'écart "
    "interquartile (Q3 − Q1) au-delà de Q1 ou de Q3 — convention statistique standard "
    "d'une boîte à moustaches (paramètre whis=1.5 de matplotlib).\n\n"
    "Ces valeurs sont ici masquées plutôt qu'affichées comme des points isolés : le "
    "maximum/minimum affiché est donc la valeur la plus extrême qui RESTE une fois "
    "les valeurs extrêmes exclues — elle peut donc être strictement inférieure au "
    "maximum réel de l'échantillon, ou supérieure à son minimum réel, s'il existe des "
    "valeurs extrêmes au sens de cette règle."
)


def _dessiner_legende_boite(fig, canvas, etat_icones, ax):
    """Petit schéma annoté expliquant l'anatomie d'une boîte à moustaches (mêmes
    couleurs/styles que celle du graphique "Dispersion |dQP| par horizon") — dessiné
    une seule fois à la création de l'onglet, jamais retouché par ax.clear() ni par
    les rafraîchissements (statique, ne dépend d'aucune donnée réelle)."""
    # Données synthétiques choisies pour un espacement régulier entre les 5 repères
    # (quartiles/médiane/moustaches) — un exemple trop resserré ferait se chevaucher
    # les étiquettes de la légende (constaté avec un premier jeu de données au rendu).
    donnees_demo = list(range(1, 21))
    bp = ax.boxplot(
        [donnees_demo], positions=[0], widths=0.5, showfliers=False, patch_artist=True,
        boxprops=dict(facecolor=(0.682, 0.839, 0.945, 0.20), edgecolor="#154360", linewidth=1.2),
        medianprops=dict(color="#C0392B", linewidth=1.8),
        whiskerprops=dict(color="#154360", linewidth=1.2),
        capprops=dict(color="#154360", linewidth=1.2),
    )
    q1 = float(np.percentile(donnees_demo, 25))
    mediane = float(np.median(donnees_demo))
    q3 = float(np.percentile(donnees_demo, 75))
    moustache_bas = bp["whiskers"][0].get_ydata()[1]
    moustache_haut = bp["whiskers"][1].get_ydata()[1]

    def _annoter(y, texte, decalage_texte=0.0):
        ax.annotate(
            texte, xy=(0.26, y), xytext=(0.55, y + decalage_texte),
            fontsize=6.6, va="center", ha="left", color="#333333",
            arrowprops=dict(arrowstyle="-", color="#7B7B7B", lw=0.7,
                             shrinkA=0, shrinkB=2))

    _annoter(moustache_haut, "Maximum\n(hors valeurs\nextrêmes)")
    _annoter(q3, "3ᵉ quartile\n(75 % des crues\nsous ce niveau)")
    _annoter(mediane, "Médiane\n(50 %)")
    _annoter(q1, "1ᵉʳ quartile\n(25 % des crues\nsous ce niveau)")
    _annoter(moustache_bas, "Minimum\n(hors valeurs\nextrêmes)")

    ax.set_xlim(-1.1, 2.9)
    marge = max((moustache_haut - moustache_bas) * 0.35, 1)
    ax.set_ylim(moustache_bas - marge, moustache_haut + marge)
    ax.axis("off")
    ax.set_title("Lecture de la\nboîte à moustaches", fontsize=8, loc="left", pad=10)

    # Icônes "i" cliquables juste après le mot "extrêmes" des entrées Maximum/Minimum
    # (demandé) — explique précisément ce que signifie "hors valeurs extrêmes". Position
    # calculée depuis les VRAIES coordonnées data de chaque annotation (transData ->
    # transFigure), décalée d'environ une demi-hauteur de bloc de texte vers le bas pour
    # viser la 3e ligne ("extrêmes)") plutôt que le centre du bloc — bien plus fiable
    # qu'une fraction d'axe devinée à l'œil (essayé d'abord, imprécis au rendu réel).
    def _position_figure(x_donnee, y_donnee):
        x_disp, y_disp = ax.transData.transform((x_donnee, y_donnee))
        return fig.transFigure.inverted().transform((x_disp, y_disp))

    x_icone_max, y_icone_max = _position_figure(1.35, moustache_haut - 1.3)
    x_icone_min, y_icone_min = _position_figure(1.35, moustache_bas - 1.3)
    _icone_info_axe(fig, canvas, etat_icones, "extremes_max", x_icone_max, y_icone_max,
                     "Valeurs extrêmes (boîte à moustaches)", _TEXTE_VALEURS_EXTREMES)
    _icone_info_axe(fig, canvas, etat_icones, "extremes_min", x_icone_min, y_icone_min,
                     "Valeurs extrêmes (boîte à moustaches)", _TEXTE_VALEURS_EXTREMES)


def _build_synthese(frame, app):
    barre = tk.Frame(frame)
    barre.pack(fill=tk.X, padx=8, pady=6)
    var_statut = tk.StringVar(value="")
    tk.Label(barre, textvariable=var_statut, fg="#555555").pack(side=tk.LEFT)
    bouton_info(barre, "Score composite",
                lambda: explication_score(*_poids_actifs(app)[:2])).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Button(barre, text="Rafraîchir", command=lambda: _rafraichir()).pack(side=tk.RIGHT, padx=4)
    ttk.Button(barre, text="Exporter en Excel…", command=lambda: _exporter()).pack(side=tk.RIGHT)

    corps = tk.Frame(frame)
    corps.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

    # Figure agrandie et dispersion élargie par rapport à la heatmap (demandé) — la
    # heatmap reste compacte (contrainte par le nb d'horizons/seuils), la dispersion
    # profite de l'espace supplémentaire pour rester lisible avec le nuage de points.
    fig = Figure(figsize=(14, 5.2), dpi=100)
    # 3e colonne, étroite, réservée à la légende de lecture de la boîte à moustaches —
    # les 2 graphiques de données sont décalés vers la gauche pour lui laisser la
    # place sans jamais empiéter dessus (demande explicite de l'utilisateur).
    gs = fig.add_gridspec(1, 3, width_ratios=(1, 1.6, 0.42), wspace=0.5)
    ax_heatmap = fig.add_subplot(gs[0, 0])
    ax_dispersion = fig.add_subplot(gs[0, 1])
    ax_legende_boite = fig.add_subplot(gs[0, 2])
    fig.subplots_adjust(bottom=0.2, left=0.05, right=0.98)
    canvas = FigureCanvasTkAgg(fig, master=corps)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    etat_icones = {}
    etat_colorbar = {"cb": None}
    _dessiner_legende_boite(fig, canvas, etat_icones, ax_legende_boite)

    cadre_classement = tk.Frame(frame)
    cadre_classement.pack(fill=tk.X, padx=8, pady=(0, 8))
    tableau = ttk.Treeview(cadre_classement, columns=("horizon", "seuil", "methode", "score", "nb_crues"),
                            show="headings", height=8)
    for col, libelle in (("horizon", "Horizon"), ("seuil", "Seuil C1"), ("methode", "Méthode"),
                         ("score", "Score (0=meilleur)"), ("nb_crues", "Nb crues")):
        tableau.heading(col, text=libelle)
        tableau.column(col, width=120, anchor="center")
    ascenseur_tableau = ttk.Scrollbar(cadre_classement, orient=tk.VERTICAL, command=tableau.yview)
    tableau.configure(yscrollcommand=ascenseur_tableau.set)
    tableau.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur_tableau.pack(side=tk.RIGHT, fill=tk.Y)

    def _exporter():
        chemin = filedialog.asksaveasfilename(
            title="Exporter les résultats en Excel", defaultextension=".xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")])
        if not chemin:
            return
        try:
            export_excel.exporter(chemin, app)
        except Exception as e:
            messagebox.showerror("Export Excel", str(e))
            return
        messagebox.showinfo("Export Excel", f"Export réussi : {chemin}")

    def _rafraichir():
        lignes, erreur = _charger_resultats(app)
        if erreur:
            var_statut.set(erreur)
            return
        lignes_ok = _filtrer_lignes_score(app, [l for l in lignes if l["statut_crue"] == "success"])
        if not lignes_ok:
            var_statut.set("Aucun résultat réussi en base pour l'instant (ou aucune crue "
                            "incluse dans le score, voir \"Crues dans le score\" en haut) — "
                            "lancez une campagne (onglet Campagne).")
            if etat_colorbar["cb"] is not None:  # retirer AVANT clear(), voir plus bas
                etat_colorbar["cb"].remove()
                etat_colorbar["cb"] = None
            ax_heatmap.clear()
            ax_dispersion.clear()
            canvas.draw_idle()
            tableau.delete(*tableau.get_children())
            return

        poids, asymetrie_dtp, libelle_profil = _poids_actifs(app)
        scores = calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp)
        var_statut.set(f"{len(lignes_ok)} résultat(s) réussi(s), {len(scores)} combinaison(s) "
                        f"— pondération : {libelle_profil}.")

        # -- Heatmap horizon x seuil (score moyen, toutes méthodes confondues) --------
        # La colorbar doit être retirée AVANT ax_heatmap.clear() : Colorbar.remove()
        # s'appuie sur l'axes "parent" (ax_heatmap) pour restaurer sa place dans la
        # grille de subplots — une fois cet axes vidé par clear(), remove() plante
        # (AttributeError, constaté en conditions réelles au 2e rafraîchissement).
        if etat_colorbar["cb"] is not None:
            etat_colorbar["cb"].remove()
            etat_colorbar["cb"] = None
        ax_heatmap.clear()
        horizons = sorted({s.horizon for s in scores}, key=_horizon_en_minutes)
        seuils = sorted({s.seuil_c1 for s in scores})
        if horizons and seuils:
            grille = np.full((len(seuils), len(horizons)), np.nan)
            for s in scores:
                if s.score is None:
                    continue
                i, j = seuils.index(s.seuil_c1), horizons.index(s.horizon)
                # Moyenne si plusieurs méthodes partagent la même case (peu lisible sinon)
                grille[i, j] = s.score if np.isnan(grille[i, j]) else (grille[i, j] + s.score) / 2
            im = ax_heatmap.imshow(grille, aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=1)
            ax_heatmap.set_xticks(range(len(horizons)))
            ax_heatmap.set_xticklabels(horizons, rotation=45, ha="right", fontsize=7)
            ax_heatmap.set_yticks(range(len(seuils)))
            ax_heatmap.set_yticklabels([f"{v:.2f}" for v in seuils], fontsize=7)
            ax_heatmap.set_title("Score composite (0=meilleur)", fontsize=9)
            etat_colorbar["cb"] = fig.colorbar(im, ax=ax_heatmap, fraction=0.046, pad=0.04)
            # Position recalculée depuis la vraie bbox de l'axe (pas de coordonnées
            # figure codées en dur) : reste correcte quels que soient les width_ratios
            # de la grille (modifiés depuis pour élargir la dispersion, voir plus haut).
            bbox_hm = ax_heatmap.get_position()
            # Note ajoutée à l'explication du score : une case peut sembler plus foncée
            # (donc meilleure en apparence) que la case encadrée en jaune sans que ce
            # soit une anomalie — question posée par l'utilisateur en conditions
            # réelles. La case est la MOYENNE des méthodes qui partagent cet horizon et
            # ce seuil (voir juste au-dessus), alors que le cadre jaune désigne la
            # MEILLEURE COMBINAISON INDIVIDUELLE (une méthode précise, T ou R) — les
            # deux ne coïncident pas forcément : une case peut être sombre parce que ses
            # 2 méthodes sont toutes les deux correctes en moyenne, sans qu'aucune des
            # deux n'atteigne individuellement le meilleur score de l'ensemble.
            texte_heatmap = (
                explication_score(poids, asymetrie_dtp) + "\n\n"
                "Note sur la heatmap : chaque case est la MOYENNE des méthodes (T et/ou "
                "R) qui partagent cet horizon et ce seuil — le cadre jaune désigne, lui, "
                "la MEILLEURE COMBINAISON INDIVIDUELLE (une seule méthode). Une case peut "
                "donc paraître plus sombre (meilleure en apparence) que la case encadrée "
                "sans contradiction : elle reflète 2 méthodes moyennement bonnes, alors "
                "que le cadre désigne la seule méthode la plus performante prise seule."
            )
            _icone_info_axe(fig, canvas, etat_icones, "heatmap",
                             bbox_hm.x0 + 0.98 * bbox_hm.width, bbox_hm.y1 + 0.025,
                             "Score composite", texte_heatmap)

            # Cadre jaune autour de la case (horizon, seuil) de la meilleure combinaison
            # trouvée (scores est trié meilleur -> moins bon, voir modules.score) — la
            # case affiche une moyenne si plusieurs méthodes s'y superposent, mais
            # repérer la case suffit à situer visuellement la meilleure combinaison.
            meilleur = scores[0] if scores and scores[0].score is not None else None
            if meilleur is not None and meilleur.horizon in horizons and meilleur.seuil_c1 in seuils:
                j = horizons.index(meilleur.horizon)
                i = seuils.index(meilleur.seuil_c1)
                ax_heatmap.add_patch(Rectangle(
                    (j - 0.5, i - 0.5), 1, 1, fill=False, edgecolor="#FFD700",
                    linewidth=2.5, zorder=5))

        # -- Dispersion |dQP| par horizon (scatter + boîtes à moustaches fines) -------
        ax_dispersion.clear()
        positions = [_horizon_en_minutes(h) for h in horizons]
        # Largeur des boîtes = fraction du plus PETIT écart entre 2 horizons voisins
        # (pas de l'étendue totale) : les horizons choisis sont souvent très inégalement
        # espacés (de 1h à plusieurs jours), une largeur basée sur l'étendue globale
        # peut donc rester trop large là où les horizons sont rapprochés et se toucher.
        positions_triees = sorted(positions)
        ecarts = [b - a for a, b in zip(positions_triees, positions_triees[1:]) if b > a]
        largeur_boite = max(min(ecarts) * 0.4, 1) if ecarts else 1
        valeurs_par_horizon = []
        for horizon in horizons:
            valeurs = [abs(l["dqp"]) for l in lignes_ok if l["horizon"] == horizon and l["dqp"] is not None]
            valeurs_par_horizon.append(valeurs)
            xs = [_horizon_en_minutes(horizon)] * len(valeurs)
            # Nuage discret en arrière-plan (alpha faible) : la boîte à moustaches
            # devient l'élément principal, le nuage n'est qu'un repère de densité.
            ax_dispersion.scatter(xs, valeurs, alpha=0.3, s=10, color="#1F618D", zorder=2)
        if positions and any(valeurs_par_horizon):
            # Remplissage très transparent (alpha porté par la couleur RGBA elle-même,
            # pas par le patch entier) pour laisser voir les points du nuage en
            # dessous — seul le contour reste pleinement opaque, sinon la boîte
            # devient illisible en même temps que transparente.
            ax_dispersion.boxplot(
                valeurs_par_horizon, positions=positions, widths=largeur_boite,
                showfliers=False, patch_artist=True, zorder=3,
                boxprops=dict(facecolor=(0.682, 0.839, 0.945, 0.20), edgecolor="#154360", linewidth=1.2),
                medianprops=dict(color="#C0392B", linewidth=1.8),
                whiskerprops=dict(color="#154360", linewidth=1.2),
                capprops=dict(color="#154360", linewidth=1.2),
            )
        ax_dispersion.set_xticks([_horizon_en_minutes(h) for h in horizons])
        ax_dispersion.set_xticklabels(horizons, rotation=45, ha="right", fontsize=7)
        ax_dispersion.set_ylabel("|dQP| (%)", fontsize=8)
        ax_dispersion.set_title("Dispersion |dQP| par horizon", fontsize=9)
        ax_dispersion.grid(True, alpha=0.3)

        canvas.draw_idle()

        tableau.delete(*tableau.get_children())
        for s in scores:  # toutes les combinaisons, pas seulement les 15 meilleures -- l'ascenseur permet de tout parcourir
            tableau.insert("", tk.END, values=(s.horizon, f"{s.seuil_c1:.2f}", s.methode,
                                                f"{s.score:.4f}" if s.score is not None else "—",
                                                s.nb_crues))

    _rafraichir()
    return _rafraichir  # exposé pour que build_tab_dashboard puisse retracer au changement de pondération


# ══════════════════════════════════════════════════════════════════════════════════
# 2. Détail par crue
# ══════════════════════════════════════════════════════════════════════════════════

def _build_detail(frame, app):
    barre, bg = make_section(frame, "Sélection", "bleu")
    r = make_row(barre, bg)
    make_label(r, "Pas de temps :", bg, width=14)
    var_pdt = tk.StringVar()
    combo_pdt = ttk.Combobox(r, textvariable=var_pdt, state="readonly", width=14)
    combo_pdt.pack(side=tk.LEFT, padx=(2, 12))

    make_label(r, "Crue :", bg, width=8)
    var_crue = tk.StringVar()
    combo_crue = ttk.Combobox(r, textvariable=var_crue, state="readonly", width=22)
    combo_crue.pack(side=tk.LEFT, padx=(2, 2))
    ttk.Button(r, text="◀", width=3,
               command=lambda: _changer_crue(-1)).pack(side=tk.LEFT)
    ttk.Button(r, text="▶", width=3,
               command=lambda: _changer_crue(1)).pack(side=tk.LEFT, padx=(0, 12))

    r2 = make_row(barre, bg)
    make_label(r2, "Combinaison(s) :", bg, width=14)
    liste_combis = tk.Listbox(r2, selectmode=tk.EXTENDED, height=5, width=34,
                               exportselection=False)
    liste_combis.pack(side=tk.LEFT, padx=(2, 8))
    cadre_boutons_combi = tk.Frame(r2, bg=bg)
    cadre_boutons_combi.pack(side=tk.LEFT, padx=(0, 12))
    ttk.Button(cadre_boutons_combi, text="Toutes",
               command=lambda: _selectionner_toutes(True)).pack(fill=tk.X, pady=1)
    ttk.Button(cadre_boutons_combi, text="Aucune",
               command=lambda: _selectionner_toutes(False)).pack(fill=tk.X, pady=1)
    ttk.Button(r2, text="Tracer", command=lambda: _tracer()).pack(side=tk.LEFT, anchor="n")

    var_indicateurs = tk.StringVar(value="")
    tk.Label(frame, textvariable=var_indicateurs, font=("TkDefaultFont", 9, "bold"),
             wraplength=900, justify=tk.LEFT).pack(anchor="w", padx=10, pady=(4, 0))

    fig = Figure(figsize=(9, 4), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    # Hyétogramme (pluie de bassin) en axe jumeau, inversé, cantonné au quart supérieur
    # du graphique — convention hydrologique classique (histogramme de pluie qui
    # "tombe" depuis le haut, débit en dessous). set_zorder + patch invisible sur ax
    # font passer les courbes de débit AU-DESSUS des barres de pluie (sinon l'axe créé
    # en second, ax_pluie, s'affiche par défaut par-dessus).
    ax_pluie = ax.twinx()
    ax_pluie.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    etat_y_max = {"global": None}  # recalculé par _rafraichir_crues (pas à chaque tracé)
    # Courbes actuellement tracées (Q observé + une par combinaison sélectionnée), pour
    # le survol à la souris : identifier quelle courbe est pointée (demandé, la légende
    # devenant vite très fournie avec plusieurs combinaisons superposées) — voir
    # _survol_courbes ci-dessous, même principe que le tooltip Q d'OPALE v2 mais
    # étendu à PLUSIEURS courbes (recherche de la plus proche du curseur, en pixels).
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

    def _calculer_y_max_global(paths, code_pdt):
        """Qmax le plus élevé (observé ou simulé) toutes crues confondues pour ce pas
        de temps, +10% — donne une échelle Y COMMUNE à toutes les crues (demandé
        explicitement) plutôt qu'une échelle qui se réajuste à chaque changement de
        crue, pour pouvoir comparer directement l'amplitude d'un épisode à l'autre.
        Best-effort : une crue dont la série observée est illisible est simplement
        ignorée pour ce calcul, jamais une erreur bloquante."""
        valeurs = []
        try:
            evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
        except (FileNotFoundError, CriteresPerfError):
            evenements = []
        for evt in evenements:
            chemin = os.path.join(paths.evenements_dir(code_pdt),
                                   f"{paths.code_site}-EV{evt.num_evt:04d}.DAT")
            try:
                serie = parse_evenement_serie(chemin)
            except (FileNotFoundError, CriteresPerfError):
                continue
            valeurs.extend(p[2] for p in serie if p[2] is not None)
        try:
            with results_store.db_session() as conn:
                max_sim = results_store.max_debit_simule(conn)
            if max_sim is not None:
                valeurs.append(max_sim)
        except Exception:
            pass
        return max(valeurs) * 1.1 if valeurs else None

    # ── Récapitulatif max/horodatage par courbe tracée ────────────────────────────
    inn_max, bg_max = make_section(frame, "Maximum de chaque courbe tracée", "gris")
    cadre_tableau_max = tk.Frame(inn_max, bg=bg_max)
    cadre_tableau_max.pack(fill=tk.BOTH, expand=True)
    tableau_max = ttk.Treeview(
        cadre_tableau_max,
        columns=("courbe", "max", "horodatage", "dqp", "dt"),
        show="headings", height=6)
    for col, libelle, largeur in (
        ("courbe", "Courbe", 240), ("max", "Max (m³/s)", 100),
        ("horodatage", "Horodatage du max", 140),
        ("dqp", "dQP vs observé (%)", 130), ("dt", "dT vs observé (pdt)", 130),
    ):
        tableau_max.heading(col, text=libelle)
        tableau_max.column(col, width=largeur, anchor="center" if col != "courbe" else "w")
    ascenseur_max = ttk.Scrollbar(cadre_tableau_max, orient=tk.VERTICAL, command=tableau_max.yview)
    tableau_max.configure(yscrollcommand=ascenseur_max.set)
    tableau_max.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur_max.pack(side=tk.RIGHT, fill=tk.Y)
    # Repère visuel de la courbe simulée la plus proche de l'observé (demandé) : fond
    # jaune + gras sur toute la ligne — ttk.Treeview ne permet de colorer/styler qu'une
    # ligne entière via un tag, pas une cellule isolée.
    tableau_max.tag_configure("meilleure_dqp", background="#FFF59D", font=("TkDefaultFont", 9, "bold"))
    tableau_max.tag_configure("meilleure_dt", background="#FFF59D", font=("TkDefaultFont", 9, "bold"))

    def _max_et_horodatage(points_xy):
        """points_xy : liste de (datetime, valeur). Retourne (max, date_du_max) en
        ignorant les valeurs None, ou (None, None) si aucune valeur exploitable."""
        valides = [(d, v) for d, v in points_xy if v is not None]
        if not valides:
            return None, None
        date_max, valeur_max = max(valides, key=lambda dv: dv[1])
        return valeur_max, date_max

    def _combinaisons_disponibles_pour_crue(crue_iso):
        """Combinaisons dont CETTE crue précise a réussi — inutile de proposer une
        combinaison qui n'a jamais été rejouée pour la date sélectionnée."""
        if not crue_iso:
            return []
        lignes, _ = _charger_resultats(app)
        vues = sorted({(l["horizon"], l["seuil_c1"], l["methode"], l["combinaison_id"])
                       for l in lignes
                       if l["statut_crue"] == "success" and l["crue_date"] == crue_iso})
        return vues

    def _crue_iso_courante():
        """Résout le libellé actuellement affiché dans le menu déroulant ("#12 -
        13/10/2018") vers la date ISO réelle (utilisée pour toutes les requêtes en
        base) — voir combo_crue._valeurs, construit par _rafraichir_crues."""
        libelles = list(combo_crue["values"])
        valeurs = getattr(combo_crue, "_valeurs", [])
        if var_crue.get() not in libelles or len(valeurs) != len(libelles):
            return None
        return valeurs[libelles.index(var_crue.get())][1]

    def _rafraichir_crues(*_evt):
        lignes, _ = _charger_resultats(app)
        dates_disponibles = {l["crue_date"] for l in lignes if l["statut_crue"] == "success"}

        # Tri par numéro d'événement (#N), pas par date — demandé explicitement.
        # Le numéro vient de CRITERES_PERF.DAT (results_store ne connaît que la date) :
        # toute crue en base mais absente de ce fichier (pas de temps différent au
        # moment du rejeu, etc.) reste affichée, juste sans numéro ("? - date"), plutôt
        # que d'être masquée silencieusement.
        paths = _construire_grp_paths(app)
        code_pdt = _pas_de_temps_courant()
        entrees = []
        if paths is not None and code_pdt:
            try:
                evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
                for e in evenements:
                    iso = e.date_deb.isoformat()
                    if iso in dates_disponibles:
                        entrees.append((e.num_evt, iso))
            except (FileNotFoundError, CriteresPerfError):
                pass
        isos_numerotes = {iso for _n, iso in entrees}
        for iso in sorted(dates_disponibles - isos_numerotes):
            entrees.append((None, iso))
        entrees.sort(key=lambda t: (t[0] is None, t[0]))

        libelles = []
        for num_evt, iso in entrees:
            d = datetime.fromisoformat(iso)
            prefixe = f"#{num_evt}" if num_evt is not None else "?"
            libelles.append(f"{prefixe} - {d:%d/%m/%Y}")
        combo_crue["values"] = libelles
        combo_crue._valeurs = entrees
        if libelles and var_crue.get() not in libelles:
            var_crue.set(libelles[0])

        etat_y_max["global"] = (_calculer_y_max_global(paths, code_pdt)
                                  if paths is not None and code_pdt else None)
        _rafraichir_combis()

    def _changer_crue(delta):
        """Passe à la crue précédente/suivante (ordre chronologique de la liste
        déroulante) — évite d'avoir à rouvrir le menu déroulant pour parcourir les
        épisodes un par un. Conserve la même sélection de combinaisons (par identité
        horizon/seuil/méthode, pas par position dans la liste — qui peut changer d'une
        crue à l'autre) et retrace automatiquement, sans avoir à recliquer sur Tracer."""
        dates = list(combo_crue["values"])
        if not dates or var_crue.get() not in dates:
            return
        nouvel_index = list(dates).index(var_crue.get()) + delta
        if not (0 <= nouvel_index < len(dates)):
            return  # déjà au premier/dernier épisode, rien à faire
        var_crue.set(dates[nouvel_index])
        _rafraichir_combis()
        _tracer()

    def _rafraichir_combis(*_evt, garder_selection=True):
        # Mémorise la sélection actuelle PAR IDENTITÉ (horizon, seuil, méthode) avant de
        # reconstruire la liste — la position dans la liste n'est pas stable d'une crue à
        # l'autre (certaines combinaisons peuvent ne pas avoir réussi pour la nouvelle
        # crue), donc se souvenir d'un simple index sélectionnerait la mauvaise ligne.
        identites_gardees = set()
        if garder_selection:
            combis_avant = getattr(liste_combis, "_valeurs", [])
            identites_gardees = {(combis_avant[i][0], combis_avant[i][1], combis_avant[i][2])
                                  for i in liste_combis.curselection()}

        combis = _combinaisons_disponibles_pour_crue(_crue_iso_courante())
        liste_combis.delete(0, tk.END)
        for h, s, m, _cid in combis:
            liste_combis.insert(tk.END, f"{h} / seuil {s:.2f} / {m}")
        liste_combis._valeurs = combis

        indices_a_restaurer = [i for i, (h, s, m, _cid) in enumerate(combis)
                                if (h, s, m) in identites_gardees]
        if indices_a_restaurer:
            for i in indices_a_restaurer:
                liste_combis.selection_set(i)
        elif combis:
            liste_combis.selection_set(0)  # repli : au moins une courbe simulée par défaut

    def _selectionner_toutes(valeur):
        if valeur:
            liste_combis.selection_set(0, tk.END)
        else:
            liste_combis.selection_clear(0, tk.END)

    def _pas_de_temps_courant():
        for p in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if p["libelle"] == var_pdt.get():
                return p["code"]
        return None

    def _tracer():
        ax.clear()
        ax_pluie.clear()
        # ax.clear() recrée le patch de fond (le rend visible par défaut) : à refaire à
        # chaque tracé, pas seulement à la création, sinon les barres de pluie
        # repassent au-dessus des courbes de débit dès le 2e tracé.
        ax.patch.set_visible(False)
        etat_courbes["liste"] = []
        etat_survol["artist"] = ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points", fontsize=7.5,
            zorder=25, visible=False,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#555555", linewidth=1.0, alpha=0.92),
        )
        tableau_max.delete(*tableau_max.get_children())
        var_indicateurs.set("")
        paths = _construire_grp_paths(app)
        code_pdt = _pas_de_temps_courant()
        crue_iso = _crue_iso_courante()
        if paths is None or not code_pdt or not crue_iso:
            canvas.draw_idle()
            return

        # Série observée (source déjà vérifiée, voir modules.criteres_perf) : commune à
        # toutes les combinaisons sélectionnées (ne dépend que de la crue), tracée une
        # seule fois. On cherche l'événement dont la date de début correspond à la crue.
        try:
            evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
        except (FileNotFoundError, CriteresPerfError) as e:
            var_indicateurs.set(f"Impossible de charger les événements : {e}")
            canvas.draw_idle()
            return

        evt = next((e for e in evenements if e.date_deb.isoformat() == crue_iso), None)
        if evt is None:
            var_indicateurs.set("Crue introuvable dans CRITERES_PERF.DAT pour ce pas de temps.")
            canvas.draw_idle()
            return

        num_evt_str = f"{evt.num_evt:04d}"
        chemin_serie = os.path.join(paths.evenements_dir(code_pdt),
                                     f"{paths.code_site}-EV{num_evt_str}.DAT")
        try:
            serie = parse_evenement_serie(chemin_serie)
        except (FileNotFoundError, CriteresPerfError) as e:
            var_indicateurs.set(f"Série observée indisponible : {e}")
            serie = []

        # dQP/dT de chaque courbe RELATIFS au pic observé (même principe que les
        # indicateurs dQP/dTP de campagne, mais calculés ici directement sur les
        # séries tracées) — nécessite la durée du pas de temps en minutes pour
        # convertir un écart d'horodatage en nombre de pas de temps ; le format du
        # code pas de temps ("ddJhhHmmM") est le même que celui des horizons, donc
        # _horizon_en_minutes s'applique tel quel.
        pas_de_temps_minutes = _horizon_en_minutes(code_pdt) or None
        valeur_max_obs, date_max_obs = None, None

        def _dqp_dt_vs_obs(valeur_max, date_max):
            """Retourne (texte_dqp, texte_dt, valeur_dqp, valeur_dt) — les valeurs
            numériques brutes (en plus du texte déjà formaté) servent à repérer la
            courbe la plus proche de l'observé (voir tag "meilleure_*" ci-dessous)."""
            if valeur_max_obs is None or date_max_obs is None:
                return "—", "—", None, None
            dqp = (((valeur_max - valeur_max_obs) / valeur_max_obs) * 100
                   if valeur_max_obs != 0 else None)
            dt = ((date_max - date_max_obs).total_seconds() / 60 / pas_de_temps_minutes
                  if pas_de_temps_minutes else None)
            return (f"{dqp:+.1f}" if dqp is not None else "—",
                    f"{dt:+.1f}" if dt is not None else "—", dqp, dt)

        toutes_valeurs = []
        if serie:
            points_obs = [(p[0], p[2]) for p in serie]
            ligne_obs, = ax.plot([p[0] for p in points_obs], [p[1] for p in points_obs],
                                  color=_COULEUR_OBS, lw=1.8, label="Q observé")
            etat_courbes["liste"].append({
                "label": "Q observé", "couleur": _COULEUR_OBS,
                "x": [mdates.date2num(d) for d, _v in points_obs],
                "y": [v for _d, v in points_obs],
            })
            toutes_valeurs.extend(v for _d, v in points_obs if v is not None)
            valeur_max, date_max = _max_et_horodatage(points_obs)
            if valeur_max is not None:
                valeur_max_obs, date_max_obs = valeur_max, date_max
                tableau_max.insert("", tk.END, values=(
                    "Q observé", f"{valeur_max:.1f}", f"{date_max:%d/%m/%Y %H:%M}", "0.0", "0"))
                # Annotation directement sur le graphique (même principe que OPALE v2 :
                # point marqué + valeur/horodatage dans un encart), en plus de la ligne
                # déjà présente dans le tableau récapitulatif ci-dessous.
                ax.plot(date_max, valeur_max, "o", color=_COULEUR_OBS, markersize=5, zorder=5)
                ax.annotate(
                    f"Q max obs\n{valeur_max:.1f} m³/s\n{date_max:%d/%m/%Y %H:%M}",
                    xy=(date_max, valeur_max), xytext=(8, 10), textcoords="offset points",
                    fontsize=7.5, color=_COULEUR_OBS, fontweight="bold", linespacing=1.3,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=_COULEUR_OBS, alpha=0.85),
                )

            # -- Hyétogramme (pluie de bassin) -----------------------------------------
            # <code_site>-EVxxxx.DAT donne Pobs déjà en mm par pas de temps — utilisée
            # SANS conversion. L'en-tête du fichier ("Pobs(mm/h)") est trompeur : une
            # interprétation en intensité mm/h avait d'abord été tentée, mais donnait
            # des cumuls trop faibles comparés aux cumuls réellement observés pour de
            # vrais épisodes majeurs (ex. 33mm calculés contre 150-300mm connus pour la
            # crue historique de l'Aude d'octobre 2018) — confirmé par l'utilisateur.
            # L'intervalle réel (mesuré sur les horodatages) ne sert plus qu'à calibrer
            # la LARGEUR des barres, plus fiable qu'un code pas de temps qui pourrait ne
            # pas correspondre.
            if len(serie) >= 2:
                intervalle_minutes = (serie[1][0] - serie[0][0]).total_seconds() / 60
                if intervalle_minutes > 0:
                    dates_pluie = [p[0] for p in serie]
                    profondeurs = [p[1] for p in serie]
                    largeur_jours = (intervalle_minutes / (24 * 60)) * 0.8
                    ax_pluie.bar(dates_pluie, profondeurs, width=largeur_jours,
                                 color="#5DADE2", edgecolor="#2E86AB", linewidth=0.3,
                                 alpha=0.75, zorder=1, label="Pluie de bassin")
                    plafond = max(max(profondeurs, default=0) * 4, 1)
                    ax_pluie.set_ylim(plafond, 0)  # inversé : la pluie "tombe" depuis le haut
                    # set_label_position("right") impératif ici : ax_pluie.clear() (en
                    # tête de _tracer) réinitialise la position du label à "left" à
                    # CHAQUE rafraîchissement malgré twinx() — sans ce rappel explicite,
                    # le titre se superposait à celui de l'axe Débit (constaté au rendu
                    # réel, alors que les graduations, elles, restaient bien à droite).
                    # labelpad augmenté en plus : par défaut il restait collé aux
                    # graduations plutôt que nettement à droite — signalé par l'utilisateur.
                    ax_pluie.yaxis.set_label_position("right")
                    ax_pluie.set_ylabel("Pluie (mm / pas de temps)", fontsize=7.5,
                                         color="#2E86AB", labelpad=14)
                    ax_pluie.tick_params(axis="y", labelsize=7, colors="#2E86AB")

        # Une ou plusieurs séries simulées archivées (voir modules.run_orchestrator —
        # archivage à chaque rejeu, car Sorties/ n'expose que le DERNIER rejeu effectué)
        # superposées sur le même graphique, une couleur distincte par combinaison —
        # légende mise à jour en conséquence pour distinguer qui est qui.
        combis = getattr(liste_combis, "_valeurs", [])
        selection = liste_combis.curselection()
        combis_selectionnees = [combis[i] for i in selection] if combis else []
        lignes_resume = []  # (item_id, |dqp|, |dt|) — pour repérer la courbe la plus proche de l'observé
        with results_store.db_session() as conn:
            for i, (h, s, m, combinaison_id) in enumerate(combis_selectionnees):
                serie_sim = results_store.charger_serie(conn, combinaison_id, crue_iso, "sim")
                if not serie_sim:
                    continue
                points_sim = [(p[0], p[1]) for p in serie_sim]
                couleur = _PALETTE_COURBES[i % len(_PALETTE_COURBES)]
                libelle = f"Sim {h}/{s:.2f}/{m}"
                ax.plot([p[0] for p in points_sim], [p[1] for p in points_sim],
                         color=couleur, lw=1.3, ls="--", label=libelle)
                etat_courbes["liste"].append({
                    "label": libelle, "couleur": couleur,
                    "x": [mdates.date2num(p[0]) for p in points_sim],
                    "y": [p[1] for p in points_sim],
                })
                toutes_valeurs.extend(v for _d, v in points_sim if v is not None)
                valeur_max, date_max = _max_et_horodatage(points_sim)
                if valeur_max is not None:
                    dqp_txt, dt_txt, dqp_val, dt_val = _dqp_dt_vs_obs(valeur_max, date_max)
                    item_id = tableau_max.insert("", tk.END, values=(
                        libelle, f"{valeur_max:.1f}", f"{date_max:%d/%m/%Y %H:%M}",
                        dqp_txt, dt_txt))
                    lignes_resume.append((item_id, abs(dqp_val) if dqp_val is not None else None,
                                           abs(dt_val) if dt_val is not None else None))

        # Repère visuel (fond jaune + gras, toute la ligne) sur la courbe SIMULÉE la
        # plus proche de l'observé — séparément pour dQP et dT, qui peuvent désigner 2
        # courbes différentes (une combinaison peut avoir le meilleur pic en débit sans
        # avoir le meilleur calage temporel, et inversement). Q observé exclu de la
        # comparaison (son dQP/dT est toujours 0 par construction, pas un vrai résultat).
        candidats_dqp = [(iid, v) for iid, v, _dt in lignes_resume if v is not None]
        if candidats_dqp:
            meilleur_id, _ = min(candidats_dqp, key=lambda t: t[1])
            tableau_max.item(meilleur_id, tags=("meilleure_dqp",))
        candidats_dt = [(iid, v) for iid, _dqp, v in lignes_resume if v is not None]
        if candidats_dt:
            meilleur_id, _ = min(candidats_dt, key=lambda t: t[1])
            tags_existants = tableau_max.item(meilleur_id, "tags")
            tableau_max.item(meilleur_id, tags=tuple(tags_existants) + ("meilleure_dt",))

        # Les 6 seuils de vigilance en débit (jaune/orange/rouge + leurs zones de
        # transition ZT) — même code couleur que l'onglet Configuration (une couleur par
        # niveau, ZT et seuil principal partagent la teinte), différenciés par le style
        # de trait (pointillé pour la ZT, plein pour le seuil principal).
        seuils = app.config_data.get("seuils_q", {})
        # Échelle Y COMMUNE à toutes les crues (Qmax observé+simulé de l'ensemble des
        # crues, +10%, calculé une fois par _rafraichir_crues) plutôt que réajustée à
        # chaque crue affichée — demandé explicitement pour comparer directement
        # l'amplitude d'un épisode à l'autre. Repli sur le max de CETTE crue si le
        # calcul global a échoué (ex. aucune série simulée archivée pour l'instant).
        y_max = etat_y_max["global"] or max(toutes_valeurs, default=None)
        if y_max is not None:
            ax.set_ylim(0, y_max)
        for cle, libelle, couleur in LIBELLES_SEUILS_Q:
            val = seuils.get(cle)
            if val is None:
                continue
            est_zt = cle.startswith("zt_")
            ax.axhline(val, color=couleur, lw=1.0 if est_zt else 1.3,
                       ls=":" if est_zt else "-", alpha=0.85)
            if y_max is None or val <= y_max * 1.3:
                ax.text(0.002, val, f" {libelle} {val:.0f} m³/s", va="bottom", fontsize=6.5,
                        color=couleur, transform=ax.get_yaxis_transform())

        ax.set_ylabel("Débit (m³/s)")
        ax.grid(True, alpha=0.3)
        # Légende sortie du graphique, ancrée à droite des axes (bbox_to_anchor avec un
        # x > 1) pour ne jamais recouvrir les courbes tracées — la marge de figure à
        # droite est élargie en conséquence (l'étiquette de l'axe pluie, à droite lui
        # aussi, y a maintenant sa place) pour que légende ET libellé restent visibles.
        # Fusionnée avec la pluie (sur ax_pluie, donc absente de ax.legend() par défaut).
        fig.subplots_adjust(right=0.70)
        lignes_ax, labels_ax = ax.get_legend_handles_labels()
        lignes_pluie, labels_pluie = ax_pluie.get_legend_handles_labels()
        ax.legend(lignes_ax + lignes_pluie, labels_ax + labels_pluie,
                  loc="center left", bbox_to_anchor=(1.18, 0.5), fontsize=7.5)
        fig.autofmt_xdate()
        canvas.draw_idle()

        texte = (
            f"Crue #{evt.num_evt} ({evt.date_deb:%d/%m/%Y %H:%M}) — configuration en place : "
            f"dQP {evt.dqp}%  dTP {evt.dtp}  VE {evt.ve}%  KGE {evt.kge}"
            + ("  ⚠ suspect" if evt.suspects else "")
        )
        if combis_selectionnees and not toutes_valeurs:
            texte += "  —  Aucune série simulée disponible pour la/les combinaison(s) sélectionnée(s)."
        elif not combis_selectionnees:
            texte += "  —  Aucune combinaison sélectionnée dans la liste (Q observé seul affiché)."
        var_indicateurs.set(texte)

    combo_pdt.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_crues())
    combo_crue.bind("<<ComboboxSelected>>", lambda *_: (_rafraichir_combis(), _tracer()))

    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
    combo_pdt["values"] = [p["libelle"] for p in pdt_list]
    if pdt_list:
        var_pdt.set(pdt_list[0]["libelle"])
    _rafraichir_crues()


# ══════════════════════════════════════════════════════════════════════════════════
# 3. Sensibilité au seuil de calage
# ══════════════════════════════════════════════════════════════════════════════════

def _build_sensibilite(frame, app):
    barre, bg = make_section(frame, "Sélection", "ocre")
    r = make_row(barre, bg)
    make_label(r, "Horizon(s) :", bg, width=10)
    liste_horizons = tk.Listbox(r, selectmode=tk.EXTENDED, height=5, width=16,
                                 exportselection=False)
    liste_horizons.pack(side=tk.LEFT, padx=(2, 8))
    cadre_boutons_horizon = tk.Frame(r, bg=bg)
    cadre_boutons_horizon.pack(side=tk.LEFT, padx=(0, 12))
    ttk.Button(cadre_boutons_horizon, text="Tous",
               command=lambda: _selectionner_tous(True)).pack(fill=tk.X, pady=1)
    ttk.Button(cadre_boutons_horizon, text="Aucun",
               command=lambda: _selectionner_tous(False)).pack(fill=tk.X, pady=1)

    make_label(r, "Méthode(s) :", bg, width=10)
    liste_methodes = tk.Listbox(r, selectmode=tk.EXTENDED, height=2, width=4,
                                 exportselection=False)
    liste_methodes.pack(side=tk.LEFT, padx=(2, 4))
    cadre_boutons_methode = tk.Frame(r, bg=bg)
    cadre_boutons_methode.pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(cadre_boutons_methode, text="Les 2",
               command=lambda: _selectionner_tous_methodes(True)).pack(fill=tk.X, pady=1)
    ttk.Button(cadre_boutons_methode, text="Aucune",
               command=lambda: _selectionner_tous_methodes(False)).pack(fill=tk.X, pady=1)
    tk.Label(r, bg=bg, font=("TkDefaultFont", 7, "italic"), fg="#555555",
             text="T : Tangara   ·   R : RNA (Réseaux de\nNeurones Artificiels)",
             justify=tk.LEFT).pack(side=tk.LEFT, padx=(0, 12))
    # Centré verticalement par défaut (pack sans anchor="n") et décalé à droite
    # (padx gauche) pour se démarquer visuellement du reste de la ligne — l'icône
    # d'explication du score, redondante avec celle désormais posée directement sur le
    # graphique (voir _icone_info_axe ci-dessous), a été retirée d'ici. Le tracé se
    # déclenche aussi automatiquement à chaque changement de sélection (horizon ou
    # méthode) — ce bouton reste utile pour forcer un nouveau tracé sans changer la
    # sélection (ex. après une nouvelle campagne).
    ttk.Button(r, text="Tracer", command=lambda: _tracer()).pack(side=tk.LEFT, padx=(20, 0))

    fig = Figure(figsize=(9, 4), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    etat_icones = {}

    _LIGNE_PAR_METHODE = {"T": "-", "R": "--"}  # trait plein = Tangara, pointillé = RNA

    def _rafraichir_listes():
        lignes, _ = _charger_resultats(app)
        horizons = sorted({l["horizon"] for l in lignes}, key=_horizon_en_minutes)
        methodes = sorted({l["methode"] for l in lignes})
        liste_horizons.delete(0, tk.END)
        for h in horizons:
            liste_horizons.insert(tk.END, h)
        if horizons:
            liste_horizons.selection_set(0)  # un seul horizon par défaut, pour rester lisible
        liste_methodes.delete(0, tk.END)
        for m in methodes:
            liste_methodes.insert(tk.END, m)
        if methodes:
            liste_methodes.selection_set(0, tk.END)  # les 2 méthodes par défaut : montre d'emblée la comparaison T/R

    def _selectionner_tous(valeur):
        if valeur:
            liste_horizons.selection_set(0, tk.END)
        else:
            liste_horizons.selection_clear(0, tk.END)
        _tracer()

    def _selectionner_tous_methodes(valeur):
        if valeur:
            liste_methodes.selection_set(0, tk.END)
        else:
            liste_methodes.selection_clear(0, tk.END)
        _tracer()

    def _tracer():
        ax.clear()
        lignes, erreur = _charger_resultats(app)
        if erreur:
            canvas.draw_idle()
            return
        horizons_selectionnes = [liste_horizons.get(i) for i in liste_horizons.curselection()]
        methodes_selectionnees = [liste_methodes.get(i) for i in liste_methodes.curselection()]
        if not horizons_selectionnes or not methodes_selectionnees:
            canvas.draw_idle()
            return

        lignes_ok = _filtrer_lignes_score(app, [
            l for l in lignes if l["statut_crue"] == "success"
            and l["horizon"] in horizons_selectionnes and l["methode"] in methodes_selectionnees])
        # Un seul appel à calculer_scores sur l'ensemble horizons x méthodes sélectionné :
        # la normalisation min-max du score (voir modules.score) porte alors sur ce même
        # ensemble affiché, donc les courbes superposées restent comparables entre elles
        # (les calculer séparément donnerait à chacune son propre 0/1, faussant la
        # comparaison visuelle).
        poids, asymetrie_dtp, _libelle_profil = _poids_actifs(app)
        scores = calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp)
        if not scores:
            canvas.draw_idle()
            return

        # Couleur = horizon (constante entre les 2 méthodes d'un même horizon, pour les
        # associer visuellement), style de trait = méthode (plein Tangara, pointillé
        # RNA) — permet de comparer aussi bien tous les horizons pour une méthode fixée
        # que les 2 méthodes pour un horizon fixé, sur le même graphique.
        for i, horizon in enumerate(horizons_selectionnes):
            couleur = _PALETTE_COURBES[i % len(_PALETTE_COURBES)]
            for methode in methodes_selectionnees:
                scores_hm = sorted((s for s in scores if s.horizon == horizon and s.methode == methode),
                                    key=lambda s: s.seuil_c1)
                if not scores_hm:
                    continue
                ax.plot([s.seuil_c1 for s in scores_hm], [s.score for s in scores_hm],
                        marker="o", color=couleur, ls=_LIGNE_PAR_METHODE.get(methode, "-"),
                        label=f"{horizon} ({methode})")

        nb_courbes = len(horizons_selectionnes) * len(methodes_selectionnees)
        ax.set_xlabel("Seuil de calage SeuilC1 (m³/s)")
        ax.set_ylabel("Score composite (0=meilleur)")
        ax.grid(True, alpha=0.3)
        # handlelength augmenté : par défaut, le segment de ligne dans la légende est
        # trop court à cette taille de police pour distinguer un pointillé d'un trait
        # plein (on ne voit qu'un ou deux tirets, visuellement proches d'un trait
        # continu) — signalé par l'utilisateur.
        ax.legend(loc="best", fontsize=7.5, ncol=2 if nb_courbes > 5 else 1, handlelength=3.5)
        _icone_info_axe(fig, canvas, etat_icones, "y", 0.06, 0.88,
                         "Score composite", explication_score(poids, asymetrie_dtp))
        canvas.draw_idle()

    # Retrace automatiquement dès que la sélection change (horizon ou méthode) — plus
    # besoin de recliquer sur Tracer, "<<ListboxSelect>>" se déclenche aussi bien pour
    # un clic simple que pour une sélection multiple au glisser/Ctrl-clic.
    liste_horizons.bind("<<ListboxSelect>>", lambda *_: _tracer())
    liste_methodes.bind("<<ListboxSelect>>", lambda *_: _tracer())

    _rafraichir_listes()
    _tracer()
    return _tracer  # exposé pour que build_tab_dashboard puisse retracer au changement de pondération


# ══════════════════════════════════════════════════════════════════════════════════
# 4. Vue 3D — meilleur score en fonction de l'horizon, du seuil et de la méthode
# ══════════════════════════════════════════════════════════════════════════════════

_MARQUEURS_METHODE = {"T": "o", "R": "h"}  # rond / hexagone — formes arrondies plutôt qu'un triangle anguleux


def _build_vue3d(frame, app):
    barre = tk.Frame(frame)
    barre.pack(fill=tk.X, padx=8, pady=6)
    var_statut = tk.StringVar(value="")
    tk.Label(barre, textvariable=var_statut, fg="#555555").pack(side=tk.LEFT)
    bouton_info(barre, "Score composite",
                lambda: explication_score(*_poids_actifs(app)[:2])).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Button(barre, text="Rafraîchir", command=lambda: _rafraichir()).pack(side=tk.RIGHT)

    tk.Label(frame, font=("TkDefaultFont", 8, "italic"), fg="#555555",
             text="Clic-glisser dans le graphique pour tourner la vue en 3D.").pack(
        anchor="w", padx=10)

    var_meilleure = tk.StringVar(value="")
    tk.Label(frame, textvariable=var_meilleure, font=("TkDefaultFont", 9, "bold")).pack(
        anchor="w", padx=10, pady=(2, 0))

    # Deux vues complémentaires plutôt qu'une seule 3D : la perspective/rotation d'un
    # nuage de points 3D rend l'ŒIL peu fiable pour juger l'écart réel entre la
    # meilleure combinaison et les autres (deux points proches en apparence peuvent
    # être très éloignés en profondeur, et inversement) — signalé par l'utilisateur.
    # Le classement 2D à droite (barres triées, écart au meilleur score) donne cette
    # information sans ambiguïté, la 3D restant utile pour explorer la structure
    # d'ensemble (horizon x seuil x méthode).
    fig = Figure(figsize=(13, 5.5), dpi=100)
    # Classement légèrement resserré et décalé à droite (width_ratios) pour laisser le
    # maximum de place au nuage 3D, qui reste le graphique principal de cette vue.
    gs = fig.add_gridspec(1, 2, width_ratios=(1.25, 0.85), wspace=0.38)
    ax = fig.add_subplot(gs[0, 0], projection="3d")
    ax_classement = fig.add_subplot(gs[0, 1])
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    etat_icones = {}
    etat_colorbar = {"cb": None}

    def _rafraichir():
        # La colorbar doit être retirée AVANT ax.clear() : Colorbar.remove() s'appuie
        # sur l'axes "parent" pour restaurer sa place dans la grille de subplots — une
        # fois cet axes vidé par clear(), remove() plante (AttributeError, constaté en
        # conditions réelles au 2e rafraîchissement). ax.clear() ne supprime de toute
        # façon pas la colorbar (Axes à part, ajoutée à la figure) : sans ce retrait
        # explicite, chaque rafraîchissement en empilait une nouvelle (constaté aussi :
        # 2 échelles de couleur identiques côte à côte).
        if etat_colorbar["cb"] is not None:
            etat_colorbar["cb"].remove()
            etat_colorbar["cb"] = None
        ax.clear()
        ax_classement.clear()
        # Rendu "doux" plutôt que la grille 3D par défaut de matplotlib (panneaux
        # blancs, arêtes noires nettes) — demandé explicitement par l'utilisateur
        # ("plus douce et arrondie"). ax.clear() réinitialise ces réglages à chaque
        # rafraîchissement, donc à refaire ici plutôt qu'une seule fois à la création.
        for axe in (ax.xaxis, ax.yaxis, ax.zaxis):
            axe.pane.set_facecolor((0.97, 0.97, 0.99, 0.6))
            axe.pane.set_edgecolor((0.85, 0.85, 0.90, 0.5))
            axe._axinfo["grid"]["color"] = (0.85, 0.85, 0.90, 0.4)
            axe._axinfo["grid"]["linewidth"] = 0.6
        ax.view_init(elev=20, azim=-55)
        lignes, erreur = _charger_resultats(app)
        if erreur:
            var_statut.set(erreur)
            var_meilleure.set("")
            canvas.draw_idle()
            return
        lignes_ok = _filtrer_lignes_score(app, [l for l in lignes if l["statut_crue"] == "success"])
        if not lignes_ok:
            var_statut.set("Aucun résultat réussi en base pour l'instant (ou aucune crue "
                            "incluse dans le score, voir \"Crues dans le score\" en haut) — "
                            "lancez une campagne (onglet Campagne).")
            var_meilleure.set("")
            canvas.draw_idle()
            return

        # Un seul appel à calculer_scores sur TOUS les résultats réussis : normalisation
        # cohérente avec la Vue synthèse (même score, même échelle 0=meilleur/1=pire).
        poids, asymetrie_dtp, _libelle_profil = _poids_actifs(app)
        scores = [s for s in calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp)
                  if s.score is not None]
        var_statut.set(f"{len(scores)} combinaison(s) avec score exploitable.")
        if not scores:
            var_meilleure.set("")
            canvas.draw_idle()
            return

        xs = [_horizon_en_minutes(s.horizon) for s in scores]
        ys = [s.seuil_c1 for s in scores]
        zs = [s.score for s in scores]

        # Ombres au sol : un repère discret projeté sur le plancher du graphique pour
        # chaque point, sous sa vraie position 3D — aide l'œil à situer la profondeur
        # sans devoir tourner la vue (complète le classement 2D, plus lisible mais
        # moins "d'ensemble"). Calculé AVANT les vrais marqueurs pour rester dessous.
        z_floor = min(zs) - max((max(zs) - min(zs)) * 0.08, 0.02) if zs else 0.0
        ax.scatter(xs, ys, [z_floor] * len(xs), color=(0.55, 0.55, 0.6), alpha=0.18,
                   s=26, marker="o", linewidths=0, depthshade=False)

        for methode, marqueur in _MARQUEURS_METHODE.items():
            indices = [i for i, s in enumerate(scores) if s.methode == methode]
            if not indices:
                continue
            nuage = ax.scatter(
                [xs[i] for i in indices], [ys[i] for i in indices], [zs[i] for i in indices],
                c=[zs[i] for i in indices], cmap="RdYlGn_r", vmin=0, vmax=1,
                marker=marqueur, s=70, edgecolors=(0.3, 0.3, 0.35, 0.7), linewidths=0.5,
                alpha=0.92, label=f"Méthode {methode}",
            )
        ax.set_zlim(z_floor, max(zs) + max((max(zs) - min(zs)) * 0.08, 0.02))
        etat_colorbar["cb"] = fig.colorbar(nuage, ax=ax, shrink=0.6, pad=0.1,
                                            label="Score composite (0=meilleur)")
        # Repère posé en coordonnées FIGURE (pas données/axes) — reste donc fixe à côté
        # de la colorbar (élément 2D stable) même quand la vue 3D est tournée à la
        # souris, contrairement à un label d'axe Z qui pivote avec la vue. Position
        # recalculée depuis la vraie bbox de la colorbar (pas de coordonnées codées en
        # dur) : reste juste au-dessus d'elle quels que soient les width_ratios de la
        # grille (modifiés depuis pour agrandir le nuage 3D, voir plus haut).
        bbox_cb = etat_colorbar["cb"].ax.get_position()
        _icone_info_axe(fig, canvas, etat_icones, "colorbar",
                         bbox_cb.x0 + 0.5 * bbox_cb.width, bbox_cb.y1 + 0.035,
                         "Score composite", explication_score(poids, asymetrie_dtp))

        # Meilleure combinaison mise en évidence (score le plus bas = le plus vert). Le
        # détail (paramètres + les 4 indicateurs moyens qui composent son score) est
        # inclus directement dans le libellé de légende de cette étoile — matplotlib
        # affiche un label multi-lignes comme une seule entrée de légende.
        meilleur = min(scores, key=lambda s: s.score)
        m = meilleur.moyennes_erreur
        libelle_meilleure = (
            "Meilleure combinaison\n"
            f"Horizon {meilleur.horizon} / seuil {meilleur.seuil_c1:.2f} / méthode {meilleur.methode}\n"
            f"Score composite : {meilleur.score:.3f}\n"
            f"|dQP| {m.get('dqp'):.2f}%   |dTP| {m.get('dtp'):.2f} pdt\n"
            f"|VE| {m.get('ve'):.2f}%   (1−KGE) {m.get('kge'):.2f}"
        )
        ax.scatter([_horizon_en_minutes(meilleur.horizon)], [meilleur.seuil_c1], [meilleur.score],
                   marker="*", s=400, color="gold", edgecolors="#333333", linewidths=0.8,
                   label=libelle_meilleure)
        var_meilleure.set(
            f"★ Meilleure combinaison : horizon {meilleur.horizon} / seuil "
            f"{meilleur.seuil_c1:.2f} / méthode {meilleur.methode} — score "
            f"{meilleur.score:.3f} ({meilleur.nb_crues} crue(s))"
        )

        horizons_uniques = sorted({s.horizon for s in scores}, key=_horizon_en_minutes)
        ax.set_xticks([_horizon_en_minutes(h) for h in horizons_uniques])
        ax.set_xticklabels(horizons_uniques, rotation=30, ha="right", fontsize=6.5)
        ax.set_yticks(sorted({s.seuil_c1 for s in scores}))
        ax.set_xlabel("Horizon", labelpad=14, fontsize=8)
        ax.set_ylabel("Seuil de calage (m³/s)", labelpad=8, fontsize=8)
        ax.set_zlabel("Score composite (0=meilleur)", fontsize=8)
        # Légende ancrée en coordonnées FIGURE (bbox_transform=fig.transFigure), pas en
        # coordonnées de l'axe 3D — reste bien dans le coin haut-gauche du cadre entier
        # quelle que soit la largeur réelle de l'axe (demande explicite : la première
        # version, ancrée en négatif RELATIVEMENT à l'axe, ne collait pas vraiment au
        # coin du cadre). Marge de gauche réduite d'autant pour agrandir le nuage 3D.
        fig.subplots_adjust(left=0.15, wspace=0.38)
        ax.legend(loc="upper left", bbox_to_anchor=(0.005, 0.98), bbox_transform=fig.transFigure,
                  fontsize=7, labelspacing=1.8)

        # -- Classement 2D : écart de score par rapport à la meilleure combinaison ----
        # Barres triées (la meilleure en haut), longueur = à quel point chaque
        # combinaison est plus mauvaise que la meilleure (Δ = 0 pour elle-même) —
        # rend l'écart directement lisible en longueur, sans l'ambiguïté de profondeur
        # inhérente à un nuage de points 3D tourné à la souris.
        cmap = colormaps["RdYlGn_r"]
        NB_MAX_AFFICHEES = 20
        scores_tries = sorted(scores, key=lambda s: s.score)
        scores_affiches = scores_tries[:NB_MAX_AFFICHEES]
        libelles = [f"{s.horizon} / {s.seuil_c1:.2f} / {s.methode}" for s in scores_affiches]
        deltas = [s.score - meilleur.score for s in scores_affiches]
        positions_y = list(range(len(scores_affiches)))[::-1]  # meilleure en haut du graphique
        couleurs_barres = [cmap(s.score) for s in scores_affiches]
        ax_classement.barh(positions_y, deltas, color=couleurs_barres,
                            edgecolor="#333333", linewidth=0.5, height=0.7, zorder=2)
        ax_classement.scatter([0], [positions_y[0]], marker="*", s=220, color="gold",
                               edgecolors="#333333", linewidths=0.7, zorder=5)
        ax_classement.set_yticks(positions_y)
        ax_classement.set_yticklabels(libelles, fontsize=7)
        ax_classement.set_xlabel("Écart de score par rapport à la meilleure combinaison (Δ)", fontsize=8)
        ax_classement.set_title(
            f"Classement — écart au meilleur"
            + (f" (20 premières sur {len(scores_tries)})" if len(scores_tries) > NB_MAX_AFFICHEES else ""),
            fontsize=9)
        ax_classement.grid(True, axis="x", alpha=0.3)
        ax_classement.axvline(0, color="#333333", lw=0.8)

        canvas.draw_idle()

    _rafraichir()
    return _rafraichir  # exposé pour que build_tab_dashboard puisse retracer au changement de pondération
