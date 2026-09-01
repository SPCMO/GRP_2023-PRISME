# -*- coding: utf-8 -*-
"""Tests unitaires — modules/results_store.py, uniquement la résolution du dossier de
stockage des bases (dossier_data_effectif) : voir ui/tab_config.py, bandeau "Dossier de
stockage des bases de résultats", et config.FICHIER_POINTEUR_DATA — corrige un incident
réel où une réinstallation de l'outil dans un nouveau dossier repartait avec une base
vierge, l'ancienne (restée dans l'ancien dossier) n'étant ni retrouvée ni signalée.

`app_config.FICHIER_POINTEUR_DATA` est monkeypatché vers un chemin de test à chaque
test (jamais le vrai %APPDATA% de la machine qui exécute la suite), pour rester isolé
et sans effet de bord sur une éventuelle configuration réelle déjà en place."""
import os

import config as app_config
from modules import results_store


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
