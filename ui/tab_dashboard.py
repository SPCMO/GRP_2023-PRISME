# -*- coding: utf-8 -*-
"""Onglet Dashboard — bloc 6 : synthèse des résultats de campagne (results_store),
remplace l'unique graphique Excel du script d'origine par 3 vues complémentaires :

  1. Vue synthèse : heatmap horizon × seuil (score composite), classement des
     meilleures combinaisons, dispersion de |dQP| par horizon.
  2. Détail par crue : courbe Qobs (+ Qsimulé si disponible) avec seuils de vigilance
     PHyC superposés, indicateurs dQP/dTP/VE/KGE de la combinaison choisie.
  3. Sensibilité au seuil de calage : score/KGE médian en fonction de SeuilC1, à horizon
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
from modules.grp_paths import construire_grp_paths
from modules.score import (
    AGREGATION_PAR_DEFAUT, PROFILS_PONDERATION, calculer_scores, config_ponderation_par_defaut,
    explication_score, filtrer_par_crues, resoudre_ponderation,
)
from ui.tab_config import LIBELLES_SEUILS_Q
from ui.widgets_common import (
    PALETTE_COURBES, bouton_info, enregistrer_observateur_pdt, icone_info_axe,
    libelle_dernier_pdt, make_label, make_row, make_scrollable_tab, make_section,
    sauvegarder_dernier_pdt,
)

# Couleur de la courbe Q observé (Détail par crue) — bleu net, distinct des couleurs
# de PALETTE_COURBES pour ne jamais être confondu avec une courbe simulée.
_COULEUR_OBS = "#1B4F72"

# Question posée indépendamment par 2 utilisateurs sur la même ligne ("Crue #N (...) —
# configuration en place : dQP ... KGE ...") : ce n'est PAS l'une des combinaisons
# testées par la campagne, mais la performance du calage ACTUELLEMENT installé dans
# GRP — voir aussi ui/tab_crues.py::TEXTE_INFO_INDICATEURS_REFERENCE (même source de
# données, CRITERES_PERF.DAT, affichée sous une autre forme dans l'onglet Crues).
_TEXTE_INFO_CONFIGURATION_EN_PLACE = (
    "Ces indicateurs (dQP/dTP/VE/KGE) ne correspondent à AUCUNE des combinaisons "
    "horizon/seuil/méthode sélectionnées ci-dessus : ils viennent de "
    "CRITERES_PERF.DAT, calculés par GRP lui-même au moment de la détection des crues "
    "(exe 01+03), avec le calage ACTUELLEMENT installé dans GRP (typiquement celui "
    "utilisé en production/astreinte réelle, indépendant de tout test PRISME) et un "
    "calage COMPLET (toute la chronique connue d'avance) — pas un rejeu en conditions "
    "de prévision comme le fait une campagne. Voir Aide.html > Architecture > "
    "\"Pourquoi deux sources de performance différentes\". Une simple référence pour "
    "comparer, jamais un résultat de campagne."
)

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


def _libelle_horizon_court(horizon):
    """Forme courte d'un horizon 'ddJhhHmmM' pour étiquettes compactes sur un graphique
    (ex. "1H", "6H", "1J") — voir onglet Dashboard "Variation selon le nb de crues"."""
    minutes = _horizon_en_minutes(horizon)
    if minutes % 1440 == 0:
        return f"{minutes // 1440}J"
    if minutes % 60 == 0:
        return f"{minutes // 60}H"
    return f"{minutes}min"


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
    # padx de gauche généreux : décale nettement ce bloc vers la droite du bandeau,
    # pour bien le séparer visuellement du bloc pondération — demandé explicitement.
    tk.Label(barre_pondération, text="Crues dans le score :").pack(side=tk.LEFT, padx=(40, 0))
    var_crues_score = tk.StringVar(value="")
    tk.Label(barre_pondération, textvariable=var_crues_score, font=("TkDefaultFont", 9, "bold")).pack(
        side=tk.LEFT, padx=(4, 6))
    ttk.Button(barre_pondération, text="Choisir…",
               command=lambda: _ouvrir_selecteur_crues_score(
                   app, lambda: _apres_choix_crues_score())).pack(side=tk.LEFT)

    # Sélecteur d'agrégation des erreurs par crue (médiane/moyenne) — même principe
    # que les 2 réglages ci-dessus (un seul, partagé par les 3 vues qui affichent un
    # score composite ET la fenêtre "Combinaisons déjà réalisées") : demandé pour
    # comparer les 2 modes à la volée, sans reprendre aucun calage/rejeu GRP (le score
    # ne dépend que des dQP/dTP/VE/KGE déjà stockés en base par crue). "Médiane" reste
    # le comportement par défaut (jamais changé en place, voir _agregation_active).
    tk.Label(barre_pondération, text="Agrégation par crue :").pack(side=tk.LEFT, padx=(40, 0))
    var_agregation = tk.StringVar()
    combo_agregation = ttk.Combobox(
        barre_pondération, textvariable=var_agregation, state="readonly", width=10,
        values=["Médiane", "Moyenne"])
    combo_agregation.pack(side=tk.LEFT, padx=(6, 0))

    def _appliquer_agregation(*_evt):
        _config_score(app)["agregation"] = (
            "mediane" if var_agregation.get() == "Médiane" else "moyenne")
        app.persist_config()
        _rafraichir_toutes_les_vues_du_score()

    combo_agregation.bind("<<ComboboxSelected>>", _appliquer_agregation)
    var_agregation.set("Médiane" if _agregation_active(app) == "mediane" else "Moyenne")

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
    onglet_variation_crues = ttk.Frame(sous_notebook)
    sous_notebook.add(onglet_synthese, text="Vue synthèse")
    sous_notebook.add(onglet_detail, text="Détail par crue")
    sous_notebook.add(onglet_sensibilite, text="Sensibilité au seuil")
    sous_notebook.add(onglet_3d, text="Vue 3D")
    sous_notebook.add(onglet_variation_crues, text="Variation selon le nb de crues")

    # Chaque sous-onglet est enveloppé dans un Canvas+Scrollbar (make_scrollable_tab,
    # déjà utilisé par tous les autres onglets principaux de l'outil — Configuration,
    # Paramétrage, Crues, Campagne, Analyse crues affl. — mais jusqu'ici oublié sur
    # Dashboard) : sur un écran/une fenêtre trop petite pour tout afficher d'un coup
    # (ex. "Détail par crue" sur un PC portable, où le tableau "Maximum de chaque
    # courbe tracée" sous le graphique restait invisible, hors de portée), le contenu
    # devient défilant au lieu d'être simplement coupé sans aucun moyen d'y accéder.
    # `defilement_horizontal=True` (demandé) : plusieurs tableaux (heatmap
    # horizon×seuil, comparaison des instants de rejeu) peuvent être plus larges que la
    # fenêtre — sans ascenseur horizontal ils étaient silencieusement écrasés/tronqués
    # plutôt que consultables en défilant.
    _rafraichir_synthese = _build_synthese(
        make_scrollable_tab(onglet_synthese, defilement_horizontal=True), app)
    _rafraichir_detail = _build_detail(
        make_scrollable_tab(onglet_detail, defilement_horizontal=True), app)
    _rafraichir_sensibilite = _build_sensibilite(
        make_scrollable_tab(onglet_sensibilite, defilement_horizontal=True), app)
    _rafraichir_vue3d = _build_vue3d(
        make_scrollable_tab(onglet_3d, defilement_horizontal=True), app)
    _rafraichir_variation_crues = _build_variation_crues(
        make_scrollable_tab(onglet_variation_crues, defilement_horizontal=True), app)

    # Exposées sur `app` pour main.App.on_resultats_changed (voir aussi
    # ui/tab_orchestration.py, fenêtre "Combinaisons déjà réalisées" > Supprimer) :
    # après une suppression de combinaisons en base, TOUTES les vues du Dashboard
    # doivent redevenir cohérentes, pas seulement celle actuellement affichée à
    # l'écran (le binding <<NotebookTabChanged>> ci-dessous ne rafraîchit que le
    # sous-onglet qu'on VIENT de sélectionner, jamais les autres déjà construits).
    app.rafraichir_dashboard_synthese = _rafraichir_synthese
    app.rafraichir_dashboard_detail = _rafraichir_detail
    app.rafraichir_dashboard_sensibilite = _rafraichir_sensibilite
    app.rafraichir_dashboard_vue3d = _rafraichir_vue3d
    app.rafraichir_dashboard_variation_crues = _rafraichir_variation_crues

    def _rafraichir_toutes_les_vues_du_score():
        # Détail par crue n'affiche pas de score composite (dQP/dTP/VE/KGE de
        # référence seulement) — volontairement absent de cette liste. Variation selon
        # le nb de crues EST incluse : elle utilise la même pondération, même si elle
        # ignore délibérément le sélecteur "Crues dans le score" (voir cet onglet).
        _rafraichir_synthese()
        _rafraichir_sensibilite()
        _rafraichir_vue3d()
        _rafraichir_variation_crues()

    # Rafraîchit automatiquement la vue du sous-onglet vers lequel on navigue — avant
    # cela, seule "Vue synthèse" (bouton "Rafraîchir") et un changement de pondération
    # redessinaient quoi que ce soit : après une nouvelle campagne, "Sensibilité au
    # seuil" en particulier restait bloquée sur ses listes Horizon(s)/Méthode(s) vides
    # du tout premier lancement jusqu'à fermer/rouvrir l'outil (signalé explicitement).
    _RAFRAICHIR_PAR_ONGLET = {
        str(onglet_synthese): _rafraichir_synthese,
        str(onglet_sensibilite): _rafraichir_sensibilite,
        str(onglet_3d): _rafraichir_vue3d,
        str(onglet_variation_crues): _rafraichir_variation_crues,
    }

    def _au_changement_sous_onglet(_evt=None):
        fn = _RAFRAICHIR_PAR_ONGLET.get(sous_notebook.select())
        if fn:
            fn()

    sous_notebook.bind("<<NotebookTabChanged>>", _au_changement_sous_onglet)

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


def _agregation_active(app):
    """Mode d'agrégation des erreurs par crue (médiane/moyenne) RÉELLEMENT actif à cet
    instant — "mediane" par défaut, jamais changé sans action explicite de
    l'utilisateur (même principe que _poids_actifs). Partagé par toutes les vues à
    score du Dashboard ET la fenêtre "Combinaisons déjà réalisées" (onglet Campagne),
    pour que le score composite désigne toujours la même chose partout dans l'outil —
    voir modules.score.calculer_scores(agregation=...)."""
    return _config_score(app).get("agregation", AGREGATION_PAR_DEFAUT)


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
    paths, _manquants = construire_grp_paths(app)
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


_PALIERS_VIGILANCE = (
    ("rouge", "Rouge"), ("zt_rouge", "ZT rouge"),
    ("orange", "Orange"), ("zt_orange", "ZT orange"),
    ("jaune", "Jaune"), ("zt_jaune", "ZT jaune"),
)  # du plus sévère au moins sévère — même clés que ui.tab_config.LIBELLES_SEUILS_Q


def _niveau_vigilance(qmax, seuils_q):
    """Niveau de vigilance PHyC atteint par le débit de pointe d'une crue — même
    principe que tab_crues._couleur_vigilance (comparaison aux seuils du bloc 2), mais
    renvoie le LIBELLÉ du plus haut niveau atteint (7 valeurs possibles : Vert, ZT
    jaune, Jaune, ZT orange, Orange, ZT rouge, Rouge) plutôt qu'une couleur de fond.
    None si Qmax est inconnu (crue non retrouvée dans CRITERES_PERF.DAT)."""
    if qmax is None:
        return None
    for cle, libelle in _PALIERS_VIGILANCE:
        seuil = seuils_q.get(cle)
        if seuil is not None and qmax >= seuil:
            return libelle
    return "Vert"


def _lister_crues_details_pour_score(app):
    """Comme _lister_crues_pour_score, mais avec en plus TypEvt (Q/P), Qmax, le niveau
    de vigilance max atteint et le cumul de pluie de l'épisode (mm, somme brute — voir
    modules.export_excel pour la même donnée/le même calcul côté export) — alimente les
    colonnes du sélecteur de crues du score (demandé). Retourne une liste de dicts
    {iso, libelle, typ_evt, qmax, vigilance, cumul_pluie}, à None si l'événement n'a pas
    pu être retrouvé dans CRITERES_PERF.DAT (best-effort, jamais bloquant)."""
    lignes, _erreur = _charger_resultats(app)
    dates_disponibles = sorted({l["crue_date"] for l in lignes if l["statut_crue"] == "success"})
    if not dates_disponibles:
        return []

    entrees = []
    restants = set(dates_disponibles)
    paths, _manquants = construire_grp_paths(app)
    if paths is not None:
        for pdt in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if not restants:
                break
            code_pdt = pdt["code"]
            try:
                evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
            except (FileNotFoundError, CriteresPerfError):
                continue
            for e in evenements:
                iso = e.date_deb.isoformat()
                if iso not in restants:
                    continue
                cumul_pluie = None
                try:
                    chemin_serie = os.path.join(paths.evenements_dir(code_pdt),
                                                 f"{paths.code_site}-EV{e.num_evt:04d}.DAT")
                    serie = parse_evenement_serie(chemin_serie)
                    if serie:
                        cumul_pluie = sum(p[1] for p in serie)  # déjà en mm/pas de temps, somme brute
                except (FileNotFoundError, CriteresPerfError):
                    pass
                entrees.append({"num_evt": e.num_evt, "iso": iso, "date_deb": e.date_deb,
                                  "typ_evt": e.typ_evt, "qmax": e.qmax, "cumul_pluie": cumul_pluie})
                restants.discard(iso)
    for iso in sorted(restants):
        d = datetime.fromisoformat(iso)
        entrees.append({"num_evt": None, "iso": iso, "date_deb": d,
                          "typ_evt": None, "qmax": None, "cumul_pluie": None})
    entrees.sort(key=lambda e: (e["num_evt"] is None, e["num_evt"] or 0))

    seuils_q = app.config_data.get("seuils_q", {})
    resultat = []
    for e in entrees:
        prefixe = f"#{e['num_evt']}" if e["num_evt"] is not None else "?"
        resultat.append({
            "iso": e["iso"], "libelle": f"{prefixe} - {e['date_deb']:%d/%m/%Y %H:%M}",
            "typ_evt": e["typ_evt"], "qmax": e["qmax"],
            "vigilance": _niveau_vigilance(e["qmax"], seuils_q),
            "cumul_pluie": e["cumul_pluie"],
        })
    return resultat


def _ouvrir_selecteur_crues_score(app, apres_enregistrement):
    """Fenêtre de sélection des crues incluses dans le calcul du score composite —
    permet de recalculer le score sur un sous-ensemble de crues (ex. exclure un épisode
    atypique) sans reprendre aucun calage/rejeu GRP, voir modules.score.filtrer_par_crues.
    Pré-cochée sur la sélection ACTUELLEMENT active (toutes par défaut)."""
    crues = _lister_crues_details_pour_score(app)
    incluses_actuelles = _crues_incluses_score(app)

    fenetre = tk.Toplevel(app)
    fenetre.title("Crues incluses dans le score composite")
    fenetre.geometry("700x520")
    fenetre.transient(app)
    fenetre.grab_set()

    tk.Label(fenetre, text="Décocher une crue l'exclut du calcul du score composite "
                           "(Vue synthèse, Sensibilité au seuil, Vue 3D, et la fenêtre "
                           "\"Combinaisons déjà réalisées\" de l'onglet Campagne) — sans "
                           "reprendre aucun calage ni rejeu GRP.",
             wraplength=530, justify=tk.LEFT).pack(anchor="w", padx=10, pady=(10, 6))

    if not crues:
        tk.Label(fenetre, text="Aucune crue avec résultat en base pour l'instant.",
                 fg="#a94442").pack(anchor="w", padx=10)

    cadre_liste = tk.Frame(fenetre)
    cadre_liste.pack(fill=tk.BOTH, expand=True, padx=10)
    liste = ttk.Treeview(cadre_liste, columns=("crue", "type", "vigilance", "qmax", "pluie"),
                          show="headings", selectmode="extended")
    for col, libelle, largeur in (
        ("crue", "Crue", 190), ("type", "TypEvt", 70), ("vigilance", "Vigilance max", 100),
        ("qmax", "Qmax (m³/s)", 100), ("pluie", "Cumul pluie (mm)", 120),
    ):
        liste.heading(col, text=libelle)
        liste.column(col, width=largeur, anchor="center" if col != "crue" else "w")
    ascenseur = ttk.Scrollbar(cadre_liste, orient=tk.VERTICAL, command=liste.yview)
    liste.configure(yscrollcommand=ascenseur.set)
    liste.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur.pack(side=tk.RIGHT, fill=tk.Y)

    # Fond de ligne coloré par niveau de vigilance — même logique de teintes que les
    # vignettes de l'onglet Crues (tab_crues._couleur_vigilance), étendue aux paliers
    # ZT intermédiaires pour rester lisible d'un coup d'œil sans lire le texte.
    _COULEURS_VIGILANCE = {
        "Vert": "#D5F5E3", "ZT jaune": "#FCF3CF", "Jaune": "#F9E79F",
        "ZT orange": "#FDEBD0", "Orange": "#FAD7A0", "ZT rouge": "#FADBD8", "Rouge": "#F5B7B1",
    }
    for niveau, couleur in _COULEURS_VIGILANCE.items():
        liste.tag_configure(f"vig_{niveau}", background=couleur)

    isos_deja_incluses = set(incluses_actuelles) if incluses_actuelles is not None else None
    for c in crues:
        tags = (f"vig_{c['vigilance']}",) if c["vigilance"] in _COULEURS_VIGILANCE else ()
        item_id = liste.insert("", tk.END, iid=c["iso"], tags=tags, values=(
            c["libelle"],
            "Q (crue)" if c["typ_evt"] == "Q" else "P (pluie)" if c["typ_evt"] == "P" else "—",
            c["vigilance"] or "—",
            f"{c['qmax']:.1f}" if c["qmax"] is not None else "—",
            f"{c['cumul_pluie']:.1f}" if c["cumul_pluie"] is not None else "—",
        ))
        if isos_deja_incluses is None or c["iso"] in isos_deja_incluses:
            liste.selection_add(item_id)

    barre_rapide = tk.Frame(fenetre)
    barre_rapide.pack(pady=(6, 0))
    ttk.Button(barre_rapide, text="Toutes",
               command=lambda: liste.selection_set(liste.get_children())).pack(side=tk.LEFT, padx=4)
    ttk.Button(barre_rapide, text="Aucune",
               command=lambda: liste.selection_remove(liste.get_children())).pack(side=tk.LEFT, padx=4)

    var_erreur = tk.StringVar(value="")
    tk.Label(fenetre, textvariable=var_erreur, fg="#A93226", wraplength=530,
             justify=tk.LEFT).pack(anchor="w", padx=10, pady=(6, 0))

    def _enregistrer():
        selection = liste.selection()
        if not selection:
            var_erreur.set("Sélectionnez au moins une crue (un score sur aucune crue "
                            "n'a pas de sens).")
            return
        cfg = _config_score(app)
        if len(selection) == len(crues):
            cfg["crues_incluses"] = None  # "toutes" — suit dynamiquement les futures crues
        else:
            cfg["crues_incluses"] = list(selection)  # iid == iso (voir liste.insert ci-dessus)
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

    # Icône réduite de moitié (taille=5, contre 10 par défaut) et décalée plus à droite
    # (x=1.75 au lieu de 1.35) pour se poser juste APRÈS le mot "extrêmes" plutôt que de
    # le recouvrir — signalé par l'utilisateur sur le rendu réel.
    x_icone_max, y_icone_max = _position_figure(1.75, moustache_haut - 1.3)
    x_icone_min, y_icone_min = _position_figure(1.75, moustache_bas - 1.3)
    icone_info_axe(fig, canvas, etat_icones, "extremes_max", x_icone_max, y_icone_max,
                     "Valeurs extrêmes (boîte à moustaches)", _TEXTE_VALEURS_EXTREMES, taille=5)
    icone_info_axe(fig, canvas, etat_icones, "extremes_min", x_icone_min, y_icone_min,
                     "Valeurs extrêmes (boîte à moustaches)", _TEXTE_VALEURS_EXTREMES, taille=5)


def _build_synthese(frame, app):
    barre = tk.Frame(frame)
    barre.pack(fill=tk.X, padx=8, pady=6)
    var_statut = tk.StringVar(value="")
    tk.Label(barre, textvariable=var_statut, fg="#555555").pack(side=tk.LEFT)
    bouton_info(barre, "Score composite",
                lambda: explication_score(*_poids_actifs(app)[:2],
                                           agregation=_agregation_active(app))).pack(
        side=tk.LEFT, padx=(6, 0))
    ttk.Button(barre, text="Rafraîchir", command=lambda: _rafraichir()).pack(side=tk.RIGHT, padx=4)
    bouton_export = ttk.Button(barre, text="Exporter en Excel…", command=lambda: _exporter())
    bouton_export.pack(side=tk.RIGHT)

    # Filtre méthode(s) pour le graphique composite (heatmap + dispersion + tableau) —
    # les 2 cases cochées par défaut reproduisent le comportement d'origine (les 2
    # méthodes confondues) ; décocher l'une des deux exclut ses lignes du score avant
    # même son calcul, pas seulement de l'affichage.
    barre_methodes = tk.Frame(frame)
    barre_methodes.pack(fill=tk.X, padx=8, pady=(0, 4))
    tk.Label(barre_methodes, text="Méthode(s) affichée(s) :").pack(side=tk.LEFT)
    var_methode_t = tk.BooleanVar(value=True)
    var_methode_r = tk.BooleanVar(value=True)
    ttk.Checkbutton(barre_methodes, text="Tangara", variable=var_methode_t,
                     command=lambda: _rafraichir()).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Checkbutton(barre_methodes, text="RNA", variable=var_methode_r,
                     command=lambda: _rafraichir()).pack(side=tk.LEFT, padx=(10, 0))

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

    ligne_titre_classement = tk.Frame(frame)
    ligne_titre_classement.pack(fill=tk.X, padx=8, pady=(4, 0))
    tk.Label(ligne_titre_classement, text="Classement des combinaisons",
             font=("TkDefaultFont", 9, "bold")).pack(side=tk.LEFT)
    bouton_info(
        ligne_titre_classement, "Sous/sur-estimation",
        "Colonne « Sous/sur-estim. » : x / y\n\n"
        "x = nombre de crues (parmi celles incluses dans le score, voir la sélection "
        "\"Crues dans le score\" en haut) où le débit simulé SOUS-estime le débit "
        "observé (dQP < 0, pic simulé plus bas que le pic observé).\n\n"
        "y = nombre de crues où il le SURestime (dQP > 0).\n\n"
        "Les crues à dQP exactement nul, ou sans dQP disponible pour cette "
        "combinaison, ne comptent ni dans x ni dans y — x + y peut donc être "
        "inférieur au nombre total de crues de la colonne \"Nb crues\"."
    ).pack(side=tk.LEFT, padx=(4, 0))

    # Colonnes du tableau — score ET dQP/dTP affichés en DOUBLE (médiane + moyenne,
    # côte à côte, toujours les deux) : demandé pour comparer les 2 modes d'agrégation
    # directement, indépendamment du sélecteur "Agrégation par crue" ci-dessus (qui,
    # lui, ne pilote que la heatmap/dispersion et le tri PAR DÉFAUT du tableau — voir
    # _COLONNE_SCORE_PAR_AGREGATION et _trier_tableau ci-dessous).
    _COLONNES_TABLEAU = ("horizon", "seuil", "methode", "score_med", "score_moy",
                          "nb_crues", "sous_sur", "dqp_med", "dqp_moy", "dt_med", "dt_moy")
    _LIBELLES_TABLEAU = {
        "horizon": "Horizon", "seuil": "Seuil C1", "methode": "Méthode",
        "score_med": "Score ac méd. (0=meilleur)", "score_moy": "Score ac moy. (0=meilleur)",
        "nb_crues": "Nb crues", "sous_sur": "Sous/sur-estim.",
        "dqp_med": "dQp médian (%)", "dqp_moy": "dQp moyen (%)",
        "dt_med": "dT médian (pdt)", "dt_moy": "dT moyen (pdt)",
    }
    # Colonne de tri par défaut selon le mode actif (voir demande explicite : "quand
    # l'utilisateur a choisi Médiane, le tri croissant se fait sur Score ac méd., idem
    # pour Moyenne") — recalée à CHAQUE changement du sélecteur (voir
    # _appliquer_agregation ci-dessus), sans effacer un tri manuel choisi entretemps
    # (l'utilisateur peut re-cliquer une colonne après coup pour trier autrement).
    _COLONNE_SCORE_PAR_AGREGATION = {"mediane": "score_med", "moyenne": "score_moy"}

    cadre_classement = tk.Frame(frame)
    cadre_classement.pack(fill=tk.X, padx=8, pady=(0, 8))
    tableau = ttk.Treeview(cadre_classement, columns=_COLONNES_TABLEAU,
                            show="headings", height=8)
    etat_tri = {"colonne": _COLONNE_SCORE_PAR_AGREGATION[_agregation_active(app)],
                "croissant": True, "dernier_mode_vu": None}

    def _trier_tableau(col):
        if etat_tri["colonne"] == col:
            etat_tri["croissant"] = not etat_tri["croissant"]
        else:
            etat_tri["colonne"], etat_tri["croissant"] = col, True
        _rafraichir()

    def _maj_entetes_tri():
        for col in _COLONNES_TABLEAU:
            texte = _LIBELLES_TABLEAU[col]
            if col == etat_tri["colonne"]:
                texte += " ▲" if etat_tri["croissant"] else " ▼"
            tableau.heading(col, text=texte, command=lambda c=col: _trier_tableau(c))

    _maj_entetes_tri()
    for col in _COLONNES_TABLEAU:
        tableau.column(col, width=118, anchor="center")
    ascenseur_tableau = ttk.Scrollbar(cadre_classement, orient=tk.VERTICAL, command=tableau.yview)
    tableau.configure(yscrollcommand=ascenseur_tableau.set)
    tableau.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur_tableau.pack(side=tk.RIGHT, fill=tk.Y)

    def _exporter():
        poids, asymetrie_dtp, libelle_profil = _poids_actifs(app)
        crues_incluses = _crues_incluses_score(app)
        total_crues = len(_lister_crues_pour_score(app))
        nb_crues_score = len(crues_incluses) if crues_incluses else total_crues
        station = app.config_data.get("station", {})

        # Seuils/méthodes RÉELLEMENT présents dans les résultats qui seront exportés
        # (data/runs_<code_station>.sqlite3, tous instants='reference' confondus) —
        # PAS la sélection actuelle de l'onglet Paramétrage (qui ne prépare qu'une
        # PROCHAINE campagne et peut très bien avoir changé depuis, ex. une méthode
        # décochée après coup) ni le filtre d'affichage Tangara/RNA de cette Vue
        # synthèse (qui ne restreint que le graphique à l'écran, jamais l'export —
        # voir les cases à cocher "Méthode(s) affichée(s)" ci-dessus). Sans ce
        # correctif, la fenêtre de vérification pouvait annoncer "Tangara" seul alors
        # que le classeur exporté contient en réalité les deux méthodes (constaté par
        # l'utilisateur) — trompeur pour une fenêtre dont le rôle est justement de
        # prévisualiser fidèlement ce qui va être écrit dans le fichier.
        lignes_toutes, _erreur = _charger_resultats(app)
        lignes_reussies = [l for l in lignes_toutes if l["statut_crue"] == "success"]
        seuils_reels = sorted({l["seuil_c1"] for l in lignes_reussies})
        methodes_reelles = sorted({l["methode"] for l in lignes_reussies})
        libelles_methodes = ", ".join(
            "Tangara" if m == "T" else "RNA" if m == "R" else m for m in methodes_reelles) or "—"

        message = (
            f"Vérifiez les paramètres avant d'exporter :\n\n"
            f"Station : {station.get('nom_station') or station.get('code_station') or '—'}\n"
            f"Pondération du score composite : {libelle_profil}\n"
            f"  (|dQP| : {poids.get('dqp')}, |dTP| : {poids.get('dtp')}, "
            f"|VE| : {poids.get('ve')}, (1−KGE) : {poids.get('kge')})\n"
            f"  Asymétrie dTP — retard : {asymetrie_dtp.get('retard')}, "
            f"avance : {asymetrie_dtp.get('avance')}\n"
            f"Crues incluses dans le score : {nb_crues_score}/{total_crues}"
            + (" (sélection restreinte)" if crues_incluses else " (toutes)") + "\n"
            f"Seuils de calage présents dans les résultats : "
            f"{', '.join(f'{s:.2f}' for s in seuils_reels) or '—'}\n"
            f"Méthode(s) présente(s) dans les résultats : {libelles_methodes}\n\n"
            f"OK pour continuer et choisir où enregistrer le fichier, "
            f"Annuler pour revenir corriger des valeurs sur l'outil."
        )
        if not messagebox.askokcancel("Export Excel — vérification avant export", message):
            return

        chemin = filedialog.asksaveasfilename(
            title="Exporter les résultats en Excel", defaultextension=".xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")])
        if not chemin:
            return
        # Retour visuel minimal pendant l'export (classeur à 7 feuilles avec figures
        # matplotlib re-rendues, potentiellement plusieurs secondes) — signalé comme
        # manquant : sans lui, l'interface semble figée et incite à recliquer. Pas de
        # thread dédié (contrairement à l'onglet Campagne, pour un traitement bien plus
        # long) : juste désactiver le bouton et forcer l'affichage avant l'appel
        # bloquant, qui suffit pour une opération de l'ordre de quelques secondes.
        texte_normal = bouton_export["text"]
        bouton_export.config(text="Export en cours…", state="disabled")
        app.config(cursor="watch")
        app.update_idletasks()
        try:
            export_excel.exporter(chemin, app)
        except Exception as e:
            messagebox.showerror("Export Excel", str(e))
            return
        finally:
            bouton_export.config(text=texte_normal, state="normal")
            app.config(cursor="")
        messagebox.showinfo("Export Excel", f"Export réussi : {chemin}")

    def _rafraichir():
        # Recale le tri sur la colonne de score du mode ACTIF dès qu'il vient de
        # changer (sélecteur "Agrégation par crue") — demandé explicitement — sans
        # écraser un tri manuel choisi entretemps pour toute autre raison de
        # rafraîchissement (nouvelle campagne, changement de pondération...).
        mode_actif = _agregation_active(app)
        if etat_tri["dernier_mode_vu"] != mode_actif:
            etat_tri["colonne"] = _COLONNE_SCORE_PAR_AGREGATION[mode_actif]
            etat_tri["croissant"] = True
            etat_tri["dernier_mode_vu"] = mode_actif
        _maj_entetes_tri()

        lignes, erreur = _charger_resultats(app)
        if erreur:
            var_statut.set(erreur)
            return
        lignes_toutes_methodes = _filtrer_lignes_score(app, [l for l in lignes if l["statut_crue"] == "success"])
        methodes_cochees = {m for m, v in (("T", var_methode_t), ("R", var_methode_r)) if v.get()}
        if not methodes_cochees:
            var_statut.set("Cochez au moins une méthode (Tangara ou RNA) pour afficher le graphique.")
            if etat_colorbar["cb"] is not None:
                etat_colorbar["cb"].remove()
                etat_colorbar["cb"] = None
            ax_heatmap.clear()
            ax_dispersion.clear()
            canvas.draw_idle()
            tableau.delete(*tableau.get_children())
            return
        lignes_ok = [l for l in lignes_toutes_methodes if l["methode"] in methodes_cochees]
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
        scores = calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp, agregation=_agregation_active(app))
        var_statut.set(f"{len(lignes_ok)} résultat(s) réussi(s), {len(scores)} combinaison(s) "
                        f"— pondération : {libelle_profil}.")

        # Le tableau de classement affiche TOUJOURS les 2 modes côte à côte (demandé),
        # indépendamment de `scores` ci-dessus (mode actif, utilisé UNIQUEMENT pour la
        # heatmap/dispersion/cadre jaune) — réutilise `scores` sans le recalculer pour
        # le mode déjà obtenu, calcule l'autre mode séparément.
        scores_mediane = (scores if mode_actif == "mediane" else
                           calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp,
                                            agregation="mediane"))
        scores_moyenne = (scores if mode_actif == "moyenne" else
                           calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp,
                                            agregation="moyenne"))
        scores_moyenne_par_cle = {(s.horizon, s.seuil_c1, s.methode): s for s in scores_moyenne}

        # Échelles X/Y calculées sur les 2 méthodes confondues (indépendamment des
        # cases cochées) — pour que basculer Tangara/RNA ne fasse jamais bouger les
        # axes de la heatmap ni de la dispersion, et permette de comparer les 2
        # méthodes sur une échelle strictement identique (demandé).
        horizons_echelle = sorted({l["horizon"] for l in lignes_toutes_methodes}, key=_horizon_en_minutes)
        seuils_echelle = sorted({l["seuil_c1"] for l in lignes_toutes_methodes})
        dqp_max_echelle = max((abs(l["dqp"]) for l in lignes_toutes_methodes if l["dqp"] is not None),
                              default=None)

        # -- Heatmap horizon x seuil (score moyen, toutes méthodes confondues) --------
        # La colorbar doit être retirée AVANT ax_heatmap.clear() : Colorbar.remove()
        # s'appuie sur l'axes "parent" (ax_heatmap) pour restaurer sa place dans la
        # grille de subplots — une fois cet axes vidé par clear(), remove() plante
        # (AttributeError, constaté en conditions réelles au 2e rafraîchissement).
        if etat_colorbar["cb"] is not None:
            etat_colorbar["cb"].remove()
            etat_colorbar["cb"] = None
        ax_heatmap.clear()
        horizons = horizons_echelle
        seuils = seuils_echelle
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
            ax_heatmap.set_ylabel("Seuil de calage (m³/s)", fontsize=8)
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
                explication_score(poids, asymetrie_dtp, agregation=_agregation_active(app)) + "\n\n"
                "Note sur la heatmap : chaque case est la MOYENNE des méthodes (T et/ou "
                "R) qui partagent cet horizon et ce seuil — le cadre jaune désigne, lui, "
                "la MEILLEURE COMBINAISON INDIVIDUELLE (une seule méthode). Une case peut "
                "donc paraître plus sombre (meilleure en apparence) que la case encadrée "
                "sans contradiction : elle reflète 2 méthodes moyennement bonnes, alors "
                "que le cadre désigne la seule méthode la plus performante prise seule."
            )
            icone_info_axe(fig, canvas, etat_icones, "heatmap",
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
        # Échelles fixes (voir horizons_echelle/dqp_max_echelle ci-dessus) : la marge
        # en X reprend le même calcul que largeur_boite pour rester cohérente avec
        # l'espacement réel des horizons.
        if positions:
            marge_x = max(min(ecarts) * 0.6, 1) if ecarts else max(positions[-1] * 0.05, 1)
            ax_dispersion.set_xlim(positions[0] - marge_x, positions[-1] + marge_x)
        if dqp_max_echelle is not None:
            ax_dispersion.set_ylim(0, dqp_max_echelle * 1.05)

        canvas.draw_idle()

        # Regroupement par combinaison des lignes crue-par-crue déjà chargées
        # (lignes_ok), pour compter combien SOUS-estiment (dqp < 0, pic simulé plus
        # bas que l'observé) vs SURestiment (dqp > 0) le débit — demandé, colonne
        # "Sous/sur-estim." du tableau ci-dessous. Un seul passage sur lignes_ok
        # plutôt qu'un filtre répété par combinaison (potentiellement des centaines).
        lignes_par_combi = {}
        for l in lignes_ok:
            lignes_par_combi.setdefault((l["horizon"], l["seuil_c1"], l["methode"]), []).append(l)

        def _fmt(valeur, decimales=2):
            return f"{valeur:.{decimales}f}" if valeur is not None else "—"

        lignes_tableau = []
        for s in scores_mediane:  # toutes les combinaisons (mêmes identités des 2 côtés)
            cle = (s.horizon, s.seuil_c1, s.methode)
            s_moy = scores_moyenne_par_cle.get(cle)
            lignes_combi = lignes_par_combi.get(cle, [])
            nb_sous = sum(1 for l in lignes_combi if l["dqp"] is not None and l["dqp"] < 0)
            nb_sur = sum(1 for l in lignes_combi if l["dqp"] is not None and l["dqp"] > 0)
            m_med, m_moy = s.erreurs_agregees, (s_moy.erreurs_agregees if s_moy else {})
            lignes_tableau.append({
                "horizon": s.horizon, "seuil": s.seuil_c1, "methode": s.methode,
                "score_med": s.score, "score_moy": s_moy.score if s_moy else None,
                "nb_crues": s.nb_crues, "sous_sur_tri": nb_sous,
                "sous_sur_texte": f"{nb_sous} / {nb_sur}",
                "dqp_med": m_med.get("dqp"), "dqp_moy": m_moy.get("dqp"),
                "dt_med": m_med.get("dtp"), "dt_moy": m_moy.get("dtp"),
            })

        # Tri générique par colonne (clic sur un en-tête, voir _trier_tableau) — None
        # toujours en dernier quel que soit le sens (repli à 0, jamais utilisé par les
        # colonnes non numériques horizon/seuil/methode/sous_sur, jamais None).
        # "horizon" trié par sa durée réelle en minutes (_horizon_en_minutes), pas
        # alphabétiquement ("10J..." < "2J..." donnerait un ordre chronologique faux.
        def _cle_tri(ligne):
            col = etat_tri["colonne"]
            if col == "horizon":
                v = _horizon_en_minutes(ligne["horizon"])
            elif col == "sous_sur":
                v = ligne["sous_sur_tri"]
            else:
                v = ligne.get(col)
            return (v is None, v if v is not None else 0)

        lignes_tableau.sort(key=_cle_tri)
        if not etat_tri["croissant"]:
            lignes_tableau.reverse()

        tableau.delete(*tableau.get_children())
        for l in lignes_tableau:  # toutes les combinaisons -- l'ascenseur permet de tout parcourir
            tableau.insert("", tk.END, values=(
                l["horizon"], f"{l['seuil']:.2f}", l["methode"],
                _fmt(l["score_med"], 4), _fmt(l["score_moy"], 4),
                l["nb_crues"], l["sous_sur_texte"],
                _fmt(l["dqp_med"]), _fmt(l["dqp_moy"]), _fmt(l["dt_med"]), _fmt(l["dt_moy"]),
            ))

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
    # Sélection multiple déjà possible (selectmode=EXTENDED, Ctrl/Maj + clic) — ajout
    # demandé d'un ascenseur vertical toujours visible : au-delà de `height` lignes, les
    # combinaisons supplémentaires n'étaient accessibles qu'à la molette, sans aucun
    # indice qu'il y en avait plus à voir.
    cadre_liste_combis = tk.Frame(r2, bg=bg)
    cadre_liste_combis.pack(side=tk.LEFT, padx=(2, 8))
    liste_combis = tk.Listbox(cadre_liste_combis, selectmode=tk.EXTENDED, height=5, width=34,
                               exportselection=False)
    barre_v_combis = tk.Scrollbar(cadre_liste_combis, orient=tk.VERTICAL,
                                   command=liste_combis.yview)
    liste_combis.config(yscrollcommand=barre_v_combis.set)
    liste_combis.pack(side=tk.LEFT, fill=tk.Y)
    barre_v_combis.pack(side=tk.LEFT, fill=tk.Y)
    cadre_boutons_combi = tk.Frame(r2, bg=bg)
    cadre_boutons_combi.pack(side=tk.LEFT, padx=(0, 12))
    ttk.Button(cadre_boutons_combi, text="Toutes",
               command=lambda: _selectionner_toutes(True)).pack(fill=tk.X, pady=1)
    ttk.Button(cadre_boutons_combi, text="Aucune",
               command=lambda: _selectionner_toutes(False)).pack(fill=tk.X, pady=1)
    ttk.Button(r2, text="Tracer", command=lambda: _tracer()).pack(side=tk.LEFT, anchor="n")

    var_vigilance = tk.BooleanVar(value=True)
    ttk.Checkbutton(r2, text="Afficher les seuils de vigilance", variable=var_vigilance,
                     command=lambda: _tracer()).pack(side=tk.LEFT, padx=(12, 0), anchor="n")

    ligne_indicateurs = tk.Frame(frame)
    ligne_indicateurs.pack(fill=tk.X, padx=10, pady=(4, 0))
    var_indicateurs = tk.StringVar(value="")
    tk.Label(ligne_indicateurs, textvariable=var_indicateurs, font=("TkDefaultFont", 9, "bold"),
             wraplength=850, justify=tk.LEFT).pack(side=tk.LEFT, anchor="n")
    bouton_info(ligne_indicateurs, "Configuration en place",
                _TEXTE_INFO_CONFIGURATION_EN_PLACE).pack(side=tk.LEFT, padx=(4, 0), anchor="n")

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
    # Grille de Label (pas un ttk.Treeview) : demandé de colorer une CELLULE isolée
    # (la valeur min de dQP/dT) plus intensément que le reste de sa ligne — un
    # ttk.Treeview ne permet de styler qu'une ligne ENTIÈRE via un tag, jamais une
    # cellule seule (limitation Tkinter, pas de contournement propre).
    inn_max, bg_max = make_section(frame, "Maximum de chaque courbe tracée", "gris")
    cadre_tableau_max = tk.Frame(inn_max, bg=bg_max)
    cadre_tableau_max.pack(fill=tk.BOTH, expand=True)

    _COLONNES_MAX = (("Courbe", 32, "w"), ("Max (m³/s)", 12, "center"),
                      ("Horodatage du max", 18, "center"),
                      ("dQP vs observé (%)", 16, "center"), ("dT vs observé (pdt)", 16, "center"))
    _COULEUR_LIGNE_MEILLEURE = "#FFFDE0"   # jaune très pâle ("plus transparent", demandé)
    _COULEUR_CELLULE_MEILLEURE = "#FFD600"  # jaune intense, sur la seule cellule min

    canvas_max = tk.Canvas(cadre_tableau_max, height=170, highlightthickness=0, bg="white")
    ascenseur_max = ttk.Scrollbar(cadre_tableau_max, orient=tk.VERTICAL, command=canvas_max.yview)
    canvas_max.configure(yscrollcommand=ascenseur_max.set)
    canvas_max.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur_max.pack(side=tk.RIGHT, fill=tk.Y)
    grille_max = tk.Frame(canvas_max, bg="white")
    fenetre_grille_max = canvas_max.create_window((0, 0), window=grille_max, anchor="nw")
    grille_max.bind("<Configure>", lambda e: canvas_max.configure(scrollregion=canvas_max.bbox("all")))
    canvas_max.bind("<Configure>", lambda e: canvas_max.itemconfig(fenetre_grille_max, width=e.width))

    def _molette_max(e):
        canvas_max.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas_max.bind("<Enter>", lambda e: canvas_max.bind_all("<MouseWheel>", _molette_max))
    canvas_max.bind("<Leave>", lambda e: canvas_max.unbind_all("<MouseWheel>"))

    # ── Comparaison des instants de rejeu (rejeu à plusieurs instants avant le pic) ──
    # Un panneau par instant testé (référence + instants supplémentaires configurés en
    # Paramétrage), même crue et mêmes combinaisons sélectionnées que le graphique
    # principal ci-dessus — pour visualiser directement si le comportement du modèle
    # change selon qu'il démarre bien en amont du pic ou en pleine montée de crue
    # (demande explicite de l'utilisateur, 27/08/2026). Volontairement plus simple que
    # le graphique principal (pas de survol interactif, pas de tableau récapitulatif
    # par panneau) : la comparaison visuelle entre panneaux est le seul objectif ici.
    inn_instants, bg_instants = make_section(
        frame, "Comparaison des instants de rejeu (avant le pic)", "teal")
    var_statut_instants = tk.StringVar(value="")
    tk.Label(inn_instants, textvariable=var_statut_instants, bg=bg_instants, fg="#555555",
             font=("TkDefaultFont", 8, "italic"), wraplength=900, justify=tk.LEFT).pack(anchor="w")
    fig_instants = Figure(figsize=(9, 6), dpi=100)
    canvas_instants = FigureCanvasTkAgg(fig_instants, master=inn_instants)
    canvas_instants.get_tk_widget().pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    for j, (libelle, largeur, _anchor) in enumerate(_COLONNES_MAX):
        tk.Label(grille_max, text=libelle, font=("TkDefaultFont", 9, "bold"), bg="#E0E0E0",
                 relief=tk.RIDGE, borderwidth=1, width=largeur).grid(row=0, column=j, sticky="nsew")

    etat_tableau_max = {"lignes": []}  # liste de listes de Label (une sous-liste par ligne de données)

    def _vider_tableau_max():
        for ligne in etat_tableau_max["lignes"]:
            for lbl in ligne:
                lbl.destroy()
        etat_tableau_max["lignes"] = []

    def _ajouter_ligne_max(valeurs):
        """Ajoute une ligne de données, retourne sa liste de Label (index 3 = cellule
        dQP, index 4 = cellule dT — utilisé ensuite pour le surlignage ciblé)."""
        rang = len(etat_tableau_max["lignes"]) + 1  # +1 : la ligne 0 est l'en-tête
        labels_ligne = []
        for j, (_libelle, largeur, anchor) in enumerate(_COLONNES_MAX):
            lbl = tk.Label(grille_max, text=str(valeurs[j]), bg="white", anchor=anchor,
                            width=largeur, relief=tk.FLAT, borderwidth=1)
            lbl.grid(row=rang, column=j, sticky="nsew", padx=1, pady=1)
            labels_ligne.append(lbl)
        etat_tableau_max["lignes"].append(labels_ligne)
        return labels_ligne

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
        paths, _manquants = construire_grp_paths(app)
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

    def _tracer_instants():
        """Grille 2 colonnes (référence + instants supplémentaires configurés en
        Paramétrage), un panneau par instant, pour la crue et les combinaisons
        actuellement sélectionnées dans le graphique principal ci-dessus. Échelle Y
        commune à tous les panneaux (Qobs + Qsim de tous les instants confondus),
        indispensable pour comparer directement l'amplitude d'un panneau à l'autre."""
        fig_instants.clear()
        decalages = app.config_data.get("parametrage", {}).get("decalages_pic_heures", [])
        if not decalages:
            var_statut_instants.set(
                "Aucun instant supplémentaire configuré — voir Paramétrage > "
                "\"Instants de rejeu supplémentaires (avant le pic)\".")
            canvas_instants.draw_idle()
            return

        paths, _manquants = construire_grp_paths(app)
        code_pdt = _pas_de_temps_courant()
        crue_iso = _crue_iso_courante()
        if paths is None or not code_pdt or not crue_iso:
            var_statut_instants.set("")
            canvas_instants.draw_idle()
            return

        combis = getattr(liste_combis, "_valeurs", [])
        selection = liste_combis.curselection()
        combis_selectionnees = [combis[i] for i in selection] if combis else []
        if not combis_selectionnees:
            var_statut_instants.set(
                "Sélectionnez au moins une combinaison ci-dessus pour comparer ses "
                "instants de rejeu sur cette crue.")
            canvas_instants.draw_idle()
            return

        try:
            evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
        except (FileNotFoundError, CriteresPerfError):
            var_statut_instants.set("")
            canvas_instants.draw_idle()
            return
        evt = next((e for e in evenements if e.date_deb.isoformat() == crue_iso), None)
        if evt is None:
            var_statut_instants.set("")
            canvas_instants.draw_idle()
            return
        chemin_serie = os.path.join(paths.evenements_dir(code_pdt),
                                     f"{paths.code_site}-EV{evt.num_evt:04d}.DAT")
        try:
            serie_obs = parse_evenement_serie(chemin_serie)
        except (FileNotFoundError, CriteresPerfError):
            serie_obs = []

        labels_instants = [results_store.INSTANT_REFERENCE] + [f"H-{h:g}" for h in decalages]
        libelles_instants = {results_store.INSTANT_REFERENCE: "Référence (~2j avant le pic)"}
        libelles_instants.update({f"H-{h:g}": f"Pic − {h:g} h" for h in decalages})

        # Précharge toutes les séries simulées AVANT de tracer quoi que ce soit : la
        # comparaison entre panneaux n'a de sens qu'avec une échelle Y commune,
        # calculée sur l'ensemble des instants plutôt que panneau par panneau.
        series_par_instant = {}
        toutes_valeurs = [p[2] for p in serie_obs if p[2] is not None]
        with results_store.db_session() as conn:
            for label in labels_instants:
                series_par_instant[label] = []
                for h, s, m, combinaison_id in combis_selectionnees:
                    serie_sim = results_store.charger_serie(
                        conn, combinaison_id, crue_iso, "sim", instant_label=label)
                    series_par_instant[label].append((h, s, m, serie_sim))
                    toutes_valeurs.extend(p[1] for p in serie_sim if p[1] is not None)
        y_max = max(toutes_valeurs) * 1.1 if toutes_valeurs else None

        n = len(labels_instants)
        ncols = 2 if n > 1 else 1
        nrows = -(-n // ncols)  # division entière arrondie au supérieur
        axes = fig_instants.subplots(nrows, ncols, squeeze=False)
        seuils = app.config_data.get("seuils_q", {})

        for idx, label in enumerate(labels_instants):
            ax_i = axes[idx // ncols][idx % ncols]
            if serie_obs:
                ax_i.plot([p[0] for p in serie_obs], [p[2] for p in serie_obs],
                          color=_COULEUR_OBS, lw=1.4, label="Q observé")
            une_courbe_sim = False
            for i, (h, s, m, serie_sim) in enumerate(series_par_instant[label]):
                if not serie_sim:
                    continue
                une_courbe_sim = True
                couleur = PALETTE_COURBES[i % len(PALETTE_COURBES)]
                ax_i.plot([p[0] for p in serie_sim], [p[1] for p in serie_sim],
                          color=couleur, lw=1.1, ls="--", label=f"{h}/{s:.2f}/{m}")
            if y_max is not None:
                ax_i.set_ylim(0, y_max)
            if var_vigilance.get():
                for cle, _libelle_seuil, couleur in LIBELLES_SEUILS_Q:
                    val = seuils.get(cle)
                    if val is None or (y_max is not None and val > y_max * 1.3):
                        continue
                    est_zt = cle.startswith("zt_")
                    ax_i.axhline(val, color=couleur, lw=0.8 if est_zt else 1.0,
                                 ls=":" if est_zt else "-", alpha=0.75)
            ax_i.set_title(libelles_instants[label], fontsize=8.5)
            ax_i.tick_params(axis="both", labelsize=6.5)
            ax_i.grid(True, alpha=0.3)
            if not serie_obs and not une_courbe_sim:
                ax_i.text(0.5, 0.5, "Pas encore de données", transform=ax_i.transAxes,
                          ha="center", va="center", fontsize=8, color="#888888")
            if idx == 0:
                ax_i.legend(fontsize=6, loc="upper right")

        for idx in range(n, nrows * ncols):  # masque les cases vides si n est impair
            axes[idx // ncols][idx % ncols].axis("off")

        fig_instants.autofmt_xdate()
        fig_instants.subplots_adjust(hspace=0.55, wspace=0.25, bottom=0.12)
        var_statut_instants.set(
            f"{n} instant(s) comparé(s) pour {len(combis_selectionnees)} combinaison(s) "
            "sélectionnée(s) — échelle Y commune aux panneaux.")
        canvas_instants.draw_idle()

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
        _vider_tableau_max()
        var_indicateurs.set("")
        paths, _manquants = construire_grp_paths(app)
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
                _ajouter_ligne_max((
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
                couleur = PALETTE_COURBES[i % len(PALETTE_COURBES)]
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
                    labels_ligne = _ajouter_ligne_max((
                        libelle, f"{valeur_max:.1f}", f"{date_max:%d/%m/%Y %H:%M}",
                        dqp_txt, dt_txt))
                    lignes_resume.append((labels_ligne, abs(dqp_val) if dqp_val is not None else None,
                                           abs(dt_val) if dt_val is not None else None))

        # Repère visuel sur la courbe SIMULÉE la plus proche de l'observé — séparément
        # pour dQP et dT, qui peuvent désigner 2 courbes différentes (une combinaison
        # peut avoir le meilleur pic en débit sans avoir le meilleur calage temporel, et
        # inversement). Toute la ligne en jaune pâle, et en PLUS la seule cellule de la
        # valeur min (dQP ou dT) en jaune intense — Q observé exclu de la comparaison
        # (son dQP/dT est toujours 0 par construction, pas un vrai résultat).
        candidats_dqp = [(lg, v) for lg, v, _dt in lignes_resume if v is not None]
        if candidats_dqp:
            meilleure_ligne, _ = min(candidats_dqp, key=lambda t: t[1])
            for lbl in meilleure_ligne:
                lbl.configure(bg=_COULEUR_LIGNE_MEILLEURE, font=("TkDefaultFont", 9, "bold"))
            meilleure_ligne[3].configure(bg=_COULEUR_CELLULE_MEILLEURE)  # colonne dQP
        candidats_dt = [(lg, v) for lg, _dqp, v in lignes_resume if v is not None]
        if candidats_dt:
            meilleure_ligne, _ = min(candidats_dt, key=lambda t: t[1])
            for lbl in meilleure_ligne:
                if str(lbl.cget("bg")) == "white":  # ne pas écraser un surlignage dQP déjà posé
                    lbl.configure(bg=_COULEUR_LIGNE_MEILLEURE, font=("TkDefaultFont", 9, "bold"))
            meilleure_ligne[4].configure(bg=_COULEUR_CELLULE_MEILLEURE)  # colonne dT

        # Les 6 seuils de vigilance en débit (jaune/orange/rouge + leurs zones de
        # transition ZT) — même code couleur que l'onglet Configuration (une couleur par
        # niveau, ZT et seuil principal partagent la teinte), différenciés par le style
        # de trait (pointillé pour la ZT, plein pour le seuil principal). Case à cocher
        # "Afficher les seuils de vigilance" (demandé) pour les masquer temporairement
        # sans perdre l'échelle Y commune, qui reste calculée dans tous les cas.
        seuils = app.config_data.get("seuils_q", {})
        # Échelle Y COMMUNE à toutes les crues (Qmax observé+simulé de l'ensemble des
        # crues, +10%, calculé une fois par _rafraichir_crues) plutôt que réajustée à
        # chaque crue affichée — demandé explicitement pour comparer directement
        # l'amplitude d'un épisode à l'autre. Repli sur le max de CETTE crue si le
        # calcul global a échoué (ex. aucune série simulée archivée pour l'instant).
        y_max = etat_y_max["global"] or max(toutes_valeurs, default=None)
        if y_max is not None:
            ax.set_ylim(0, y_max)
        if var_vigilance.get():
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

        _tracer_instants()

    def _on_pdt_change(*_evt):
        sauvegarder_dernier_pdt(app, _pas_de_temps_courant(), source=_pdt_change_externe)
        _rafraichir_crues()

    def _pdt_change_externe(code_pdt):
        # Le pas de temps a été changé dans un AUTRE onglet (Crues ou Analyse crues
        # affl.) — aligne ce combo sans re-notifier (déjà fait par la source).
        pdt_list_actuelle = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        libelle = next((p["libelle"] for p in pdt_list_actuelle if p["code"] == code_pdt), None)
        if libelle and var_pdt.get() != libelle:
            var_pdt.set(libelle)
            _rafraichir_crues()

    combo_pdt.bind("<<ComboboxSelected>>", _on_pdt_change)
    combo_crue.bind("<<ComboboxSelected>>", lambda *_: (_rafraichir_combis(), _tracer()))
    enregistrer_observateur_pdt(app, _pdt_change_externe)

    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
    combo_pdt["values"] = [p["libelle"] for p in pdt_list]
    libelle_init = libelle_dernier_pdt(app, pdt_list)
    if libelle_init:
        var_pdt.set(libelle_init)
    _rafraichir_crues()
    return _rafraichir_crues  # exposé pour build_tab_dashboard (changement de sous-onglet,
                               # et main.App.on_resultats_changed après une suppression de
                               # combinaisons) — jusqu'ici jamais rafraîchi de l'extérieur,
                               # une combinaison supprimée pouvait rester listée/sélectionnée.


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
    # graphique (voir icone_info_axe ci-dessous), a été retirée d'ici. Le tracé se
    # déclenche aussi automatiquement à chaque changement de sélection (horizon ou
    # méthode) — ce bouton reste utile pour forcer un nouveau tracé sans changer la
    # sélection (ex. après une nouvelle campagne).
    ttk.Button(r, text="Tracer", command=lambda: _rafraichir_et_tracer()).pack(side=tk.LEFT, padx=(20, 0))

    fig = Figure(figsize=(9, 4), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    etat_icones = {}

    _LIGNE_PAR_METHODE = {"T": "-", "R": "--"}  # trait plein = Tangara, pointillé = RNA

    def _rafraichir_listes():
        """Recharge les horizons/méthodes disponibles depuis la base — appelée à chaque
        clic sur "Tracer" (et depuis _au_changement_sous_onglet, voir build_tab_dashboard),
        pas seulement à la construction de l'onglet : jusqu'ici, les listes restaient
        vides après les tout premiers lancements d'une campagne tant que l'outil n'était
        pas fermé/rouvert (signalé explicitement). Conserve la sélection courante quand
        les valeurs existent encore ; ne retombe sur la sélection par défaut (1er
        horizon, les 2 méthodes) que si rien n'était encore sélectionnable (listes vides)
        ou que la sélection précédente a disparu."""
        lignes, _ = _charger_resultats(app)
        horizons = sorted({l["horizon"] for l in lignes}, key=_horizon_en_minutes)
        methodes = sorted({l["methode"] for l in lignes})

        horizons_avant = {liste_horizons.get(i) for i in liste_horizons.curselection()}
        methodes_avant = {liste_methodes.get(i) for i in liste_methodes.curselection()}

        liste_horizons.delete(0, tk.END)
        for h in horizons:
            liste_horizons.insert(tk.END, h)
        horizons_a_garder = horizons_avant & set(horizons)
        for i, h in enumerate(horizons):
            if h in horizons_a_garder or (not horizons_a_garder and i == 0):
                liste_horizons.selection_set(i)  # 1 seul horizon par défaut, pour rester lisible

        liste_methodes.delete(0, tk.END)
        for m in methodes:
            liste_methodes.insert(tk.END, m)
        methodes_a_garder = methodes_avant & set(methodes)
        for i, m in enumerate(methodes):
            if m in methodes_a_garder or not methodes_a_garder:
                liste_methodes.selection_set(i)  # les 2 méthodes par défaut : comparaison T/R d'emblée

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
        scores = calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp, agregation=_agregation_active(app))
        if not scores:
            canvas.draw_idle()
            return

        # Couleur = horizon (constante entre les 2 méthodes d'un même horizon, pour les
        # associer visuellement), style de trait = méthode (plein Tangara, pointillé
        # RNA) — permet de comparer aussi bien tous les horizons pour une méthode fixée
        # que les 2 méthodes pour un horizon fixé, sur le même graphique.
        for i, horizon in enumerate(horizons_selectionnes):
            couleur = PALETTE_COURBES[i % len(PALETTE_COURBES)]
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
        icone_info_axe(fig, canvas, etat_icones, "y", 0.06, 0.88,
                         "Score composite",
                         explication_score(poids, asymetrie_dtp, agregation=_agregation_active(app)))
        canvas.draw_idle()

    # Retrace automatiquement dès que la sélection change (horizon ou méthode) — plus
    # besoin de recliquer sur Tracer, "<<ListboxSelect>>" se déclenche aussi bien pour
    # un clic simple que pour une sélection multiple au glisser/Ctrl-clic. Un changement
    # de sélection ne recharge PAS les listes elles-mêmes (_tracer seul) : recharger à
    # chaque clic dans une liste réinitialiserait la sélection qu'on est justement en
    # train de faire.
    liste_horizons.bind("<<ListboxSelect>>", lambda *_: _tracer())
    liste_methodes.bind("<<ListboxSelect>>", lambda *_: _tracer())

    def _rafraichir_et_tracer():
        _rafraichir_listes()
        _tracer()

    _rafraichir_et_tracer()
    return _rafraichir_et_tracer  # exposé pour build_tab_dashboard (pondération ET changement de sous-onglet)


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
                lambda: explication_score(*_poids_actifs(app)[:2],
                                           agregation=_agregation_active(app))).pack(
        side=tk.LEFT, padx=(6, 0))
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
    gs = fig.add_gridspec(1, 2, width_ratios=(1.45, 0.8), wspace=0.35)
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
        poids, asymetrie_dtp, libelle_profil = _poids_actifs(app)
        scores = [s for s in calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp, agregation=_agregation_active(app))
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
        # Trait fin pointillé gris entre chaque point (X, Y) au plancher et son résultat
        # réel en Z (demandé) — relie visuellement l'intersection horizon×seuil à son
        # score composite, en complément de l'ombre au sol déjà posée juste au-dessus.
        for x, y, z in zip(xs, ys, zs):
            ax.plot([x, x], [y, y], [z_floor, z], color=(0.6, 0.6, 0.6), lw=0.6,
                     ls=":", alpha=0.5, zorder=1)

        _LIBELLES_METHODE = {"T": "Méthode T - Tan.", "R": "Méthode R - RNA"}
        handles_legende, labels_legende = [], []
        for methode, marqueur in _MARQUEURS_METHODE.items():
            indices = [i for i, s in enumerate(scores) if s.methode == methode]
            if not indices:
                continue
            nuage = ax.scatter(
                [xs[i] for i in indices], [ys[i] for i in indices], [zs[i] for i in indices],
                c=[zs[i] for i in indices], cmap="RdYlGn_r", vmin=0, vmax=1,
                marker=marqueur, s=70, edgecolors=(0.3, 0.3, 0.35, 0.7), linewidths=0.5,
                alpha=0.92,
            )
            handles_legende.append(nuage)
            labels_legende.append(_LIBELLES_METHODE.get(methode, f"Méthode {methode}"))
        ax.set_zlim(z_floor, max(zs) + max((max(zs) - min(zs)) * 0.08, 0.02))
        etat_colorbar["cb"] = fig.colorbar(nuage, ax=ax, shrink=0.6, pad=0.1,
                                            label="Score composite (0=meilleur)")
        # Repère posé en coordonnées FIGURE (pas données/axes) — reste donc fixe à côté
        # de la colorbar (élément 2D stable) même quand la vue 3D est tournée à la
        # souris, contrairement à un label d'axe Z qui pivote avec la vue. Position
        # recalculée depuis la vraie bbox de la colorbar (pas de coordonnées codées en
        # dur) : reste juste au-dessus d'elle quels que soient les width_ratios de la
        # grille (modifiés depuis pour agrandir le nuage 3D, voir plus haut).
        # Note ajoutée à l'explication du score : pourquoi un point peut sembler plus
        # vert (donc meilleur en apparence) que l'étoile dorée sans être réellement
        # meilleur — question posée par l'utilisateur en conditions réelles. L'or de
        # l'étoile est une couleur FIXE (repère visuel), PAS une position sur l'échelle
        # RdYlGn_r : rien ne garantit qu'il paraisse "plus vert à l'œil" qu'un point
        # voisin très proche en score, alors même que l'étoile désigne bien le score le
        # plus bas de tout l'ensemble (vérifiable dans le texte de la légende).
        texte_colorbar = (
            explication_score(poids, asymetrie_dtp) + "\n\n"
            "Note sur les couleurs : l'étoile dorée n'est PAS positionnée sur l'échelle "
            "de couleur ci-contre — c'est une couleur fixe choisie pour rester visible, "
            "indépendante du score. Un point voisin très proche en score (donc coloré "
            "d'un vert tout aussi foncé) peut ainsi sembler visuellement \"aussi bon\" ou "
            "\"meilleur\" que l'étoile sans l'être réellement : le score exact de la "
            "meilleure combinaison est celui affiché dans la légende, pas la teinte "
            "perçue à l'œil."
        )
        bbox_cb = etat_colorbar["cb"].ax.get_position()
        icone_info_axe(fig, canvas, etat_icones, "colorbar",
                         bbox_cb.x0 + 0.5 * bbox_cb.width, bbox_cb.y1 + 0.035,
                         "Score composite", texte_colorbar)

        # Meilleure combinaison mise en évidence (score le plus bas = le plus vert). Le
        # détail (paramètres + les 4 indicateurs médians qui composent son score) est
        # inclus directement dans le libellé de légende de cette étoile — matplotlib
        # affiche un label multi-lignes comme une seule entrée de légende.
        meilleur = min(scores, key=lambda s: s.score)
        m = meilleur.erreurs_agregees
        libelle_meilleure = (
            "Meilleure combinaison\n"
            f"Horizon {meilleur.horizon} / seuil {meilleur.seuil_c1:.2f} / méthode {meilleur.methode}\n"
            f"Score composite : {meilleur.score:.3f} ({libelle_profil})\n"
            f"|dQP| {m.get('dqp'):.2f}%   |dTP| {m.get('dtp'):.2f} pdt   "
            f"|VE| {m.get('ve'):.2f}%   (1−KGE) {m.get('kge'):.2f}"
        )
        etoile = ax.scatter([_horizon_en_minutes(meilleur.horizon)], [meilleur.seuil_c1], [meilleur.score],
                             marker="*", s=400, color="gold", edgecolors="#333333", linewidths=0.8)
        handles_legende.append(etoile)
        labels_legende.append(libelle_meilleure)
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
        # quelle que soit la largeur réelle de l'axe. Marge de gauche resserrée pour
        # agrandir le nuage 3D (demandé). Sur 2 colonnes (ncol=2) : avec 3 entrées,
        # matplotlib remplit la 1re colonne en premier (les 2 méthodes), la meilleure
        # combinaison se retrouve donc seule en 2e colonne — demandé explicitement,
        # sans avoir à construire un layout de légende personnalisé.
        fig.subplots_adjust(left=0.09, wspace=0.35)
        legende = ax.legend(handles=handles_legende, labels=labels_legende, loc="upper left",
                              bbox_to_anchor=(0.005, 0.98), bbox_transform=fig.transFigure,
                              fontsize=7, labelspacing=1.8, ncol=2, columnspacing=1.5, handletextpad=0.6)

        # -- Bouton "Réinitialiser la vue 3D", juste sous la légende (demandé — la vue
        # 3D se manipule à la souris et il est facile de se retrouver sous un angle peu
        # lisible sans moyen simple d'y revenir). Dessiné DANS la figure (comme l'icône
        # "i", voir icone_info_axe) plutôt qu'en widget Tkinter classique, pour rester
        # collé à la position réelle de la légende quelle que soit sa hauteur — mesurée
        # ici via get_window_extent() après un premier rendu, plutôt que devinée.
        ancien_reset = etat_icones.get("reset_vue3d")
        if ancien_reset is not None:
            marqueur_prec, cid_prec = ancien_reset
            try:
                marqueur_prec.remove()
            except Exception:
                pass
            canvas.mpl_disconnect(cid_prec)
        fig.canvas.draw()
        bbox_legende_fig = legende.get_window_extent(
            renderer=fig.canvas.get_renderer()).transformed(fig.transFigure.inverted())
        marqueur_reset = fig.text(
            bbox_legende_fig.x0, bbox_legende_fig.y0 - 0.025, "Réinitialiser la vue 3D",
            fontsize=8, color="#1A5276", ha="left", va="top", picker=True,
            bbox=dict(boxstyle="round,pad=0.35", fc="#D6EAF8", ec="#1A5276", lw=0.8))

        def _au_clic_reset(event):
            if event.artist is marqueur_reset:
                ax.view_init(elev=20, azim=-55)
                canvas.draw_idle()

        cid_reset = canvas.mpl_connect("pick_event", _au_clic_reset)
        etat_icones["reset_vue3d"] = (marqueur_reset, cid_reset)

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


# ══════════════════════════════════════════════════════════════════════════════════
# 5. Variation de la meilleure combinaison selon le nombre de crues
# ══════════════════════════════════════════════════════════════════════════════════

_TEXTE_EXPLICATION_KGE = (
    "POURQUOI LE KGE ICI, PAS LE SCORE COMPOSITE ?\n\n"
    "Le score composite est normalisé min-max À L'INTÉRIEUR DE CHAQUE N (0=meilleur/"
    "1=pire recalculé sur les 96 combinaisons pour ce N précis) : sa valeur absolue "
    "n'est donc pas comparable d'un N à l'autre, une baisse ou une hausse pourrait "
    "juste venir d'un changement d'échelle de référence, pas d'une vraie différence "
    "de performance. Le KGE, lui, a une définition FIXE, indépendante des autres "
    "combinaisons ou du sous-ensemble de crues retenu — il reste donc directement "
    "comparable d'un point à l'autre du graphique. Le score composite reste affiché "
    "dans le tableau ci-dessous (pour savoir QUI gagne à chaque N), le graphique "
    "trace le KGE (pour comparer À QUEL POINT c'est bon, de façon cohérente).\n\n"
    "─────────────────────────────\n\n"
    "EN SIMPLE\n\n"
    "Le KGE (Kling-Gupta Efficiency) est une note globale qui dit à quel point le "
    "débit simulé ressemble au débit observé. Plus il est ÉLEVÉ, meilleure est la "
    "simulation (fond vert du graphique) ; plus il est BAS ou négatif, plus elle est "
    "mauvaise (fond rouge).\n\n"
    "Repères utiles :\n"
    "  • KGE = 1  →  simulation parfaite\n"
    "  • KGE proche de 0,5 à 0,7  →  performance correcte à bonne\n"
    "  • KGE ≤ 0  →  le modèle fait moins bien que de prévoir simplement la valeur "
    "moyenne observée en permanence (très mauvais signe)\n\n"
    "─────────────────────────────\n\n"
    "PLUS TECHNIQUE\n\n"
    "KGE = 1 − √[(r−1)² + (α−1)² + (β−1)²]\n\n"
    "où, sur la période évaluée :\n"
    "  • r = coefficient de corrélation de Pearson entre débits simulés et observés "
    "(la dynamique/la forme est-elle bien reproduite ?)\n"
    "  • α = écart-type(simulé) / écart-type(observé) (la variabilité — trop lisse "
    "ou trop erratique ?)\n"
    "  • β = moyenne(simulé) / moyenne(observé) (le biais global — sur- ou "
    "sous-estimation systématique ?)\n\n"
    "Les 3 termes valent 1 en cas de simulation parfaite, donc KGE = 1. Le KGE "
    "pénalise autant une mauvaise corrélation, un mauvais biais ou une mauvaise "
    "variabilité — contrairement au critère de Nash-Sutcliffe (NSE), plus focalisé "
    "sur la seule variance. Référence : Gupta et al. (2009), Journal of Hydrology.\n\n"
    "─────────────────────────────\n\n"
    "COMMENT LIRE UN PIC SUR CE GRAPHIQUE\n\n"
    "Un pic isolé (souvent à petit N, à gauche) n'est PAS forcément le meilleur choix, "
    "pour 3 raisons qui se cumulent :\n\n"
    "  1. Chaque point est le gagnant du SCORE COMPOSITE à ce N, pas forcément celui "
    "du meilleur KGE — la combinaison affichée peut changer d'un N à l'autre (voir "
    "les étiquettes verticales), ce n'est pas le suivi d'un seul modèle fixe.\n\n"
    "  2. Une médiane sur peu de crues (petit N) est statistiquement fragile — une "
    "seule crue facile ou difficile en plus ou en moins peut faire bondir la valeur. "
    "Un pic à N=10-12 tombe souvent bien sur CE sous-ensemble précis, sans être un "
    "optimum généralisable.\n\n"
    "  3. Un modèle opérationnel doit couvrir TOUTES les crues qu'il rencontrera, pas "
    "seulement un sous-ensemble choisi après coup — se caler sur un pic à petit N "
    "risque de surapprendre quelques événements historiques précis.\n\n"
    "Les zones plus STABLES (peu de variation d'un N au suivant, en général à N "
    "élevé, à droite) sont plus dignes de confiance pour un choix opérationnel, même "
    "si leur KGE affiché n'est pas le plus haut du graphique."
)


def _build_variation_crues(frame, app):
    """Onglet demandé explicitement : comment la combinaison optimale (et sa
    performance) évolue si on ne retient que les N crues les plus fortes (Qmax
    décroissant), pour N croissant de quelques crues jusqu'au total disponible.

    Volontairement INDÉPENDANT du sélecteur "Crues dans le score" du bandeau (qui
    filtre un ensemble FIXE, choisi à la main) : ici N varie automatiquement du plus
    petit au plus grand sous-ensemble des crues les plus fortes, toujours à partir de
    TOUTES les crues disponibles. Utilise la même pondération que le reste du
    Dashboard, donc rafraîchi avec elle (voir build_tab_dashboard).

    ⚠️ Le score composite est normalisé min-max SUR LE SOUS-ENSEMBLE de chaque N (voir
    modules.score.calculer_scores) : sa valeur absolue n'est donc PAS comparable d'un N
    à l'autre (0=meilleur/1=pire est relatif à cet N précis, pas une échelle fixe). Le
    graphique trace donc le KGE MOYEN (indicateur brut, non normalisé) de la
    combinaison gagnante à chaque N — directement comparable d'un N à l'autre. Le
    score normalisé reste affiché dans le tableau pour référence, mais seulement pour
    désigner QUI gagne à ce N précis, pas pour comparer les N entre eux.
    """
    barre = tk.Frame(frame)
    barre.pack(fill=tk.X, padx=8, pady=6)
    var_statut = tk.StringVar(value="")
    tk.Label(barre, textvariable=var_statut, fg="#555555").pack(side=tk.LEFT)
    ttk.Button(barre, text="Rafraîchir", command=lambda: _rafraichir()).pack(side=tk.RIGHT)

    tk.Label(frame, font=("TkDefaultFont", 8, "italic"), fg="#555555", wraplength=1000,
             justify=tk.LEFT,
             text="Crues classées par Qmax décroissant (les plus fortes en premier). "
                  "Indépendant du sélecteur \"Crues dans le score\" en haut du Dashboard, "
                  "qui filtre un ensemble fixe choisi à la main — ici N grandit "
                  "automatiquement des crues les plus fortes jusqu'à la totalité "
                  "disponible, pour observer si la combinaison optimale reste stable.").pack(
        anchor="w", padx=10, pady=(0, 4))
    tk.Label(frame, font=("TkDefaultFont", 8, "italic"), fg="#A93226", wraplength=1000,
             justify=tk.LEFT,
             text="⚠ Un pic isolé (souvent à petit N) n'est pas forcément le meilleur "
                  "choix : la combinaison gagnante peut changer d'un N à l'autre, et une "
                  "médiane sur peu de crues est statistiquement fragile. Préférez les "
                  "zones stables (peu de variation) — voir l'icône i à côté de l'axe KGE.").pack(
        anchor="w", padx=10, pady=(0, 4))

    fig = Figure(figsize=(11, 5.4), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    ax2 = ax.twinx()  # 2e ordonnée : horizon optimal (violet) — créée une seule fois,
                       # ax.clear() ne touche pas ax2, donc ax2.clear() aussi à chaque
                       # rafraîchissement (voir _rafraichir).
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    etat_icones = {}

    inn_tab, bg_tab = make_section(frame, "Combinaison optimale par nombre de crues (N)", "gris")
    cadre_tab = tk.Frame(inn_tab, bg=bg_tab)
    cadre_tab.pack(fill=tk.BOTH, expand=True)
    tableau = ttk.Treeview(
        cadre_tab, columns=("n", "combinaison", "score", "kge", "dqp", "dtp"),
        show="headings", height=6)
    for col, libelle, largeur in (
        ("n", "N crues", 70), ("combinaison", "Combinaison gagnante", 220),
        ("score", "Score normalisé (à ce N)", 170),
        ("kge", "KGE médian (brut)", 130), ("dqp", "Médiane |dQP| (brut, %)", 160),
        ("dtp", "Médiane |dTP| (brut, pdt)", 160),
    ):
        tableau.heading(col, text=libelle)
        tableau.column(col, width=largeur, anchor="center" if col != "combinaison" else "w")
    ascenseur_tab = ttk.Scrollbar(cadre_tab, orient=tk.VERTICAL, command=tableau.yview)
    tableau.configure(yscrollcommand=ascenseur_tab.set)
    tableau.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur_tab.pack(side=tk.RIGHT, fill=tk.Y)

    # État partagé entre _rafraichir() et le sélecteur de ligne (voir _survol_selection
    # ci-dessous) : les données du dernier tracé (pour retrouver le point correspondant
    # à une ligne cliquée) et le marqueur de sélection actuellement affiché sur le
    # graphique (à retirer avant d'en poser un nouveau, ou si le tableau est vidé).
    etat_donnees = {"points": []}
    etat_selection = {"marqueur_kge": None, "marqueur_horizon": None}

    def _survol_selection(_evt=None):
        for cle in ("marqueur_kge", "marqueur_horizon"):
            if etat_selection[cle] is not None:
                try:
                    etat_selection[cle].remove()
                except Exception:
                    pass
                etat_selection[cle] = None
        selection = tableau.selection()
        if selection:
            valeurs = tableau.item(selection[0], "values")
            n_selectionne = int(valeurs[0])
            point = next((p for p in etat_donnees["points"] if p[0] == n_selectionne), None)
            if point is not None:
                _n, s = point
                kge_val = s.erreurs_agregees.get("kge")
                if kge_val is not None:
                    etat_selection["marqueur_kge"] = ax.scatter(
                        [_n], [kge_val], s=200, facecolors="none", edgecolors="#C0392B",
                        linewidths=2.2, zorder=6)
                heures = _horizon_en_minutes(s.horizon) / 60
                etat_selection["marqueur_horizon"] = ax2.scatter(
                    [_n], [heures], s=200, facecolors="none", edgecolors="#C0392B",
                    linewidths=2.2, zorder=6)
        canvas.draw_idle()

    tableau.bind("<<TreeviewSelect>>", _survol_selection)

    def _rafraichir():
        ax.clear()
        ax2.clear()
        tableau.delete(*tableau.get_children())
        etat_selection["marqueur_kge"] = None  # clear() les a déjà invalidés
        etat_selection["marqueur_horizon"] = None
        etat_donnees["points"] = []
        lignes, erreur = _charger_resultats(app)
        if erreur:
            var_statut.set(erreur)
            canvas.draw_idle()
            return
        lignes_ok = [l for l in lignes if l["statut_crue"] == "success"]
        if not lignes_ok:
            var_statut.set("Aucun résultat réussi en base pour l'instant.")
            canvas.draw_idle()
            return

        crues_info = _lister_crues_details_pour_score(app)
        crues_avec_qmax = [c for c in crues_info if c["qmax"] is not None]
        if len(crues_avec_qmax) < 3:
            var_statut.set("Pas assez de crues avec Qmax connu pour cette analyse "
                            "(minimum 3 requis, via CRITERES_PERF.DAT).")
            canvas.draw_idle()
            return
        crues_triees = sorted(crues_avec_qmax, key=lambda c: c["qmax"], reverse=True)
        isos_ordre = [c["iso"] for c in crues_triees]

        poids, asymetrie_dtp, libelle_profil = _poids_actifs(app)
        points = []  # liste de (n, ScoreCombinaison gagnant à ce n)
        for n in range(3, len(isos_ordre) + 1):
            isos_n = set(isos_ordre[:n])
            lignes_n = [l for l in lignes_ok if l["crue_date"] in isos_n]
            if not lignes_n:
                continue
            scores_n = [s for s in calculer_scores(lignes_n, poids=poids, asymetrie_dtp=asymetrie_dtp, agregation=_agregation_active(app))
                        if s.score is not None]
            if not scores_n:
                continue
            points.append((n, min(scores_n, key=lambda s: s.score)))

        if not points:
            var_statut.set("Aucun score exploitable pour construire cette analyse.")
            canvas.draw_idle()
            return

        etat_donnees["points"] = points
        var_statut.set(f"{len(points)} valeurs de N testées (de {points[0][0]} à {points[-1][0]} "
                        f"crues sur {len(isos_ordre)} disponibles) — pondération : {libelle_profil}.")

        ns = [n for n, _s in points]
        kges = [s.erreurs_agregees.get("kge") for _n, s in points]
        heures_horizon = [_horizon_en_minutes(s.horizon) / 60 for _n, s in points]
        libelles_combo = [f"{s.horizon}/{s.seuil_c1:.2f}/{s.methode}" for _n, s in points]

        ax.plot(ns, kges, color="#1F618D", lw=1.6, marker="o", markersize=3.5, zorder=3,
                label="KGE médian (gagnant)")
        ax.set_xlabel("N (crues les plus fortes retenues, Qmax décroissant)")
        ax.set_ylabel("KGE médian — combinaison gagnante (brut, non normalisé)")
        ax.set_title("Stabilité de la combinaison optimale selon le nombre de crues retenues", fontsize=9)
        ax.grid(True, alpha=0.3)

        # Fond dégradé rouge (bas = pire) -> vert (haut = meilleur) calé sur les
        # limites RÉELLEMENT affichées de l'axe Y (demandé, pour lever l'ambiguïté
        # "un KGE élevé ou faible est-il le meilleur ?" d'un coup d'œil). Semi-
        # transparent (alpha) pour ne jamais gêner la lecture de la courbe/grille
        # posées par-dessus (zorder plus élevé). xlim/ylim explicitement restaurés
        # après l'imshow : matplotlib peut sinon les recaler sur l'image elle-même.
        xlim, ylim = ax.get_xlim(), ax.get_ylim()

        # Marge verticale supplémentaire (au-delà de l'autoscale par défaut) pour
        # laisser la place aux étiquettes forcées sur CHAQUE point, toutes au-dessus
        # de la courbe (demandé) — davantage de marge en haut qu'en bas, puisque plus
        # aucune étiquette ne descend sous son point. Voir la boucle d'annotation plus bas.
        etendue_y = (ylim[1] - ylim[0]) or 0.2
        ylim = (ylim[0] - etendue_y * 0.08, ylim[1] + etendue_y * 0.32)

        degrade = np.linspace(0, 1, 256).reshape(-1, 1)
        ax.imshow(degrade, extent=(*xlim, *ylim), aspect="auto", cmap="RdYlGn",
                   alpha=0.15, zorder=0, origin="lower")
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)

        # Ligne verticale à chaque bascule (changement de combinaison gagnante) — montre
        # directement à partir de quel N l'optimum se stabilise (ou continue de changer).
        precedent = None
        for n, lib in zip(ns, libelles_combo):
            if lib != precedent:
                ax.axvline(n, color="#7B7B7B", lw=0.7, ls="--", alpha=0.6, zorder=1)
                precedent = lib

        # Étiquette forcée sur CHAQUE point (demandé) : horizon court sur une ligne,
        # "seuil/méthode" en dessous, toujours placée AU-DESSUS de la courbe (demandé —
        # la marge verticale plus généreuse en haut, ci-dessus, lui laisse la place).
        for n, kge_val, s in zip(ns, kges, [s for _n, s in points]):
            texte = f"{_libelle_horizon_court(s.horizon)}\n{s.seuil_c1:.2f}/{s.methode}"
            ax.annotate(texte, xy=(n, kge_val), xytext=(0, 8), textcoords="offset points",
                        fontsize=7, ha="center", va="bottom", color="#333333", linespacing=1.2)

        # 2e ordonnée (violette clair, semi-transparente — demandé) : horizon de la
        # combinaison gagnante à chaque N, en heures (1J = 24h) — pour voir d'un coup
        # d'œil si l'horizon optimal bouge avec le nombre de crues retenues, en plus de
        # sa seule performance (KGE).
        COULEUR_HORIZON = "#9B59B6"
        ax2.plot(ns, heures_horizon, color=COULEUR_HORIZON, alpha=0.65, lw=1.4,
                  marker="s", markersize=4, zorder=4, label="Horizon optimal")
        # ax2.clear() (voir _rafraichir) réinitialise la position de l'axe à "left" —
        # twinx() la met à "right" une seule fois, à la création : il faut la
        # réappliquer explicitement à chaque rafraîchissement.
        ax2.yaxis.tick_right()
        ax2.yaxis.set_label_position("right")
        ax2.set_ylabel("Evol. HOR optimal f(nb crues retenues au calage)", color=COULEUR_HORIZON)
        ax2.tick_params(axis="y", colors=COULEUR_HORIZON)
        heures_uniques = sorted(set(heures_horizon))
        ax2.set_yticks(heures_uniques)
        ax2.set_yticklabels(
            [f"{int(h // 24)}J" if h % 24 == 0 and h >= 24 else f"{int(h)}H" for h in heures_uniques])
        marge_y2 = (max(heures_uniques) - min(heures_uniques)) * 0.25 or 1.0
        ax2.set_ylim(min(heures_uniques) - marge_y2, max(heures_uniques) + marge_y2)

        lignes_legende, labels_legende = ax.get_legend_handles_labels()
        lignes_legende2, labels_legende2 = ax2.get_legend_handles_labels()
        ax.legend(lignes_legende + lignes_legende2, labels_legende + labels_legende2,
                   loc="lower right", fontsize=7)

        # Icône "i" à côté du titre de l'axe Y — explique ce qu'est réellement le KGE
        # (demandé), en 2 niveaux (simple puis technique) dans la même fenêtre.
        bbox_ax = ax.get_position()
        icone_info_axe(fig, canvas, etat_icones, "kge",
                         bbox_ax.x0 + 0.012, bbox_ax.y1 - 0.02,
                         "KGE (Kling-Gupta Efficiency)", _TEXTE_EXPLICATION_KGE, taille=7)

        canvas.draw_idle()

        for n, s in points:
            tableau.insert("", tk.END, values=(
                n, f"{s.horizon}/{s.seuil_c1:.2f}/{s.methode}",
                f"{s.score:.4f}",
                f"{s.erreurs_agregees.get('kge'):.3f}" if s.erreurs_agregees.get("kge") is not None else "—",
                f"{s.erreurs_agregees.get('dqp'):.2f}" if s.erreurs_agregees.get("dqp") is not None else "—",
                f"{s.erreurs_agregees.get('dtp'):.2f}" if s.erreurs_agregees.get("dtp") is not None else "—",
            ))

    _rafraichir()
    return _rafraichir  # exposé pour que build_tab_dashboard puisse retracer au changement de pondération
