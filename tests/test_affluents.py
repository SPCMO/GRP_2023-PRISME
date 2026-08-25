# -*- coding: utf-8 -*-
"""Tests unitaires — modules/affluents.py (fonctions pures, sans Tkinter ni fichier
réel). Voir la feuille de route de l'audit du 25/08/2026, point code n°5."""

from datetime import datetime, timedelta

import pytest

from modules.affluents import (
    Affluent,
    bornes_bande_propagation,
    hhmm_vers_minutes,
    minutes_vers_hhmm,
    qmax_et_horodatage,
    valeur_au_plus_proche,
    volume_m3,
)


# -- hhmm_vers_minutes / minutes_vers_hhmm --------------------------------------------

def test_hhmm_vers_minutes_cas_normal():
    assert hhmm_vers_minutes("02:30") == 150


def test_hhmm_vers_minutes_vide_ou_none():
    assert hhmm_vers_minutes("") is None
    assert hhmm_vers_minutes(None) is None
    assert hhmm_vers_minutes("   ") is None


def test_hhmm_vers_minutes_format_invalide():
    with pytest.raises(ValueError):
        hhmm_vers_minutes("2h30")


def test_hhmm_vers_minutes_minutes_hors_bornes():
    with pytest.raises(ValueError):
        hhmm_vers_minutes("01:75")


def test_minutes_vers_hhmm_aller_retour():
    assert minutes_vers_hhmm(150) == "02:30"
    assert minutes_vers_hhmm(None) == ""
    assert hhmm_vers_minutes(minutes_vers_hhmm(150)) == 150


# -- qmax_et_horodatage -----------------------------------------------------------------

def test_qmax_et_horodatage_cas_normal():
    d0 = datetime(2024, 1, 1, 0, 0)
    serie = [(d0, 10.0), (d0 + timedelta(hours=1), 50.0), (d0 + timedelta(hours=2), 30.0)]
    qmax, date_qmax = qmax_et_horodatage(serie)
    assert qmax == 50.0
    assert date_qmax == d0 + timedelta(hours=1)


def test_qmax_et_horodatage_serie_vide():
    assert qmax_et_horodatage([]) == (None, None)


# -- volume_m3 (intégration trapézoïdale) ------------------------------------------------

def test_volume_m3_debit_constant():
    # Débit constant de 10 m3/s pendant 1h (3600s) -> volume = 10 * 3600 = 36000 m3
    d0 = datetime(2024, 1, 1, 0, 0)
    serie = [(d0, 10.0), (d0 + timedelta(hours=1), 10.0)]
    assert volume_m3(serie) == pytest.approx(36000.0)


def test_volume_m3_moins_de_2_points():
    assert volume_m3([]) is None
    assert volume_m3([(datetime(2024, 1, 1), 5.0)]) is None


# -- valeur_au_plus_proche --------------------------------------------------------------

def test_valeur_au_plus_proche_choisit_le_point_le_plus_proche():
    d0 = datetime(2024, 1, 1, 0, 0)
    serie = [(d0, 1.0), (d0 + timedelta(minutes=15), 2.0), (d0 + timedelta(minutes=30), 3.0)]
    # Cible à 20 min : le point à 15 min (écart 5 min) est plus proche que celui à
    # 30 min (écart 10 min).
    valeur, date_trouvee = valeur_au_plus_proche(serie, d0 + timedelta(minutes=20))
    assert valeur == 2.0
    assert date_trouvee == d0 + timedelta(minutes=15)


def test_valeur_au_plus_proche_serie_vide_ou_date_none():
    assert valeur_au_plus_proche([], datetime(2024, 1, 1)) == (None, None)
    assert valeur_au_plus_proche([(datetime(2024, 1, 1), 1.0)], None) == (None, None)


# -- bornes_bande_propagation -----------------------------------------------------------

def test_bornes_bande_propagation_cas_complet():
    date_pic = datetime(2024, 1, 1, 12, 0)
    affluent = Affluent(nom="Test", p10_min=30, p50_min=60, p90_min=120)
    p10, p50, p90 = bornes_bande_propagation(date_pic, affluent)
    assert p10 == date_pic + timedelta(minutes=30)
    assert p50 == date_pic + timedelta(minutes=60)
    assert p90 == date_pic + timedelta(minutes=120)


def test_bornes_bande_propagation_p10_p90_facultatifs():
    date_pic = datetime(2024, 1, 1, 12, 0)
    affluent = Affluent(nom="Test", p10_min=None, p50_min=60, p90_min=None)
    p10, p50, p90 = bornes_bande_propagation(date_pic, affluent)
    assert p10 is None
    assert p50 == date_pic + timedelta(minutes=60)
    assert p90 is None


def test_bornes_bande_propagation_sans_p50_ni_date():
    affluent_sans_p50 = Affluent(nom="Test", p50_min=None)
    assert bornes_bande_propagation(datetime(2024, 1, 1), affluent_sans_p50) == (None, None, None)
    affluent_avec_p50 = Affluent(nom="Test", p50_min=60)
    assert bornes_bande_propagation(None, affluent_avec_p50) == (None, None, None)
