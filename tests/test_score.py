# -*- coding: utf-8 -*-
"""Tests unitaires — modules/score.py (fonction pure, indépendante de l'UI et de
results_store — voir son propre docstring). Voir la feuille de route de l'audit du
25/08/2026, point code n°5."""

import pytest

from modules.score import (
    PROFILS_PONDERATION,
    calculer_scores,
    filtrer_par_crues,
    meilleur_candidat,
    resoudre_ponderation,
)


def _ligne(horizon, seuil_c1, methode, crue_date, dqp, dtp, ve, kge):
    return {
        "horizon": horizon, "seuil_c1": seuil_c1, "methode": methode,
        "crue_date": crue_date, "dqp": dqp, "dtp": dtp, "ve": ve, "kge": kge,
    }


# -- filtrer_par_crues -------------------------------------------------------------------

def test_filtrer_par_crues_restreint_a_la_selection():
    lignes = [
        _ligne("01J00H00M", 5.0, "T", "2024-01-01", 5, 1, 3, 0.9),
        _ligne("01J00H00M", 5.0, "T", "2024-02-01", 8, 2, 4, 0.8),
    ]
    resultat = filtrer_par_crues(lignes, ["2024-01-01"])
    assert len(resultat) == 1
    assert resultat[0]["crue_date"] == "2024-01-01"


def test_filtrer_par_crues_vide_ou_none_desactive_le_filtre():
    lignes = [_ligne("01J00H00M", 5.0, "T", "2024-01-01", 5, 1, 3, 0.9)]
    assert filtrer_par_crues(lignes, []) == lignes
    assert filtrer_par_crues(lignes, None) == lignes


# -- resoudre_ponderation -----------------------------------------------------------------

def test_resoudre_ponderation_profil_egal_par_defaut():
    poids, asymetrie, libelle = resoudre_ponderation(None)
    assert poids == PROFILS_PONDERATION["egal"]["poids"]
    assert libelle == PROFILS_PONDERATION["egal"]["libelle"]


def test_resoudre_ponderation_profil_metier():
    poids, asymetrie, libelle = resoudre_ponderation({"profil": "metier"})
    assert poids == PROFILS_PONDERATION["metier"]["poids"]
    assert asymetrie["retard"] > asymetrie["avance"]


def test_resoudre_ponderation_personnalise():
    poids_perso = {"dqp": 5.0, "dtp": 1.0, "ve": 1.0, "kge": 1.0}
    poids, _asymetrie, libelle = resoudre_ponderation(
        {"profil": "personnalise", "poids_personnalise": poids_perso})
    assert poids == poids_perso
    assert libelle == "Personnalisé"


# -- calculer_scores ----------------------------------------------------------------------

def test_calculer_scores_la_meilleure_combinaison_a_le_score_le_plus_bas():
    lignes = [
        # Combinaison A : erreurs faibles -> devrait gagner
        _ligne("01J00H00M", 5.0, "T", "2024-01-01", dqp=2, dtp=1, ve=2, kge=0.95),
        _ligne("01J00H00M", 5.0, "T", "2024-02-01", dqp=3, dtp=1, ve=1, kge=0.90),
        # Combinaison B : erreurs plus fortes -> devrait perdre
        _ligne("02J00H00M", 5.0, "T", "2024-01-01", dqp=20, dtp=5, ve=15, kge=0.50),
        _ligne("02J00H00M", 5.0, "T", "2024-02-01", dqp=25, dtp=6, ve=18, kge=0.40),
    ]
    scores = calculer_scores(lignes)
    assert len(scores) == 2
    # Trié du meilleur (score le plus bas) au moins bon.
    assert scores[0].horizon == "01J00H00M"
    assert scores[0].score < scores[1].score
    assert scores[0].nb_crues == 2


def test_calculer_scores_liste_vide():
    assert calculer_scores([]) == []


def test_calculer_scores_indicateur_manquant_nexclut_pas_la_ligne():
    lignes = [
        _ligne("01J00H00M", 5.0, "T", "2024-01-01", dqp=2, dtp=None, ve=2, kge=0.95),
    ]
    scores = calculer_scores(lignes)
    assert len(scores) == 1
    assert scores[0].erreurs_agregees["dtp"] is None
    assert scores[0].score is not None  # calculé sur les 3 autres indicateurs


def test_calculer_scores_agregation_mediane_par_defaut():
    """Comportement inchangé si `agregation` n'est pas précisé — voir
    AGREGATION_PAR_DEFAUT ("mediane")."""
    # 3 crues, dQP = 1, 2, 100 -> médiane = 2 (robuste à l'outlier), moyenne = 34.33
    # (très sensible à l'outlier) : les deux modes doivent donc donner des valeurs
    # nettement différentes pour erreurs_agregees["dqp"].
    lignes = [
        _ligne("01J00H00M", 5.0, "T", "2024-01-01", dqp=1, dtp=0, ve=0, kge=1.0),
        _ligne("01J00H00M", 5.0, "T", "2024-02-01", dqp=2, dtp=0, ve=0, kge=1.0),
        _ligne("01J00H00M", 5.0, "T", "2024-03-01", dqp=-100, dtp=0, ve=0, kge=1.0),
    ]
    scores = calculer_scores(lignes)
    assert scores[0].erreurs_agregees["dqp"] == 2  # médiane de [1, 2, 100]


def test_calculer_scores_agregation_moyenne_explicite():
    lignes = [
        _ligne("01J00H00M", 5.0, "T", "2024-01-01", dqp=1, dtp=0, ve=0, kge=1.0),
        _ligne("01J00H00M", 5.0, "T", "2024-02-01", dqp=2, dtp=0, ve=0, kge=1.0),
        _ligne("01J00H00M", 5.0, "T", "2024-03-01", dqp=-100, dtp=0, ve=0, kge=1.0),
    ]
    scores = calculer_scores(lignes, agregation="moyenne")
    assert scores[0].erreurs_agregees["dqp"] == pytest.approx((1 + 2 + 100) / 3)


def test_calculer_scores_mediane_et_moyenne_donnent_des_scores_differents():
    """Sur un jeu avec un vrai outlier, les 2 modes doivent produire un score composite
    différent pour la même combinaison — sinon le sélecteur d'agrégation du Dashboard
    n'aurait aucun effet visible."""
    lignes_a = [
        _ligne("01J00H00M", 5.0, "T", "2024-01-01", dqp=1, dtp=1, ve=1, kge=0.99),
        _ligne("01J00H00M", 5.0, "T", "2024-02-01", dqp=2, dtp=1, ve=1, kge=0.99),
        _ligne("01J00H00M", 5.0, "T", "2024-03-01", dqp=200, dtp=1, ve=1, kge=0.99),
    ]
    lignes_b = [
        _ligne("02J00H00M", 5.0, "T", "2024-01-01", dqp=10, dtp=1, ve=1, kge=0.99),
        _ligne("02J00H00M", 5.0, "T", "2024-02-01", dqp=11, dtp=1, ve=1, kge=0.99),
        _ligne("02J00H00M", 5.0, "T", "2024-03-01", dqp=12, dtp=1, ve=1, kge=0.99),
    ]
    lignes = lignes_a + lignes_b
    scores_mediane = {s.horizon: s.score for s in calculer_scores(lignes, agregation="mediane")}
    scores_moyenne = {s.horizon: s.score for s in calculer_scores(lignes, agregation="moyenne")}
    # En médiane, combinaison A (médiane dQP=2) bat largement B (médiane dQP=11).
    assert scores_mediane["01J00H00M"] < scores_mediane["02J00H00M"]
    # En moyenne, l'outlier (200) fait perdre A face à B (moyenne dQP=71 vs 11).
    assert scores_moyenne["01J00H00M"] > scores_moyenne["02J00H00M"]


def test_calculer_scores_agregation_invalide_leve_key_error():
    with pytest.raises(KeyError):
        calculer_scores([_ligne("01J00H00M", 5.0, "T", "2024-01-01", 1, 1, 1, 0.9)],
                         agregation="inconnue")


def test_calculer_scores_asymetrie_dtp_penalise_le_retard():
    # Même |dTP| en valeur absolue (3), une fois en retard (+3) une fois en avance (-3) :
    # avec l'asymétrie du profil "métier" (retard x1.25, avance x0.75), la ligne en
    # retard doit produire un score dTP plus élevé (pire) que celle en avance.
    poids_dtp_seul = {"dqp": 0, "dtp": 1.0, "ve": 0, "kge": 0}
    asymetrie = PROFILS_PONDERATION["metier"]["asymetrie_dtp"]
    lignes_retard = [_ligne("01J00H00M", 5.0, "T", "2024-01-01", dqp=0, dtp=3, ve=0, kge=1.0),
                      _ligne("02J00H00M", 5.0, "T", "2024-01-01", dqp=0, dtp=-3, ve=0, kge=1.0)]
    scores = calculer_scores(lignes_retard, poids=poids_dtp_seul, asymetrie_dtp=asymetrie)
    score_retard = next(s for s in scores if s.horizon == "01J00H00M")
    score_avance = next(s for s in scores if s.horizon == "02J00H00M")
    assert score_retard.score > score_avance.score


# -- meilleur_candidat --------------------------------------------------------------------

def test_meilleur_candidat_retourne_le_score_le_plus_bas():
    lignes = [
        _ligne("01J00H00M", 5.0, "T", "2024-01-01", dqp=2, dtp=1, ve=2, kge=0.95),
        _ligne("02J00H00M", 5.0, "T", "2024-01-01", dqp=20, dtp=5, ve=15, kge=0.50),
    ]
    meilleur = meilleur_candidat(lignes)
    assert meilleur.horizon == "01J00H00M"


def test_meilleur_candidat_liste_vide_retourne_none():
    assert meilleur_candidat([]) is None
