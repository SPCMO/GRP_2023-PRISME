# -*- coding: utf-8 -*-
"""Tests unitaires — modules/run_orchestrator.py, fonctions de calcul de la matrice de
campagne et de la logique de reprise sur échec (pas le lancement réel du calage GRP,
qui nécessite les exécutables externes et n'est pas testable en isolation). Voir la
feuille de route de l'audit du 25/08/2026, point code n°5."""

import sqlite3
from datetime import datetime

import pytest

from modules.results_store import SCHEMA
from modules.run_orchestrator import _combinaisons_a_traiter, _crues_a_traiter, generer_combinaisons


# -- generer_combinaisons (pure) ---------------------------------------------------------

def test_generer_combinaisons_produit_cartesien_trie():
    combinaisons = generer_combinaisons(
        horizons=["02J00H00M", "01J00H00M"], seuils=[5.0, 3.0], methodes=["R", "T"])
    # Produit cartésien complet : 2 horizons x 2 seuils x 2 méthodes = 8 combinaisons.
    assert len(combinaisons) == 8
    # Ordre déterministe (trié), pas l'ordre d'entrée — utile pour les logs/tests.
    assert combinaisons == sorted(combinaisons)
    assert combinaisons[0] == ("01J00H00M", 3.0, "R")


def test_generer_combinaisons_listes_vides():
    assert generer_combinaisons([], [5.0], ["T"]) == []
    assert generer_combinaisons(["01J00H00M"], [], ["T"]) == []


# -- _combinaisons_a_traiter / _crues_a_traiter (nécessitent une connexion sqlite) --------

@pytest.fixture
def conn():
    connexion = sqlite3.connect(":memory:")
    connexion.row_factory = sqlite3.Row
    connexion.executescript(SCHEMA)
    yield connexion
    connexion.close()


def _inserer_combinaison(conn, horizon, seuil_c1, methode, statut):
    conn.execute(
        "INSERT INTO combinaisons (horizon, seuil_c1, methode, statut, date_maj) "
        "VALUES (?, ?, ?, ?, ?)",
        (horizon, seuil_c1, methode, statut, datetime.now().isoformat()),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM combinaisons WHERE horizon = ? AND seuil_c1 = ? AND methode = ?",
        (horizon, seuil_c1, methode),
    ).fetchone()["id"]


def _inserer_resultat_crue(conn, combinaison_id, crue_date, statut):
    conn.execute(
        "INSERT INTO resultats_crues (combinaison_id, crue_date, statut, date_maj) "
        "VALUES (?, ?, ?, ?)",
        (combinaison_id, crue_date.isoformat(), statut, datetime.now().isoformat()),
    )
    conn.commit()


def test_combinaisons_a_traiter_sans_reprise_garde_tout(conn):
    combinaisons = [("01J00H00M", 5.0, "T"), ("02J00H00M", 5.0, "T")]
    resultat = _combinaisons_a_traiter(conn, combinaisons, [], seulement_echecs=False)
    assert resultat == combinaisons


def test_combinaisons_a_traiter_reprise_exclut_combinaison_deja_reussie(conn):
    crue = datetime(2024, 1, 1)
    id_ok = _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")
    _inserer_resultat_crue(conn, id_ok, crue, "success")
    _inserer_combinaison(conn, "02J00H00M", 5.0, "T", "failed")

    combinaisons = [("01J00H00M", 5.0, "T"), ("02J00H00M", 5.0, "T")]
    resultat = _combinaisons_a_traiter(conn, combinaisons, [crue], seulement_echecs=True)

    # La combinaison réussie (calage OK + crue réussie) est exclue de la reprise ;
    # celle en échec doit être relancée.
    assert resultat == [("02J00H00M", 5.0, "T")]


def test_combinaisons_a_traiter_reprise_garde_calage_ok_mais_crue_manquante(conn):
    # Calage réussi, mais aucune crue rejouée sous cette combinaison — doit être
    # reprise (pour rejouer la crue manquante), sans refaire le calage.
    crue = datetime(2024, 1, 1)
    _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")

    resultat = _combinaisons_a_traiter(
        conn, [("01J00H00M", 5.0, "T")], [crue], seulement_echecs=True)
    assert resultat == [("01J00H00M", 5.0, "T")]


def test_combinaisons_a_traiter_inconnue_en_base_est_a_traiter(conn):
    # Jamais tentée du tout -> doit être traitée même en mode reprise.
    resultat = _combinaisons_a_traiter(
        conn, [("01J00H00M", 5.0, "T")], [], seulement_echecs=True)
    assert resultat == [("01J00H00M", 5.0, "T")]


def test_crues_a_traiter_sans_reprise_garde_toutes_les_dates(conn):
    dates = [datetime(2024, 1, 1), datetime(2024, 2, 1)]
    assert _crues_a_traiter(conn, combinaison_id=1, crues_dates=dates,
                              seulement_echecs=False) == dates


def test_crues_a_traiter_reprise_ne_garde_que_les_non_reussies(conn):
    id_combi = _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")
    crue_ok = datetime(2024, 1, 1)
    crue_echec = datetime(2024, 2, 1)
    _inserer_resultat_crue(conn, id_combi, crue_ok, "success")
    _inserer_resultat_crue(conn, id_combi, crue_echec, "failed")

    resultat = _crues_a_traiter(
        conn, id_combi, [crue_ok, crue_echec], seulement_echecs=True)
    assert resultat == [crue_echec]
