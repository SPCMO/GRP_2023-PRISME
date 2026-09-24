# -*- coding: utf-8 -*-
"""Tests de modules/appariement_crues.py — reconnaissance d'une même crue vue sous des
dates de début différentes (durée de fenêtre d'événement NJ changée entre deux
campagnes : 48 h -> 72 h, début décalé de 12 h, MÊME pic — incident réel du 24 septembre
2026 sur Carcassonne_PV)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from modules.appariement_crues import (
    TOLERANCE_PIC,
    evenement_par_pic,
    pic_et_metriques,
    reconcilier_selection,
)


def _dt(j, h, m=0):
    return datetime(2026, 3, j, h, m)


# -- pic_et_metriques ---------------------------------------------------------------

def test_pic_et_metriques_pic_bornes_et_cumul():
    points = [(_dt(1, 0), 1.0, 10.0), (_dt(1, 1), 2.0, 250.5), (_dt(1, 2), 0.5, 120.0)]
    m = pic_et_metriques(points)
    assert m["pic_date"] == _dt(1, 1)
    assert m["qmax"] == 250.5
    assert m["cumul_pluie"] == 3.5
    assert (m["debut"], m["fin"]) == (_dt(1, 0), _dt(1, 2))


def test_pic_et_metriques_plateau_retient_le_premier_instant():
    points = [(_dt(1, 0), 0, 5.0), (_dt(1, 1), 0, 9.0), (_dt(1, 2), 0, 9.0)]
    assert pic_et_metriques(points)["pic_date"] == _dt(1, 1)


def test_pic_et_metriques_ignore_les_valeurs_none_et_serie_sans_debit():
    assert pic_et_metriques([(_dt(1, 0), 1.0, None)]) is None
    assert pic_et_metriques([]) is None
    m = pic_et_metriques([(_dt(1, 0), None, 4.0), (_dt(1, 1), 2.0, None)])
    assert m["qmax"] == 4.0 and m["cumul_pluie"] == 2.0


# -- evenement_par_pic --------------------------------------------------------------

def _evt(num, pic):
    return SimpleNamespace(num_evt=num, date_qmax=pic)


def test_evenement_par_pic_choisit_le_plus_proche_dans_la_tolerance():
    evenements = [_evt(1, _dt(5, 10)), _evt(2, _dt(5, 12)), _evt(3, _dt(20, 0))]
    assert evenement_par_pic(_dt(5, 11), evenements).num_evt == 1  # égalité -> plus petit n°
    assert evenement_par_pic(_dt(5, 12, 30), evenements).num_evt == 2


def test_evenement_par_pic_hors_tolerance_ou_pic_absent():
    evenements = [_evt(1, _dt(5, 10))]
    assert evenement_par_pic(_dt(5, 10) + TOLERANCE_PIC + timedelta(minutes=1), evenements) is None
    assert evenement_par_pic(None, evenements) is None
    assert evenement_par_pic(_dt(5, 10), []) is None


# -- reconcilier_selection : le scénario réel NJ=2 -> NJ=3 ----------------------------

def test_selection_nj3_reconnue_comme_deja_calee_sous_les_dates_nj2():
    # 3 crues, dates de début NJ=3 (pic à +36 h) ; en base, mêmes crues sous leurs dates
    # NJ=2 (pic à +24 h, donc début 12 h PLUS TARD) — même pic.
    pics = {_dt(1, 0): _dt(2, 12), _dt(10, 0): _dt(11, 12), _dt(20, 0): _dt(21, 12)}
    connues = {_dt(1, 12): _dt(2, 12), _dt(10, 12): _dt(11, 12), _dt(20, 12): _dt(21, 12)}
    bilan = reconcilier_selection([_dt(1, 0), _dt(10, 0), _dt(20, 0)], pics, connues)
    assert bilan.dates_effectives == [_dt(1, 12), _dt(10, 12), _dt(20, 12)]
    assert bilan.remplacements == {_dt(1, 0): _dt(1, 12), _dt(10, 0): _dt(10, 12),
                                   _dt(20, 0): _dt(20, 12)}
    assert bilan.nouvelles == [] and bilan.sans_pic == [] and bilan.deja_connues == []


def test_date_deja_connue_telle_quelle_est_gardee_meme_avec_un_autre_pic_proche():
    connues = {_dt(1, 0): _dt(2, 0), _dt(1, 12): _dt(2, 1)}
    bilan = reconcilier_selection([_dt(1, 0)], {_dt(1, 0): _dt(2, 0)}, connues)
    assert bilan.dates_effectives == [_dt(1, 0)]
    assert bilan.deja_connues == [_dt(1, 0)] and bilan.remplacements == {}


def test_crue_nouvelle_et_crue_sans_pic():
    connues = {_dt(1, 12): _dt(2, 12)}
    pics = {_dt(1, 0): _dt(2, 12), _dt(15, 0): _dt(16, 12)}  # 15/03 : jamais calée
    bilan = reconcilier_selection([_dt(1, 0), _dt(15, 0), _dt(25, 0)], pics, connues)
    assert bilan.remplacements == {_dt(1, 0): _dt(1, 12)}
    assert bilan.nouvelles == [_dt(15, 0)]
    assert bilan.sans_pic == [_dt(25, 0)]  # absente du CRITERES_PERF.DAT : pic inconnu
    assert bilan.dates_effectives == [_dt(1, 12), _dt(15, 0), _dt(25, 0)]


def test_une_date_connue_nest_attribuee_qua_une_seule_crue_selectionnee():
    """Deux crues sélectionnées au pic quasi identique ne se partagent pas la même date
    en base (sinon doublon de clé) — la plus proche l'emporte, l'autre reste nouvelle."""
    connues = {_dt(1, 12): _dt(2, 12)}
    pics = {_dt(1, 0): _dt(2, 12), _dt(1, 1): _dt(2, 13)}
    bilan = reconcilier_selection([_dt(1, 1), _dt(1, 0)], pics, connues)
    assert bilan.dates_effectives == [_dt(1, 1), _dt(1, 12)]
    assert bilan.remplacements == {_dt(1, 0): _dt(1, 12)}
    assert bilan.nouvelles == [_dt(1, 1)]
    assert len(set(bilan.dates_effectives)) == len(bilan.dates_effectives)


def test_ne_reindexe_jamais_les_dates_en_base():
    """Les dates déjà en base sont reprises telles quelles — jamais l'inverse."""
    connues = {_dt(1, 12): _dt(2, 12)}
    bilan = reconcilier_selection([_dt(1, 0)], {_dt(1, 0): _dt(2, 12)}, connues)
    assert bilan.dates_effectives[0] in connues
