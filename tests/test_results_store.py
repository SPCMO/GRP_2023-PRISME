# -*- coding: utf-8 -*-
"""Tests unitaires — modules/results_store.py.

1. Résolution du dossier de stockage des bases (dossier_data_effectif) : voir
   ui/tab_config.py, bandeau "Dossier de stockage des bases de résultats", et
   config.FICHIER_POINTEUR_DATA — corrige un incident réel où une réinstallation de
   l'outil dans un nouveau dossier repartait avec une base vierge, l'ancienne (restée
   dans l'ancien dossier) n'étant ni retrouvée ni signalée. `app_config.
   FICHIER_POINTEUR_DATA` est monkeypatché vers un chemin de test à chaque test
   (jamais le vrai %APPDATA% de la machine qui exécute la suite).

2. Migrations de schéma/fichier (_migrer_ancienne_base_partagee_si_necessaire,
   _migrer_schema_multi_instants) : ajoutés suite à l'audit de code du 1er septembre
   2026 (finding C1) — ce sont les fonctions où une régression aurait le plus fort
   impact (perte/corruption de résultats de campagne réels), et elles n'avaient
   jusqu'ici AUCUN test.

3. Fonctions qui pilotent directement l'UI (duree_par_etape, estimer_temps_restant,
   etat_combinaisons, resume_couverture, list_combinaisons_completes) : même finding
   C1 — logique non triviale (seuil d'inactivité, médiane par étape, filtrage
   instant_label='reference') alimentant respectivement le bouton "⏱ Estimer le temps
   restant", les badges de couverture de l'onglet Paramétrage, et la fenêtre
   "Combinaisons déjà réalisées".

Chaque test utilise une base sqlite temporaire (tmp_path) — jamais une base réelle."""
import os
import sqlite3

import pytest

import config as app_config
from modules import results_store

# Reproduction exacte de l'ANCIEN schéma (avant l'ajout de instant_label, 27/08/2026)
# pour tester _migrer_schema_multi_instants sur une base pré-migration réaliste.
_ANCIEN_SCHEMA = """
CREATE TABLE combinaisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    horizon TEXT NOT NULL, seuil_c1 REAL NOT NULL, methode TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'pending', erreur TEXT, date_maj TEXT NOT NULL,
    UNIQUE (horizon, seuil_c1, methode)
);
CREATE TABLE resultats_crues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combinaison_id INTEGER NOT NULL REFERENCES combinaisons(id) ON DELETE CASCADE,
    crue_date TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'pending',
    dqp REAL, dtp REAL, ve REAL, kge REAL, suspects TEXT, erreur TEXT, date_maj TEXT NOT NULL,
    UNIQUE (combinaison_id, crue_date)
);
CREATE TABLE series_archivees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combinaison_id INTEGER NOT NULL REFERENCES combinaisons(id) ON DELETE CASCADE,
    crue_date TEXT NOT NULL,
    type TEXT NOT NULL, point_date TEXT NOT NULL, debit REAL, pluie REAL
);
"""


def test_dossier_data_effectif_par_defaut_sans_pointeur(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "FICHIER_POINTEUR_DATA",
                         str(tmp_path / "pointeur_absent.txt"))
    assert results_store.dossier_data_effectif() == app_config.DATA_DIR


def test_dossier_data_effectif_lit_le_pointeur_si_present(tmp_path, monkeypatch):
    dossier_externe = str(tmp_path / "MesBasesExternes")
    fichier_pointeur = tmp_path / "pointeur.txt"
    fichier_pointeur.write_text(dossier_externe, encoding="utf-8")
    monkeypatch.setattr(app_config, "FICHIER_POINTEUR_DATA", str(fichier_pointeur))
    assert results_store.dossier_data_effectif() == dossier_externe


def test_dossier_data_effectif_ignore_un_pointeur_vide(tmp_path, monkeypatch):
    fichier_pointeur = tmp_path / "pointeur_vide.txt"
    fichier_pointeur.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(app_config, "FICHIER_POINTEUR_DATA", str(fichier_pointeur))
    assert results_store.dossier_data_effectif() == app_config.DATA_DIR


def test_chemin_db_par_defaut_utilise_le_dossier_pointeur(tmp_path, monkeypatch):
    dossier_externe = tmp_path / "MesBasesExternes"
    os.makedirs(dossier_externe)
    fichier_pointeur = tmp_path / "pointeur.txt"
    fichier_pointeur.write_text(str(dossier_externe), encoding="utf-8")
    monkeypatch.setattr(app_config, "FICHIER_POINTEUR_DATA", str(fichier_pointeur))
    monkeypatch.setattr(
        results_store.config_manager, "load_config",
        lambda: {"station": {"code_station": "Y999999999"}},
    )
    attendu = str(dossier_externe / "runs_Y999999999.sqlite3")
    assert results_store._chemin_db_par_defaut() == attendu


# -- _migrer_ancienne_base_partagee_si_necessaire ------------------------------------------

def test_migration_base_partagee_renomme_vers_la_base_station(tmp_path):
    chemin_partage = str(tmp_path / "runs.sqlite3")
    chemin_station = str(tmp_path / "runs_Y999999999.sqlite3")
    conn = sqlite3.connect(chemin_partage)
    conn.execute("CREATE TABLE marqueur (x INTEGER)")
    conn.execute("INSERT INTO marqueur VALUES (42)")
    conn.commit()
    conn.close()

    results_store._migrer_ancienne_base_partagee_si_necessaire(chemin_station, chemin_partage)

    assert not os.path.exists(chemin_partage)
    assert os.path.exists(chemin_station)
    conn = sqlite3.connect(chemin_station)
    assert conn.execute("SELECT x FROM marqueur").fetchone() == (42,)
    conn.close()


def test_migration_base_partagee_ne_touche_pas_une_base_station_existante(tmp_path):
    chemin_partage = str(tmp_path / "runs.sqlite3")
    chemin_station = str(tmp_path / "runs_Y999999999.sqlite3")
    for chemin, valeur in ((chemin_partage, 1), (chemin_station, 2)):
        conn = sqlite3.connect(chemin)
        conn.execute("CREATE TABLE marqueur (x INTEGER)")
        conn.execute("INSERT INTO marqueur VALUES (?)", (valeur,))
        conn.commit()
        conn.close()

    results_store._migrer_ancienne_base_partagee_si_necessaire(chemin_station, chemin_partage)

    # Aucun écrasement : les 2 fichiers restent en l'état, la base station existante
    # garde SA valeur (2), pas celle de l'ancienne base partagée (1).
    assert os.path.exists(chemin_partage)
    conn = sqlite3.connect(chemin_station)
    assert conn.execute("SELECT x FROM marqueur").fetchone() == (2,)
    conn.close()


def test_migration_base_partagee_sans_effet_si_rien_a_migrer(tmp_path):
    chemin_partage = str(tmp_path / "runs.sqlite3")
    chemin_station = str(tmp_path / "runs_Y999999999.sqlite3")
    results_store._migrer_ancienne_base_partagee_si_necessaire(chemin_station, chemin_partage)
    assert not os.path.exists(chemin_partage)
    assert not os.path.exists(chemin_station)


# -- _migrer_schema_multi_instants ---------------------------------------------------------

def test_migration_schema_multi_instants_conserve_les_donnees_existantes(tmp_path):
    chemin = str(tmp_path / "ancienne_base.sqlite3")
    conn = sqlite3.connect(chemin)
    conn.executescript(_ANCIEN_SCHEMA)
    conn.execute(
        "INSERT INTO combinaisons (id, horizon, seuil_c1, methode, statut, date_maj) "
        "VALUES (1, '01J00H00M', 5.0, 'T', 'success', '2026-08-01T10:00:00')")
    conn.execute(
        "INSERT INTO resultats_crues "
        "(combinaison_id, crue_date, statut, dqp, dtp, ve, kge, date_maj) "
        "VALUES (1, '2024-01-01T00:00:00', 'success', 5.0, 1.0, 3.0, 0.9, "
        "'2026-08-01T10:05:00')")
    conn.execute(
        "INSERT INTO series_archivees (combinaison_id, crue_date, type, point_date, "
        "debit, pluie) VALUES (1, '2024-01-01T00:00:00', 'sim', "
        "'2024-01-01T00:00:00', 12.5, 0.0)")
    conn.commit()
    conn.close()

    results_store.init_db(chemin)  # déclenche la migration automatiquement

    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    resultats = conn.execute("SELECT * FROM resultats_crues").fetchall()
    assert len(resultats) == 1
    assert resultats[0]["instant_label"] == "reference"
    assert resultats[0]["dqp"] == 5.0
    series = conn.execute("SELECT * FROM series_archivees").fetchall()
    assert len(series) == 1
    assert series[0]["instant_label"] == "reference"
    assert series[0]["debit"] == 12.5
    conn.close()


def test_migration_schema_multi_instants_idempotente(tmp_path):
    chemin = str(tmp_path / "base_neuve.sqlite3")
    results_store.init_db(chemin)  # base neuve, déjà au nouveau schéma
    # Un second appel direct ne doit rien casser (retour immédiat, colonne déjà là)
    with results_store.db_session(chemin) as conn:
        results_store._migrer_schema_multi_instants(conn)


# -- duree_par_etape ------------------------------------------------------------------------

def test_duree_par_etape_cas_nominal(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    with results_store.db_session(chemin) as conn:
        c1 = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T")
        conn.execute("UPDATE combinaisons SET date_maj = ? WHERE id = ?",
                     ("2026-08-01T10:00:00", c1))
        results_store.upsert_resultat_crue(conn, c1, "2024-01-01T00:00:00", "success")
        conn.execute("UPDATE resultats_crues SET date_maj = ? WHERE combinaison_id = ?",
                     ("2026-08-01T10:00:20", c1))
        c2 = results_store.upsert_combinaison(conn, "02J00H00M", 5.0, "T")
        conn.execute("UPDATE combinaisons SET date_maj = ? WHERE id = ?",
                     ("2026-08-01T10:15:20", c2))
    with results_store.db_session(chemin) as conn:
        resultat = results_store.duree_par_etape(conn)
    # Chronologie : calage c1 (10:00:00) -> rejeu c1 (+20s) -> calage c2 (+15min)
    assert resultat["rejeu"]["T"]["nb_mesures"] == 1
    assert resultat["rejeu"]["T"]["minutes"] == pytest.approx(20 / 60, rel=1e-3)
    assert resultat["calage"]["T"]["nb_mesures"] == 1
    assert resultat["calage"]["T"]["minutes"] == pytest.approx(15, rel=1e-3)


def test_duree_par_etape_ignore_ecart_au_dela_du_seuil_idle(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    with results_store.db_session(chemin) as conn:
        c1 = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T")
        conn.execute("UPDATE combinaisons SET date_maj = ? WHERE id = ?",
                     ("2026-08-01T10:00:00", c1))
        results_store.upsert_resultat_crue(conn, c1, "2024-01-01T00:00:00", "success")
        # Écart de 2h -> signe d'une session fermée puis relancée, pas une vraie durée
        # de rejeu -> doit être exclu (seuil_idle_minutes=35 par défaut).
        conn.execute("UPDATE resultats_crues SET date_maj = ? WHERE combinaison_id = ?",
                     ("2026-08-01T12:00:00", c1))
    with results_store.db_session(chemin) as conn:
        resultat = results_store.duree_par_etape(conn, seuil_idle_minutes=35)
    assert resultat["rejeu"]["T"]["nb_mesures"] == 0
    assert resultat["rejeu"]["T"]["minutes"] is None


# -- estimer_temps_restant -------------------------------------------------------------------

def test_estimer_temps_restant_rien_acquis(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    duree_data = {"calage": {"T": {"minutes": 20.0, "nb_mesures": 3}},
                  "rejeu": {"T": {"minutes": 0.5, "nb_mesures": 5}}}
    with results_store.db_session(chemin) as conn:
        minutes, restantes, total, incertain = results_store.estimer_temps_restant(
            conn, [("01J00H00M", 5.0, "T")],
            ["2024-01-01T00:00:00", "2024-02-01T00:00:00"], duree_data)
    assert total == 3  # 1 calage + 2 crues
    assert restantes == 3  # rien en base -> tout restant
    assert minutes == pytest.approx(20.0 + 0.5 * 2)
    assert incertain is False


def test_estimer_temps_restant_tout_deja_acquis(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    with results_store.db_session(chemin) as conn:
        cid = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, cid, "2024-01-01T00:00:00", "success")
    duree_data = {"calage": {"T": {"minutes": 20.0, "nb_mesures": 3}},
                  "rejeu": {"T": {"minutes": 0.5, "nb_mesures": 5}}}
    with results_store.db_session(chemin) as conn:
        minutes, restantes, total, incertain = results_store.estimer_temps_restant(
            conn, [("01J00H00M", 5.0, "T")], ["2024-01-01T00:00:00"], duree_data)
    assert total == 2
    assert restantes == 0
    assert minutes == 0.0
    assert incertain is False


def test_estimer_temps_restant_incertain_si_aucune_mesure_disponible(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    duree_data = {"calage": {}, "rejeu": {}}  # aucune mesure pour la méthode R
    with results_store.db_session(chemin) as conn:
        minutes, restantes, total, incertain = results_store.estimer_temps_restant(
            conn, [("01J00H00M", 5.0, "R")], ["2024-01-01T00:00:00"], duree_data)
    assert incertain is True
    assert minutes == 0.0  # rien à ajouter faute de mesure


# -- etat_combinaisons ------------------------------------------------------------------------

def test_etat_combinaisons_agrege_crues_ok_et_ko(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    with results_store.db_session(chemin) as conn:
        cid = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, cid, "2024-01-01T00:00:00", "success")
        results_store.upsert_resultat_crue(conn, cid, "2024-02-01T00:00:00", "failed")
    with results_store.db_session(chemin) as conn:
        etats = results_store.etat_combinaisons(conn)
    cle = ("01J00H00M", 5.0, "T")
    assert etats[cle]["statut"] == "success"
    assert etats[cle]["crues_ok"] == 1
    assert etats[cle]["crues_ko"] == 1


def test_etat_combinaisons_ignore_les_instants_supplementaires(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    with results_store.db_session(chemin) as conn:
        cid = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, cid, "2024-01-01T00:00:00", "success")
        results_store.upsert_resultat_crue(conn, cid, "2024-01-01T00:00:00", "success",
                                            instant_label="H-24")
    with results_store.db_session(chemin) as conn:
        etats = results_store.etat_combinaisons(conn)
    # L'instant H-24 ne doit JAMAIS gonfler ce compteur (filtré sur 'reference').
    assert etats[("01J00H00M", 5.0, "T")]["crues_ok"] == 1


# -- resume_couverture ------------------------------------------------------------------------

def test_resume_couverture_compte_par_dimension(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    with results_store.db_session(chemin) as conn:
        c1 = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, c1, "2024-01-01T00:00:00", "success")
        c2 = results_store.upsert_combinaison(conn, "01J00H00M", 10.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, c2, "2024-01-01T00:00:00", "failed")
    with results_store.db_session(chemin) as conn:
        couverture = results_store.resume_couverture(conn)
    assert couverture["horizons"]["01J00H00M"]["tentes"] == 2
    assert couverture["horizons"]["01J00H00M"]["complets"] == 1
    assert couverture["seuils"][5.0]["complets"] == 1
    assert couverture["seuils"][10.0]["complets"] == 0


# -- list_combinaisons_completes ---------------------------------------------------------------

def test_list_combinaisons_completes_exclut_les_combinaisons_avec_un_echec(tmp_path):
    chemin = str(tmp_path / "base.sqlite3")
    results_store.init_db(chemin)
    with results_store.db_session(chemin) as conn:
        c1 = results_store.upsert_combinaison(conn, "01J00H00M", 5.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, c1, "2024-01-01T00:00:00", "success")
        results_store.upsert_resultat_crue(conn, c1, "2024-02-01T00:00:00", "success")
        c2 = results_store.upsert_combinaison(conn, "02J00H00M", 5.0, "T", statut="success")
        results_store.upsert_resultat_crue(conn, c2, "2024-01-01T00:00:00", "success")
        results_store.upsert_resultat_crue(conn, c2, "2024-02-01T00:00:00", "failed")
    with results_store.db_session(chemin) as conn:
        completes = results_store.list_combinaisons_completes(conn)
    horizons = {c["horizon"] for c in completes}
    assert horizons == {"01J00H00M"}
