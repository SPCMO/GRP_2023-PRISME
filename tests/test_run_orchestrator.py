# -*- coding: utf-8 -*-
"""Tests unitaires — modules/run_orchestrator.py, fonctions de calcul de la matrice de
campagne et de la logique de reprise sur échec (pas le lancement réel du calage GRP,
qui nécessite les exécutables externes et n'est pas testable en isolation). Voir la
feuille de route de l'audit du 25/08/2026, point code n°5."""

import sqlite3
from datetime import datetime

import pytest

from modules.results_store import SCHEMA
from modules.run_orchestrator import (
    _calage_deja_charge, _combinaisons_a_traiter, _crues_a_traiter, generer_combinaisons,
)


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


def _inserer_resultat_crue(conn, combinaison_id, crue_date, statut, instant_label="reference"):
    conn.execute(
        "INSERT INTO resultats_crues (combinaison_id, crue_date, instant_label, statut, date_maj) "
        "VALUES (?, ?, ?, ?, ?)",
        (combinaison_id, crue_date.isoformat(), instant_label, statut, datetime.now().isoformat()),
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


def test_combinaisons_a_traiter_reprise_garde_combinaison_pour_instant_manquant(conn):
    # Régression constatée en conditions réelles (27/08/2026) : une combinaison dont
    # le calage ET la référence de toutes les crues ont déjà réussi doit quand même
    # être reprise si un instant supplémentaire configuré (voir Paramétrage) lui
    # manque encore — sans transmettre decalages_pic_heures ICI (pas seulement à
    # l'appel de _crues_a_traiter fait plus loin dans lancer_campagne), la combinaison
    # était exclue à tort et "Compléter la campagne" se terminait instantanément sans
    # traiter aucun instant.
    crue = datetime(2024, 1, 1)
    id_combi = _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="reference")

    resultat = _combinaisons_a_traiter(
        conn, [("01J00H00M", 5.0, "T")], [crue], seulement_echecs=True,
        decalages_pic_heures=[6])
    assert resultat == [("01J00H00M", 5.0, "T")]

    # Une fois l'instant H-6 lui aussi réussi, plus rien à faire pour cette combinaison.
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="H-6")
    resultat = _combinaisons_a_traiter(
        conn, [("01J00H00M", 5.0, "T")], [crue], seulement_echecs=True,
        decalages_pic_heures=[6])
    assert resultat == []


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


def test_crues_a_traiter_ignore_les_instants_supplementaires_pour_le_statut_reference(conn):
    # Une ligne d'instant supplémentaire (H-24) ne doit jamais être prise pour la
    # référence par une requête non filtrée sur instant_label — garde-fou contre le
    # même bug de principe que list_resultats_avec_combinaison (voir results_store).
    id_combi = _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")
    crue = datetime(2024, 1, 1)
    _inserer_resultat_crue(conn, id_combi, crue, "failed", instant_label="reference")
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="H-24")

    resultat = _crues_a_traiter(conn, id_combi, [crue], seulement_echecs=True)
    assert resultat == [crue]  # la référence a échoué -> à refaire, malgré H-24 réussi


def test_crues_a_traiter_reprise_pour_instant_supplementaire_manquant(conn):
    # Référence déjà réussie, mais aucun instant supplémentaire encore tenté -> doit
    # être reprise pour les ajouter, sans que la référence soit elle-même en échec.
    id_combi = _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")
    crue = datetime(2024, 1, 1)
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="reference")

    resultat = _crues_a_traiter(conn, id_combi, [crue], seulement_echecs=True,
                                  decalages_pic_heures=[24, 12])
    assert resultat == [crue]


def test_crues_a_traiter_pas_de_reprise_si_tous_les_instants_deja_reussis(conn):
    id_combi = _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")
    crue = datetime(2024, 1, 1)
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="reference")
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="H-24")
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="H-12")

    resultat = _crues_a_traiter(conn, id_combi, [crue], seulement_echecs=True,
                                  decalages_pic_heures=[24, 12])
    assert resultat == []


def test_crues_a_traiter_sans_decalages_ignore_les_instants_supplementaires(conn):
    # decalages_pic_heures absent/vide (comportement historique) : peu importe ce qui
    # existe déjà comme instants supplémentaires, seule la référence compte.
    id_combi = _inserer_combinaison(conn, "01J00H00M", 5.0, "T", "success")
    crue = datetime(2024, 1, 1)
    _inserer_resultat_crue(conn, id_combi, crue, "success", instant_label="reference")

    resultat = _crues_a_traiter(conn, id_combi, [crue], seulement_echecs=True)
    assert resultat == []


# -- _calage_deja_charge (lit un vrai LISTE_BASSINS.DAT, pas de connexion sqlite) --------
# Garde-fou contre un bug réel constaté sur des campagnes "Compléter la campagne" :
# plusieurs combinaisons horizon/seuil différentes rejouaient EXACTEMENT le même
# hydrogramme simulé, faute de vérifier que LISTE_BASSINS.DAT chargeait bien LEUR
# combinaison plutôt que celle d'une autre combinaison traitée juste avant dans la
# même reprise (voir le commentaire de calage_deja_ok dans run_orchestrator.py).

_LIGNE_LISTE_BASSINS_EXEMPLE = (
    "!Y1612020!00J00H15M!Moussoulens...!  4838.00!TU!01/07/2006 00:00!11/03/2026 06:00!##!"
    "1!0!0!0!00J06H00M!00J00H00M!    5.00!     -99!##!  400.00!  500.00!  800.00! 4!00J00H00M! 0!   10!1!\r\n"
)


class _PathsFactice:
    """Substitut minimal de GrpPaths — _calage_deja_charge n'utilise que ces 2 attributs."""

    def __init__(self, liste_bassins_dat, code_site="Y1612020"):
        self.liste_bassins_dat = liste_bassins_dat
        self.code_site = code_site


def _ecrire_liste_bassins(tmp_path, contenu=_LIGNE_LISTE_BASSINS_EXEMPLE):
    chemin = tmp_path / "LISTE_BASSINS.DAT"
    chemin.write_text(contenu, encoding="cp1252", newline="")
    return str(chemin)


def test_calage_deja_charge_combinaison_identique(tmp_path):
    paths = _PathsFactice(_ecrire_liste_bassins(tmp_path))
    assert _calage_deja_charge(paths, "00J06H00M", 5.0, "T") is True


def test_calage_deja_charge_horizon_different(tmp_path):
    # Le fichier charge 00J06H00M : une combinaison à 12h ne doit jamais être
    # considérée comme "déjà chargée", même si elle a réussi par le passé.
    paths = _PathsFactice(_ecrire_liste_bassins(tmp_path))
    assert _calage_deja_charge(paths, "00J12H00M", 5.0, "T") is False


def test_calage_deja_charge_seuil_different(tmp_path):
    paths = _PathsFactice(_ecrire_liste_bassins(tmp_path))
    assert _calage_deja_charge(paths, "00J06H00M", 10.0, "T") is False


def test_calage_deja_charge_methode_differente(tmp_path):
    paths = _PathsFactice(_ecrire_liste_bassins(tmp_path))
    assert _calage_deja_charge(paths, "00J06H00M", 5.0, "R") is False


def test_calage_deja_charge_fichier_introuvable(tmp_path):
    paths = _PathsFactice(str(tmp_path / "absent.DAT"))
    assert _calage_deja_charge(paths, "00J06H00M", 5.0, "T") is False


def test_calage_deja_charge_code_site_absent_du_fichier(tmp_path):
    paths = _PathsFactice(_ecrire_liste_bassins(tmp_path), code_site="Z9999999")
    assert _calage_deja_charge(paths, "00J06H00M", 5.0, "T") is False
