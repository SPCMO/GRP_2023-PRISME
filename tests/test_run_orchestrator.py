# -*- coding: utf-8 -*-
"""Tests unitaires — modules/run_orchestrator.py.

1. Fonctions de calcul de la matrice de campagne et de la logique de reprise sur échec
   (feuille de route de l'audit du 25/08/2026, point code n°5).

2. lancer_campagne() elle-même (audit de code du 1er septembre 2026, finding C2) — la
   fonction la plus exécutée et la plus longue du dépôt (~300 lignes), dont les propres
   docstrings documentent plusieurs bugs réels déjà rencontrés à cet endroit précis,
   jusqu'ici sans AUCUN test. Le lancement réel des exécutables GRP n'est bien sûr pas
   testable en isolation (nécessite un vrai poste avec GRP installé) — ces tests
   mockent donc entièrement les frontières externes (modules.liste_bassins,
   modules.grp_runner, modules.config_prevision, modules.fiche_controle_pdf,
   modules.grp_series) pour dérouler la vraie logique d'orchestration (boucle,
   reprise, gestion d'erreur par combinaison/crue, annulation) contre une base sqlite
   temporaire réelle."""

import os
import sqlite3
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from modules import results_store, run_orchestrator
from modules.grp_paths import GrpPaths
from modules.grp_runner import GrpRunError
from modules.liste_bassins import ListeBassinsFormatError
from modules.results_store import SCHEMA, _INDEX_SERIES_ARCHIVEES
from modules.run_orchestrator import (
    _calage_deja_charge, _combinaisons_a_traiter, _crues_a_traiter, _recalculer_dqp_dtp,
    generer_combinaisons, lancer_campagne,
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
    connexion.executescript(_INDEX_SERIES_ARCHIVEES)
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


# -- _recalculer_dqp_dtp (repli dQP/dTP quand le PDF ne les a pas reportés) --------------
# Ajouté suite à une question utilisateur sur des dQP manquants (28/08/2026) : validé par
# comparaison directe avec des dizaines de résultats déjà extraits du PDF sur la base
# réelle de l'utilisateur (concordance à la précision d'arrondi près) avant intégration.

def _serie(pas_de_temps_min, valeurs, date_debut=datetime(2024, 1, 1)):
    """valeurs : liste de débits/pobs, un point espacé de pas_de_temps_min minutes."""
    from datetime import timedelta
    return [(date_debut + timedelta(minutes=i * pas_de_temps_min), 0.0, v)
            for i, v in enumerate(valeurs)]


def test_recalculer_dqp_dtp_cas_normal():
    # Pic observé à l'indice 2 (valeur 100), pic simulé à l'indice 4 (valeur 120) --
    # décalage de 2 pas de temps, dQP = (120-100)/100*100 = 20%.
    obs = _serie(15, [10, 50, 100, 60, 20])
    sim = [(d, v, 0.0) for d, _p, v in _serie(15, [10, 50, 80, 100, 120])]
    dqp, dtp = _recalculer_dqp_dtp(obs, sim)
    assert dqp == pytest.approx(20.0)
    assert dtp == 2


def test_recalculer_dqp_dtp_pic_simule_avant_le_pic_observe():
    obs = _serie(15, [10, 50, 100, 60, 20])
    sim = [(d, v, 0.0) for d, _p, v in _serie(15, [10, 90, 70, 40, 20])]
    dqp, dtp = _recalculer_dqp_dtp(obs, sim)
    assert dqp == pytest.approx(-10.0)
    assert dtp == -1


def test_recalculer_dqp_dtp_serie_observee_trop_courte():
    assert _recalculer_dqp_dtp([(datetime(2024, 1, 1), 0.0, 10.0)], [(datetime(2024, 1, 1), 10.0, 0.0)]) \
        == (None, None)


def test_recalculer_dqp_dtp_serie_simulee_vide():
    obs = _serie(15, [10, 50, 100, 60, 20])
    assert _recalculer_dqp_dtp(obs, []) == (None, None)


def test_recalculer_dqp_dtp_qp_observe_nul():
    obs = _serie(15, [0, 0, 0])
    sim = [(d, v, 0.0) for d, _p, v in _serie(15, [0, 5, 10])]
    assert _recalculer_dqp_dtp(obs, sim) == (None, None)


# -- lancer_campagne (intégration, toutes les frontières externes mockées) ----------------

class _ResultatFicheFactice:
    """Substitut de modules.fiche_controle_pdf.ResultatFicheControle — seuls les
    attributs réellement lus par lancer_campagne (dqp/dtp/ve/kge/suspects/est_suspect)."""
    def __init__(self, dqp=5.0, dtp=1, ve=3.0, kge=0.9, suspects=None):
        self.dqp, self.dtp, self.ve, self.kge = dqp, dtp, ve, kge
        self.suspects = suspects or []

    @property
    def est_suspect(self):
        return bool(self.suspects)


def _paths_test(tmp_path):
    """GrpPaths pointant vers des sous-dossiers temporaires — jamais de vrai GRP
    installé, tous les exécutables/parsers étant mockés par _mocker_frontieres_externes."""
    return GrpPaths(
        dossier_grp=str(tmp_path / "GRP"), dossier_donnees=str(tmp_path / "Donnees"),
        dossier_bddtr=str(tmp_path / "BDDTR"), dossier_resultats=str(tmp_path / "Resultats"),
        code_site="Y1612020",
    )


def _mocker_frontieres_externes(monkeypatch, *, calage_echoue=False, rejeu_echoue_pour=None):
    """Mocke TOUTES les frontières externes de run_orchestrator (exécutables GRP,
    parsing LISTE_BASSINS.DAT/PDF, séries observée/simulée) pour dérouler la vraie
    logique d'orchestration de lancer_campagne() sans dépendre d'un poste GRP réel.

    `rejeu_echoue_pour` : ensemble de crue_date.isoformat() dont le rejeu (instant de
    référence) doit échouer — capturé via le dernier `instpr` passé à set_prevision
    (qui vaut `crue_date` pour l'instant référence, voir lancer_campagne::_rejeu_instant),
    seul moyen de savoir quelle crue est en cours de traitement depuis ces mocks sans
    paramètre crue_date direct."""
    rejeu_echoue_pour = rejeu_echoue_pour or set()
    dernier_instpr = [None]

    monkeypatch.setattr(run_orchestrator, "parse_liste_bassins", lambda chemin: (
        [], {"Y1612020": SimpleNamespace(
            hor1="01J00H00M", seuil_c1="5.00", methode_active="T")}))
    monkeypatch.setattr(run_orchestrator, "set_calage_params", lambda *a, **k: None)
    monkeypatch.setattr(run_orchestrator, "write_liste_bassins", lambda *a, **k: None)

    def _run_calage(exe):
        if calage_echoue:
            raise GrpRunError("échec calage simulé")
    monkeypatch.setattr(run_orchestrator, "run_calage", _run_calage)

    def _set_prevision(chemin_ini, instpr):
        dernier_instpr[0] = instpr
    monkeypatch.setattr(run_orchestrator, "set_prevision", _set_prevision)

    def _run_prevision_bat(bat, fiches_dir):
        instpr = dernier_instpr[0]
        cle = instpr.isoformat() if hasattr(instpr, "isoformat") else instpr
        if cle in rejeu_echoue_pour:
            raise GrpRunError("échec rejeu simulé")
        return "pdf_factice.pdf"
    monkeypatch.setattr(run_orchestrator, "run_prevision_bat", _run_prevision_bat)

    monkeypatch.setattr(run_orchestrator, "extraire_resultat",
                         lambda chemin_pdf: _ResultatFicheFactice())
    monkeypatch.setattr(run_orchestrator.grp_series, "parser_observations", lambda d: [])
    monkeypatch.setattr(run_orchestrator.grp_series, "parser_previsions", lambda d: [])
    monkeypatch.setattr(run_orchestrator, "nettoyer_bddtr", lambda *a, **k: None)
    return dernier_instpr


def test_lancer_campagne_cas_nominal_reussi(tmp_path, monkeypatch):
    paths = _paths_test(tmp_path)
    _mocker_frontieres_externes(monkeypatch)
    db_path = str(tmp_path / "base.sqlite3")
    crue = datetime(2024, 1, 1)
    evenements = []

    lancer_campagne(paths, "00J00H15M", [("01J00H00M", 5.0, "T")], [crue],
                     db_path=db_path, callback=evenements.append)

    with results_store.db_session(db_path) as conn:
        combinaisons = results_store.list_combinaisons(conn)
        resultats = results_store.list_resultats(conn)
    assert len(combinaisons) == 1 and combinaisons[0]["statut"] == "success"
    assert len(resultats) == 1 and resultats[0]["statut"] == "success"
    assert resultats[0]["dqp"] == 5.0
    etapes = {e.etape for e in evenements}
    assert {"calage", "rejeu"} <= etapes
    assert all(e.statut != "failed" for e in evenements)


def test_lancer_campagne_echec_calage_continue_avec_les_autres_combinaisons(tmp_path, monkeypatch):
    """Comportement explicitement documenté dans lancer_campagne() : 'N'interrompt
    jamais la boucle sur une erreur individuelle' — vérifié ici pour un échec de
    calage, la 2e combinaison ne doit jamais être court-circuitée par l'échec de la 1ère."""
    paths = _paths_test(tmp_path)
    _mocker_frontieres_externes(monkeypatch)
    appels_calage = []

    def _run_calage_echoue_au_premier_appel(exe):
        appels_calage.append(exe)
        if len(appels_calage) == 1:
            raise GrpRunError("échec calage simulé (1ère combinaison)")
    monkeypatch.setattr(run_orchestrator, "run_calage", _run_calage_echoue_au_premier_appel)

    db_path = str(tmp_path / "base.sqlite3")
    lancer_campagne(paths, "00J00H15M",
                     [("01J00H00M", 5.0, "T"), ("02J00H00M", 5.0, "T")],
                     [datetime(2024, 1, 1)], db_path=db_path)

    with results_store.db_session(db_path) as conn:
        statuts = {(c["horizon"], c["seuil_c1"], c["methode"]): c["statut"]
                   for c in results_store.list_combinaisons(conn)}
    assert statuts[("01J00H00M", 5.0, "T")] == "failed"
    assert statuts[("02J00H00M", 5.0, "T")] == "success"
    assert len(appels_calage) == 2  # la campagne a bien continué sur la 2e combinaison


def test_lancer_campagne_echec_rejeu_ne_touche_que_la_crue_en_echec(tmp_path, monkeypatch):
    paths = _paths_test(tmp_path)
    crue_ok, crue_ko = datetime(2024, 1, 1), datetime(2024, 2, 1)
    _mocker_frontieres_externes(monkeypatch, rejeu_echoue_pour={crue_ko.isoformat()})

    db_path = str(tmp_path / "base.sqlite3")
    lancer_campagne(paths, "00J00H15M", [("01J00H00M", 5.0, "T")], [crue_ok, crue_ko],
                     db_path=db_path)

    with results_store.db_session(db_path) as conn:
        combinaisons = results_store.list_combinaisons(conn)
        resultats = {r["crue_date"]: r["statut"] for r in results_store.list_resultats(conn)}
    assert combinaisons[0]["statut"] == "success"  # le calage, lui, a bien réussi
    assert resultats[crue_ok.isoformat()] == "success"
    assert resultats[crue_ko.isoformat()] == "failed"


def test_lancer_campagne_reprise_ne_relance_pas_un_calage_deja_reussi(tmp_path, monkeypatch):
    paths = _paths_test(tmp_path)
    _mocker_frontieres_externes(monkeypatch)
    appels_calage = []
    monkeypatch.setattr(run_orchestrator, "run_calage", lambda exe: appels_calage.append(exe))

    # calage_deja_ok exige aussi la présence physique de config_prevision.ini (voir
    # lancer_campagne, garde-fou contre le bug réel du nettoyage BDTR de fin de
    # campagne qui vide ce fichier) — créé ici pour simuler un dossier BDTR intact.
    os.makedirs(os.path.dirname(paths.config_prevision_ini), exist_ok=True)
    open(paths.config_prevision_ini, "w", encoding="utf-8").close()

    db_path = str(tmp_path / "base.sqlite3")
    crue1, crue2 = datetime(2024, 1, 1), datetime(2024, 2, 1)
    results_store.init_db(db_path)
    with results_store.db_session(db_path) as conn:
        cid = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, cid, crue1, "success")

    lancer_campagne(paths, "00J00H15M", [("01J00H00M", 5.0, "T")], [crue1, crue2],
                     db_path=db_path, seulement_echecs=True)

    assert appels_calage == []  # calage jamais relancé, déjà acquis
    with results_store.db_session(db_path) as conn:
        resultats = {r["crue_date"]: r["statut"] for r in results_store.list_resultats(conn)}
    assert resultats[crue1.isoformat()] == "success"  # inchangée, non retouchée
    assert resultats[crue2.isoformat()] == "success"  # la manquante a bien été rejouée


def test_lancer_campagne_annulation_avant_traitement_stoppe_proprement(tmp_path, monkeypatch):
    paths = _paths_test(tmp_path)
    appels_calage = []
    _mocker_frontieres_externes(monkeypatch)
    monkeypatch.setattr(run_orchestrator, "run_calage", lambda exe: appels_calage.append(exe))
    nettoyages = []
    monkeypatch.setattr(run_orchestrator, "nettoyer_bddtr",
                         lambda dossier: nettoyages.append(dossier))

    annulation = threading.Event()
    annulation.set()  # déjà annulée avant même le premier appel

    db_path = str(tmp_path / "base.sqlite3")
    evenements = []
    lancer_campagne(paths, "00J00H15M", [("01J00H00M", 5.0, "T")], [datetime(2024, 1, 1)],
                     db_path=db_path, annulation=annulation, callback=evenements.append)

    assert appels_calage == []  # rien traité du tout
    assert any(e.statut == "annule" for e in evenements)
    assert nettoyages == [paths.dossier_bddtr]  # nettoyage final bien appelé malgré tout
