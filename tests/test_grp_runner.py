# -*- coding: utf-8 -*-
"""Tests de modules.grp_runner.run_calage avec de faux « exécutables » .bat (Windows)."""

import sys
import time

import pytest

from modules import grp_runner

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="faux exécutables .bat (Windows)")


def _bat(tmp_path, corps):
    chemin = tmp_path / "faux_calage.bat"
    chemin.write_text("@echo off\r\n" + corps + "\r\n", encoding="cp1252")
    return chemin


def test_run_calage_succes_retourne_la_sortie(tmp_path):
    resultat = grp_runner.run_calage(_bat(tmp_path, "echo calage ok"), timeout=60)
    assert resultat.returncode == 0
    assert "calage ok" in resultat.stdout


def test_run_calage_code_retour_non_nul_leve_avec_la_sortie(tmp_path):
    with pytest.raises(grp_runner.GrpRunError) as exc:
        grp_runner.run_calage(_bat(tmp_path, "echo boum\r\nexit /b 3"), timeout=60)
    assert "code 3" in str(exc.value) and "boum" in str(exc.value)


def test_run_calage_timeout_rend_la_main_vite_meme_avec_un_processus_enfant(tmp_path):
    """Un descendant (ici `ping`, comme le Rscript du calage RNA) tient les tubes de sortie
    ouverts : avec subprocess.run, l'appel restait bloqué bien après l'expiration du délai.
    Ici l'arbre est tué et l'appel rend la main peu après le délai."""
    debut = time.monotonic()
    with pytest.raises(grp_runner.GrpRunError) as exc:
        grp_runner.run_calage(_bat(tmp_path, "ping -n 60 127.0.0.1 > nul"), timeout=2)
    assert "n'a pas terminé en 2s" in str(exc.value)
    assert time.monotonic() - debut < 25
