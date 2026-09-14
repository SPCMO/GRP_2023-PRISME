# -*- coding: utf-8 -*-
"""Tests de modules/verif_version.py — vérification non bloquante de version (brique
vendue depuis Git-Claude IA/Verif_Version_Outils, voir docstring du module)."""

import json

from modules.verif_version import verifier_version_disponible


def _ecrire_manifeste(tmp_path, contenu):
    chemin = tmp_path / "VERSION.json"
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    return str(chemin)


def test_version_distante_plus_recente_avec_changelog(tmp_path):
    chemin = _ecrire_manifeste(tmp_path, {
        "version": "3.3",
        "changelog": [
            {"version": "3.3", "resume": "Nouveaute A"},
            {"version": "3.2", "resume": "Correctif B"},
            {"version": "3.1", "resume": "Ancien, deja connu"},
        ],
    })
    r = verifier_version_disponible("3.1", chemin)
    assert r.nouvelle_version_disponible
    assert r.version_distante == "3.3"
    # Seules les versions STRICTEMENT postérieures à la locale (3.1) apparaissent,
    # triées de la plus récente à la plus ancienne.
    assert [e["version"] for e in r.nouveautes] == ["3.3", "3.2"]
    assert "v3.3" in r.message and "v3.1" in r.message
    assert "Nouveaute A" in r.message and "Correctif B" in r.message
    assert "Ancien, deja connu" not in r.message


def test_version_locale_deja_a_jour_ne_signale_rien(tmp_path):
    chemin = _ecrire_manifeste(tmp_path, {"version": "3.1", "changelog": []})
    r = verifier_version_disponible("3.1", chemin)
    assert not r.nouvelle_version_disponible
    assert r.message == ""


def test_version_locale_en_avance_ne_signale_rien(tmp_path):
    """Poste de développement resté sur une version plus récente que celle
    déployée — ne doit jamais rien afficher (pas de "régression" signalée)."""
    chemin = _ecrire_manifeste(tmp_path, {"version": "3.0", "changelog": []})
    r = verifier_version_disponible("3.5", chemin)
    assert not r.nouvelle_version_disponible


def test_comparaison_numerique_pas_alphabetique(tmp_path):
    """"3.10" doit être vue comme postérieure à "3.9" — une comparaison de
    chaînes ferait l'inverse à tort ("3.9" > "3.10" alphabétiquement)."""
    chemin = _ecrire_manifeste(tmp_path, {"version": "3.10", "changelog": []})
    r = verifier_version_disponible("3.9", chemin)
    assert r.nouvelle_version_disponible
    assert r.version_distante == "3.10"


def test_fichier_manifeste_absent_ne_leve_jamais(tmp_path):
    chemin = str(tmp_path / "chemin_inexistant" / "VERSION.json")
    r = verifier_version_disponible("3.1", chemin)
    assert not r.nouvelle_version_disponible
    assert r.erreur  # renseigné pour un éventuel journal, jamais affiché à l'utilisateur


def test_json_invalide_ne_leve_jamais(tmp_path):
    chemin = tmp_path / "VERSION.json"
    chemin.write_text("{ceci n'est pas du JSON", encoding="utf-8")
    r = verifier_version_disponible("3.1", str(chemin))
    assert not r.nouvelle_version_disponible
    assert r.erreur


def test_champ_version_absent_ou_vide_ne_leve_jamais(tmp_path):
    chemin = _ecrire_manifeste(tmp_path, {"changelog": []})
    r = verifier_version_disponible("3.1", chemin)
    assert not r.nouvelle_version_disponible


def test_changelog_absent_donne_message_sans_detail(tmp_path):
    chemin = _ecrire_manifeste(tmp_path, {"version": "3.2"})
    r = verifier_version_disponible("3.1", chemin)
    assert r.nouvelle_version_disponible
    assert r.nouveautes == []
    assert r.message == "Nouvelle version disponible : v3.2 (vous avez v3.1)."


def test_entree_changelog_sans_resume_ignoree_dans_le_message(tmp_path):
    chemin = _ecrire_manifeste(tmp_path, {
        "version": "3.2",
        "changelog": [{"version": "3.2", "resume": ""}],
    })
    r = verifier_version_disponible("3.1", chemin)
    assert r.nouvelle_version_disponible
    # L'entrée existe dans .nouveautes (brute) mais ne produit pas de ligne "•"
    # vide dans le message affiché à l'utilisateur.
    assert r.nouveautes == [{"version": "3.2", "resume": ""}]
    assert "•" not in r.message
