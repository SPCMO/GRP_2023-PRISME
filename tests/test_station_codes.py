# -*- coding: utf-8 -*-
"""Tests de modules/station_codes.py — conversions code station <-> code site."""

import pytest

from modules.station_codes import (
    CodeStationError,
    code_site_depuis_station,
    code_station_par_defaut_depuis_site,
    valider_code_station,
)


def test_valider_code_station_normalise_espaces_et_casse():
    assert valider_code_station(" y161202001 ") == "Y161202001"


def test_valider_code_station_refuse_format_invalide():
    with pytest.raises(CodeStationError):
        valider_code_station("Y16120200")  # 8 chiffres seulement, pas 9
    with pytest.raises(CodeStationError):
        valider_code_station("161202001")  # pas de lettre en tête
    with pytest.raises(CodeStationError):
        valider_code_station("")


def test_code_site_depuis_station_retire_les_2_derniers_chiffres():
    assert code_site_depuis_station("Y161202001") == "Y1612020"


def test_code_station_par_defaut_depuis_site_ajoute_01():
    assert code_station_par_defaut_depuis_site("Y1612020") == "Y161202001"
    assert code_station_par_defaut_depuis_site("Y1112010") == "Y111201001"


def test_code_station_par_defaut_depuis_site_normalise_espaces_et_casse():
    assert code_station_par_defaut_depuis_site(" y1612020 ") == "Y161202001"


def test_code_station_par_defaut_depuis_site_refuse_format_invalide():
    with pytest.raises(CodeStationError):
        code_station_par_defaut_depuis_site("Y16120200")  # 8 chiffres, pas 7
    with pytest.raises(CodeStationError):
        code_station_par_defaut_depuis_site("1612020")  # pas de lettre en tête
    with pytest.raises(CodeStationError):
        code_station_par_defaut_depuis_site("")


def test_code_station_par_defaut_est_linverse_de_code_site_depuis_station():
    """Round-trip : dériver un code station par défaut depuis un code site, puis en
    redériver le code site, doit redonner exactement le code site de départ — les 2
    fonctions sont bien symétriques (ajout vs retrait des 2 derniers chiffres)."""
    code_site = "Y1612020"
    code_station = code_station_par_defaut_depuis_site(code_site)
    assert code_site_depuis_station(code_station) == code_site
