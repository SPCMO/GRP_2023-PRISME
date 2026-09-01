# -*- coding: utf-8 -*-
"""Score composite de performance par combinaison (horizon, seuil_c1, méthode) — bloc 6
"Dashboard et score de synthèse".

Pour chaque combinaison, |dQP|, |dTP|, |VE|, (1-KGE) sont d'abord agrégés sur ses crues
réussies par la MÉDIANE (pas la moyenne — demandé explicitement pour rester cohérent
avec results_store.duree_par_etape, qui utilise déjà la médiane : un run/une crue
atypique fausse beaucoup plus une moyenne qu'une médiane). Chaque indicateur ainsi
agrégé est ensuite normalisé min-max sur l'ensemble des combinaisons évaluées, puis
combiné en une MOYENNE PONDÉRÉE (poids égaux par défaut, réglables) — cette 2e étape
reste une moyenne, jamais une médiane : les poids n'auraient aucun effet sur une
médiane de 4 valeurs (voir PROFILS_PONDERATION, profil "métier" où dQP compte 3x plus
que VE/KGE — un réglage qui n'a de sens qu'avec une moyenne pondérée). Un score PLUS
BAS = MEILLEURE performance (0 = la meilleure valeur observée sur chaque indicateur,
1 = la pire). Fonction pure, indépendante de l'UI et de results_store, pour rester
testable isolément.
"""

from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Optional

INDICATEURS = ("dqp", "dtp", "ve", "kge")
POIDS_PAR_DEFAUT = {"dqp": 1.0, "dtp": 1.0, "ve": 1.0, "kge": 1.0}
ASYMETRIE_DTP_PAR_DEFAUT = {"retard": 1.0, "avance": 1.0}  # symétrique : 1.0/1.0

# Profils de pondération proposés dans le Dashboard (sélecteur partagé, voir
# ui/tab_dashboard.py) — "egal" est le comportement d'origine, jamais modifié en place
# (l'utilisateur a explicitement demandé de ne pas changer le score par défaut).
# "metier" reprend l'analyse comparative demandée par les modélisateurs : dQP pesé 3x
# plus qu'VE/KGE, dTP 2x plus, et un retard de prévision pénalisé plus qu'une avance de
# même ampleur (0.75 en avance contre 1.25 en retard).
PROFILS_PONDERATION = {
    "egal": {
        "libelle": "Poids égaux (par défaut)",
        "poids": dict(POIDS_PAR_DEFAUT),
        "asymetrie_dtp": dict(ASYMETRIE_DTP_PAR_DEFAUT),
    },
    "metier": {
        "libelle": "Pondération métier (dQP favorisé, retard pénalisé)",
        "poids": {"dqp": 3.0, "dtp": 2.0, "ve": 1.0, "kge": 1.0},
        "asymetrie_dtp": {"retard": 1.25, "avance": 0.75},
    },
}


def explication_score(poids=None, asymetrie_dtp=None):
    """Texte affiché dans le bouton "ⓘ" à côté de chaque "Score composite" du
    Dashboard — généré à partir de la pondération RÉELLEMENT active (voir
    ui/tab_dashboard.py, sélecteur de profil), pour ne jamais afficher une description
    qui ne correspond plus au calcul effectivement fait."""
    poids = poids or POIDS_PAR_DEFAUT
    asymetrie_dtp = asymetrie_dtp or ASYMETRIE_DTP_PAR_DEFAUT
    poids_egaux = all(v == poids["dqp"] for v in poids.values())
    dtp_symetrique = asymetrie_dtp["retard"] == asymetrie_dtp["avance"]

    if poids_egaux:
        texte_poids = "3. Le score final est la MOYENNE des 4 indicateurs normalisés, à POIDS ÉGAUX (25% chacun)."
    else:
        total = sum(poids.values())
        detail = ", ".join(f"{ind.upper()} {v/total*100:.0f}%" for ind, v in poids.items())
        texte_poids = (
            f"3. Le score final est la MOYENNE PONDÉRÉE des 4 indicateurs normalisés — "
            f"poids actuels : {detail}."
        )
    if dtp_symetrique:
        texte_dtp = (
            "4. dTP est traité de façon SYMÉTRIQUE : une avance de 2 pas de temps compte "
            "exactement comme un retard de 2 pas de temps — le score ne privilégie pas "
            "l'avance sur le retard, ni l'inverse."
        )
    else:
        texte_dtp = (
            f"4. dTP est traité de façon ASYMÉTRIQUE : un retard est multiplié par "
            f"{asymetrie_dtp['retard']:.2f} avant normalisation, une avance par "
            f"{asymetrie_dtp['avance']:.2f} — "
            + ("le retard est pénalisé davantage qu'une avance de même ampleur."
               if asymetrie_dtp["retard"] > asymetrie_dtp["avance"]
               else "l'avance est pénalisée davantage qu'un retard de même ampleur.")
        )

    return (
        "Score composite (0 = meilleur, 1 = pire)\n\n"
        "Calculé UNIQUEMENT sur les combinaisons actuellement affichées à l'écran (jamais "
        "sur une seule, ni sur toute la base) :\n\n"
        "1. Pour chaque combinaison, on calcule l'erreur MÉDIANE (sur ses crues réussies, "
        "pas la moyenne — une crue atypique ne doit pas fausser le score plus qu'il ne "
        "faut) de 4 indicateurs : |dQP| (écart % sur le débit de pointe), |dTP| (écart en "
        "pas de temps sur l'heure du pic), |VE| (écart % sur le volume écoulé), et "
        "(1 − KGE) (KGE théoriquement ≤ 1 dans le meilleur cas).\n\n"
        "2. Chaque indicateur est normalisé entre 0 (la MEILLEURE valeur observée parmi "
        "TOUTES les combinaisons affichées) et 1 (la pire) — c'est une échelle RELATIVE à "
        "ce qui est affiché : ajouter ou retirer des combinaisons du graphique/tableau peut "
        "donc changer le score de toutes les autres.\n\n"
        f"{texte_poids}\n\n"
        f"{texte_dtp}\n\n"
        "Pondération réglable dans le Dashboard (sélecteur de profil + bouton Réglages…).\n"
        "Voir modules/score.py (fonction calculer_scores) pour le détail du calcul."
    )


# Rétro-compatibilité : ancien nom, toujours la version "poids égaux" (comportement
# d'origine — voir ui/tab_dashboard.py pour la version dynamique selon le profil actif).
EXPLICATION_SCORE = explication_score()


def config_ponderation_par_defaut():
    """État initial de app.config_data["score"] (persisté) — "egal" reproduit
    exactement le comportement d'origine, jamais changé sans action explicite de
    l'utilisateur. "poids_personnalise"/"asymetrie_personnalisee" pré-remplis avec le
    profil "metier" pour donner un point de départ sensé au réglage personnalisé
    plutôt que de démarrer sur des poids tous à zéro."""
    return {
        "profil": "egal",
        "poids_personnalise": dict(PROFILS_PONDERATION["metier"]["poids"]),
        "asymetrie_personnalisee": dict(PROFILS_PONDERATION["metier"]["asymetrie_dtp"]),
    }


def resoudre_ponderation(config_score):
    """`config_score` : dict au format de config_ponderation_par_defaut() (ou None).
    Retourne (poids, asymetrie_dtp, libellé) — la pondération RÉELLEMENT active,
    utilisée aussi bien par le Dashboard (ui/tab_dashboard.py) que par la fenêtre
    "Combinaisons déjà réalisées" de l'onglet Campagne (ui/tab_orchestration.py), pour
    que le score composite désigne la même chose partout dans l'outil."""
    config_score = config_score or {}
    profil = config_score.get("profil", "egal")
    if profil == "personnalise":
        poids = config_score.get("poids_personnalise") or PROFILS_PONDERATION["metier"]["poids"]
        asymetrie = config_score.get("asymetrie_personnalisee") or PROFILS_PONDERATION["metier"]["asymetrie_dtp"]
        return poids, asymetrie, "Personnalisé"
    profil_connu = PROFILS_PONDERATION.get(profil, PROFILS_PONDERATION["egal"])
    return profil_connu["poids"], profil_connu["asymetrie_dtp"], profil_connu["libelle"]


def filtrer_par_crues(lignes_resultats, crues_incluses):
    """Restreint `lignes_resultats` aux seules lignes dont "crue_date" figure dans
    `crues_incluses` (itérable de dates ISO). `crues_incluses` vide ou None désactive le
    filtre (toutes les crues incluses — comportement d'origine, avant l'ajout de cette
    fonctionnalité). Permet de recalculer le score composite sur un sous-ensemble de
    crues (ex. exclure un épisode atypique) SANS reprendre le calage/rejeu GRP : le
    score ne dépend que des dQP/dTP/VE/KGE déjà stockés en base par (combinaison, crue),
    donc ce filtre suffit — voir ui.tab_dashboard._filtrer_lignes_score et
    ui.tab_orchestration._charger_combinaisons_completes, qui utilisent la même
    sélection que le Dashboard pour rester cohérents entre eux."""
    if not crues_incluses:
        return lignes_resultats
    crues_set = set(crues_incluses)
    return [l for l in lignes_resultats if l["crue_date"] in crues_set]


@dataclass
class ScoreCombinaison:
    horizon: str
    seuil_c1: float
    methode: str
    score: Optional[float]     # None si aucun indicateur exploitable
    nb_crues: int
    medianes_erreur: Dict[str, Optional[float]] = field(default_factory=dict)
    # medianes_erreur : |dQP|, |dTP|, |VE|, (1-KGE) médians (non normalisés) — pour
    # affichage humain à côté du score normalisé (peu lisible tel quel). Médiane et
    # non moyenne : voir docstring de module en tête de fichier.


def _fonction_normalisation(valeurs):
    """f(x) -> [0,1], 0 = meilleure valeur du lot. Constante à 0 si toutes les valeurs
    du lot sont égales (évite une division par zéro sans écraser artificiellement le
    classement — toutes les combinaisons restent alors à égalité sur cet indicateur)."""
    mini, maxi = min(valeurs), max(valeurs)
    etendue = maxi - mini
    if etendue == 0:
        return lambda x: 0.0
    return lambda x: (x - mini) / etendue


def calculer_scores(lignes_resultats, poids=None, asymetrie_dtp=None):
    """`lignes_resultats` : itérable d'objets/dicts avec au moins les clés horizon,
    seuil_c1, methode, dqp, dtp, ve, kge (typiquement
    results_store.list_resultats_avec_combinaison() filtré sur statut_crue == "success").

    `poids` : dict {indicateur: poids}, poids égaux par défaut (comportement d'origine,
    jamais modifié en place). `asymetrie_dtp` : dict {"retard": x, "avance": y} — un
    dTP positif (retard) est multiplié par `retard`, un dTP négatif (avance) par
    `avance` avant la prise de valeur absolue ; 1.0/1.0 (défaut) = traitement
    symétrique d'origine. Voir PROFILS_PONDERATION pour des jeux de valeurs proposés à
    l'utilisateur (Dashboard, sélecteur de profil).

    Un indicateur manquant (None — GRP note "NA") sur une ligne est simplement exclu de
    la médiane POUR CETTE LIGNE, il n'exclut pas la ligne entière : un jeu de résultats
    partiel reste exploitable plutôt que rejeté en bloc.

    Retourne la liste des ScoreCombinaison, triée du meilleur (score le plus bas) au
    moins bon. Liste vide si `lignes_resultats` est vide.
    """
    poids = poids or POIDS_PAR_DEFAUT
    asymetrie_dtp = asymetrie_dtp or ASYMETRIE_DTP_PAR_DEFAUT

    groupes = {}
    for ligne in lignes_resultats:
        cle = (ligne["horizon"], ligne["seuil_c1"], ligne["methode"])
        groupes.setdefault(cle, []).append(ligne)

    def _erreur(ligne, indicateur):
        valeur = ligne[indicateur]
        if valeur is None:
            return None
        if indicateur == "kge":
            return 1 - valeur
        if indicateur == "dtp":
            facteur = asymetrie_dtp["retard"] if valeur > 0 else asymetrie_dtp["avance"]
            return abs(valeur) * facteur
        return abs(valeur)

    medianes_par_combinaison = {}
    for cle, groupe in groupes.items():
        medianes = {}
        for indicateur in INDICATEURS:
            erreurs = [e for e in (_erreur(l, indicateur) for l in groupe) if e is not None]
            medianes[indicateur] = median(erreurs) if erreurs else None
        medianes_par_combinaison[cle] = (medianes, len(groupe))

    normalisateurs = {}
    for indicateur in INDICATEURS:
        valeurs = [m[indicateur] for m, _ in medianes_par_combinaison.values()
                   if m[indicateur] is not None]
        normalisateurs[indicateur] = _fonction_normalisation(valeurs) if valeurs else None

    resultats = []
    for (horizon, seuil_c1, methode), (medianes, nb_crues) in medianes_par_combinaison.items():
        composantes, poids_total = [], 0.0
        for indicateur in INDICATEURS:
            valeur = medianes[indicateur]
            normaliseur = normalisateurs[indicateur]
            if valeur is None or normaliseur is None:
                continue
            composantes.append(poids.get(indicateur, 1.0) * normaliseur(valeur))
            poids_total += poids.get(indicateur, 1.0)
        score = (sum(composantes) / poids_total) if poids_total else None
        resultats.append(ScoreCombinaison(horizon, seuil_c1, methode, score, nb_crues, medianes))

    resultats.sort(key=lambda r: (r.score is None, r.score))
    return resultats


def meilleur_candidat(lignes_resultats, poids=None, asymetrie_dtp=None) -> Optional[ScoreCombinaison]:
    """Raccourci : la meilleure combinaison, ou None si aucun résultat exploitable."""
    scores = calculer_scores(lignes_resultats, poids, asymetrie_dtp)
    return scores[0] if scores and scores[0].score is not None else None
