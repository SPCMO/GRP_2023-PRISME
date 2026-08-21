# -*- coding: utf-8 -*-
"""Score composite de performance par combinaison (horizon, seuil_c1, méthode) — bloc 6
"Dashboard et score de synthèse".

Combine |dQP|, |dTP|, |VE|, (1-KGE) — chaque indicateur normalisé min-max sur l'ensemble
des combinaisons évaluées, puis moyenne pondérée (poids égaux par défaut, réglables). Un
score PLUS BAS = MEILLEURE performance (0 = la meilleure valeur observée sur chaque
indicateur, 1 = la pire). Fonction pure, indépendante de l'UI et de results_store, pour
rester testable isolément.
"""

from dataclasses import dataclass, field
from statistics import mean
from typing import Dict, List, Optional

INDICATEURS = ("dqp", "dtp", "ve", "kge")
POIDS_PAR_DEFAUT = {"dqp": 1.0, "dtp": 1.0, "ve": 1.0, "kge": 1.0}

# Explication affichée à l'utilisateur (bouton "ⓘ" à côté de chaque "Score composite"
# dans le Dashboard, voir ui/tab_dashboard.py et ui/tab_orchestration.py) — centralisée
# ici pour rester la description exacte du calcul réellement fait ci-dessous.
EXPLICATION_SCORE = (
    "Score composite (0 = meilleur, 1 = pire)\n\n"
    "Calculé UNIQUEMENT sur les combinaisons actuellement affichées à l'écran (jamais "
    "sur une seule, ni sur toute la base) :\n\n"
    "1. Pour chaque combinaison, on calcule l'erreur moyenne (sur ses crues réussies) "
    "de 4 indicateurs : |dQP| (écart % sur le débit de pointe), |dTP| (écart en pas de "
    "temps sur l'heure du pic), |VE| (écart % sur le volume écoulé), et (1 − KGE) "
    "(KGE théoriquement ≤ 1 dans le meilleur cas).\n\n"
    "2. Chaque indicateur est normalisé entre 0 (la MEILLEURE valeur observée parmi "
    "TOUTES les combinaisons affichées) et 1 (la pire) — c'est une échelle RELATIVE à "
    "ce qui est affiché : ajouter ou retirer des combinaisons du graphique/tableau peut "
    "donc changer le score de toutes les autres.\n\n"
    "3. Le score final est la MOYENNE des 4 indicateurs normalisés, à POIDS ÉGAUX "
    "(25% chacun).\n\n"
    "4. dTP est traité de façon SYMÉTRIQUE : une avance de 2 pas de temps compte "
    "exactement comme un retard de 2 pas de temps — le score ne privilégie pas l'avance "
    "sur le retard, ni l'inverse.\n\n"
    "Voir modules/score.py (fonction calculer_scores) pour le détail du calcul."
)


@dataclass
class ScoreCombinaison:
    horizon: str
    seuil_c1: float
    methode: str
    score: Optional[float]     # None si aucun indicateur exploitable
    nb_crues: int
    moyennes_erreur: Dict[str, Optional[float]] = field(default_factory=dict)
    # moyennes_erreur : |dQP|, |dTP|, |VE|, (1-KGE) moyens (non normalisés) — pour
    # affichage humain à côté du score normalisé (peu lisible tel quel).


def _fonction_normalisation(valeurs):
    """f(x) -> [0,1], 0 = meilleure valeur du lot. Constante à 0 si toutes les valeurs
    du lot sont égales (évite une division par zéro sans écraser artificiellement le
    classement — toutes les combinaisons restent alors à égalité sur cet indicateur)."""
    mini, maxi = min(valeurs), max(valeurs)
    etendue = maxi - mini
    if etendue == 0:
        return lambda x: 0.0
    return lambda x: (x - mini) / etendue


def calculer_scores(lignes_resultats, poids=None):
    """`lignes_resultats` : itérable d'objets/dicts avec au moins les clés horizon,
    seuil_c1, methode, dqp, dtp, ve, kge (typiquement
    results_store.list_resultats_avec_combinaison() filtré sur statut_crue == "success").

    Un indicateur manquant (None — GRP note "NA") sur une ligne est simplement exclu de
    la moyenne POUR CETTE LIGNE, il n'exclut pas la ligne entière : un jeu de résultats
    partiel reste exploitable plutôt que rejeté en bloc.

    Retourne la liste des ScoreCombinaison, triée du meilleur (score le plus bas) au
    moins bon. Liste vide si `lignes_resultats` est vide.
    """
    poids = poids or POIDS_PAR_DEFAUT

    groupes = {}
    for ligne in lignes_resultats:
        cle = (ligne["horizon"], ligne["seuil_c1"], ligne["methode"])
        groupes.setdefault(cle, []).append(ligne)

    def _erreur(ligne, indicateur):
        valeur = ligne[indicateur]
        if valeur is None:
            return None
        return (1 - valeur) if indicateur == "kge" else abs(valeur)

    moyennes_par_combinaison = {}
    for cle, groupe in groupes.items():
        moyennes = {}
        for indicateur in INDICATEURS:
            erreurs = [e for e in (_erreur(l, indicateur) for l in groupe) if e is not None]
            moyennes[indicateur] = mean(erreurs) if erreurs else None
        moyennes_par_combinaison[cle] = (moyennes, len(groupe))

    normalisateurs = {}
    for indicateur in INDICATEURS:
        valeurs = [m[indicateur] for m, _ in moyennes_par_combinaison.values()
                   if m[indicateur] is not None]
        normalisateurs[indicateur] = _fonction_normalisation(valeurs) if valeurs else None

    resultats = []
    for (horizon, seuil_c1, methode), (moyennes, nb_crues) in moyennes_par_combinaison.items():
        composantes, poids_total = [], 0.0
        for indicateur in INDICATEURS:
            valeur = moyennes[indicateur]
            normaliseur = normalisateurs[indicateur]
            if valeur is None or normaliseur is None:
                continue
            composantes.append(poids.get(indicateur, 1.0) * normaliseur(valeur))
            poids_total += poids.get(indicateur, 1.0)
        score = (sum(composantes) / poids_total) if poids_total else None
        resultats.append(ScoreCombinaison(horizon, seuil_c1, methode, score, nb_crues, moyennes))

    resultats.sort(key=lambda r: (r.score is None, r.score))
    return resultats


def meilleur_candidat(lignes_resultats, poids=None) -> Optional[ScoreCombinaison]:
    """Raccourci : la meilleure combinaison, ou None si aucun résultat exploitable."""
    scores = calculer_scores(lignes_resultats, poids)
    return scores[0] if scores and scores[0].score is not None else None
