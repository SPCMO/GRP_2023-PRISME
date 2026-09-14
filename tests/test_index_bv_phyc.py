# -*- coding: utf-8 -*-
"""Tests de modules/index_bv_phyc.py — index de recherche local des bassins versants
PHyC (brique vendue depuis Git-Claude IA/Index_BV_PHyC, voir docstring du module).

Un CSV minimal dédié (tmp_path) isole ces tests de l'évolution du vrai
modules/bv_phyc.csv — un seul test sanity-check charge le vrai fichier livré avec
l'outil, pour détecter un CSV corrompu/vide sans dépendre de son contenu exact."""

import pytest

from modules.index_bv_phyc import CSV_PAR_DEFAUT, IndexBV

_CSV_MINIMAL = (
    "CdBNBV;CdSite;NomBV;SurfaceKm2\n"
    "MO1;Y1612020;Moussoulens;1234.5\n"
    "MO2;Y1422030;Trèbes;987,6\n"  # virgule décimale — doit être convertie en point
    "MO3;Y1112010;L'Aude à Belvianes-et-Cavirac;\n"  # surface inconnue -> None
)


@pytest.fixture
def index(tmp_path):
    chemin = tmp_path / "bv_phyc.csv"
    chemin.write_text(_CSV_MINIMAL, encoding="utf-8")
    return IndexBV(str(chemin))


def test_rechercher_insensible_casse_et_accents(index):
    resultats = index.rechercher("AUDE")  # "Aude" absent tel quel, mais "à Belvianes..."
    assert [r["nom_bv"] for r in resultats] == ["L'Aude à Belvianes-et-Cavirac"]
    # "trebes" (sans accent, minuscule) doit retrouver "Trèbes"
    assert [r["nom_bv"] for r in index.rechercher("trebes")] == ["Trèbes"]


def test_rechercher_terme_vide_retourne_liste_vide(index):
    assert index.rechercher("") == []
    assert index.rechercher("   ") == []


def test_rechercher_trie_debut_de_nom_avant_alphabetique(index):
    # "L'Aude..." commence par "l", "Moussoulens" ne fait que contenir un "l" ailleurs
    # (positionné après "Trèbes" alphabétiquement, mais ça n'a pas d'importance ici :
    # seul le début de nom prime, peu importe le rang alphabétique du reste).
    resultats = index.rechercher("l")
    noms = [r["nom_bv"] for r in resultats]
    assert noms.index("L'Aude à Belvianes-et-Cavirac") < noms.index("Moussoulens")


def test_surface_virgule_convertie_en_point(index):
    r = index.par_code_site("Y1422030")
    assert r["surface_km2"] == pytest.approx(987.6)


def test_surface_absente_retourne_none(index):
    r = index.par_code_site("Y1112010")
    assert r["surface_km2"] is None


def test_par_code_site_insensible_a_la_casse_et_absent_retourne_none(index):
    assert index.par_code_site("y1612020")["nom_bv"] == "Moussoulens"
    assert index.par_code_site("Y0000000") is None


def test_len_reflete_le_nombre_dentrees_chargees(index):
    assert len(index) == 3


def test_recharger_relit_le_fichier_modifie_depuis(tmp_path):
    chemin = tmp_path / "bv_phyc.csv"
    chemin.write_text(_CSV_MINIMAL, encoding="utf-8")
    index = IndexBV(str(chemin))
    assert len(index) == 3
    chemin.write_text(_CSV_MINIMAL + "MO4;Y9999999;Nouveau site;10\n", encoding="utf-8")
    index.recharger()
    assert len(index) == 4


def test_csv_par_defaut_livre_avec_loutil_se_charge_sans_erreur():
    """Sanity-check : modules/bv_phyc.csv (livré avec l'outil, pas le CSV minimal des
    tests ci-dessus) est un CSV valide et non vide — détecte un fichier corrompu ou
    accidentellement vidé sans dépendre de son contenu exact (~65 bassins versants du
    bordereau SPCMO au moment de l'ajout de cette brique)."""
    index = IndexBV(CSV_PAR_DEFAUT)
    assert len(index) > 0
