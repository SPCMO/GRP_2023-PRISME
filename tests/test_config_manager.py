# -*- coding: utf-8 -*-
"""Tests unitaires — modules/config_manager.py.

Config par station (7 septembre 2026, demande explicite) : config/config.json reste
PARTAGÉ (phyc, alertes, station_active) ; config/config_<code_station>.json est
PROPRE à chaque station (voir CLES_PAR_STATION). `load_config`/`save_config` fusionnent
et répartissent transparemment ; `basculer_vers_station` gère le VRAI changement de
station en cours de session (mutation en place, voir sa docstring).

Chaque test monkeypatche app_config.CONFIG_JSON_PATH/CONFIG_EXEMPLE_PATH vers des
chemins temporaires (tmp_path) — jamais le vrai config/config.json de la machine qui
exécute la suite (même principe que test_results_store.py pour FICHIER_POINTEUR_DATA)."""

import json

import pytest

import config as app_config
from modules import config_manager


@pytest.fixture
def chemins(tmp_path, monkeypatch):
    """Redirige CONFIG_JSON_PATH/CONFIG_EXEMPLE_PATH vers un dossier temporaire, avec
    un gabarit exemple minimal mais réaliste (quelques clés par station + partagées)."""
    dossier = tmp_path / "config"
    dossier.mkdir()
    chemin_json = dossier / "config.json"
    chemin_exemple = dossier / "config.exemple.json"
    chemin_exemple.write_text(json.dumps({
        "phyc": {"idcontact": "", "motdepasse": ""},
        "alertes": {"active": False, "topic": ""},
        "station": {"nom_station": "", "code_station": ""},
        "chemins": {"dossier_grp": ""},
        "parametrage": {"pas_de_temps": [], "horizons_selectionnes": {}},
        "score": {},
        "seuils_q": {},
        "crues_selectionnees": [],
        "crues_inclure_pluie": False,
    }), encoding="utf-8")
    monkeypatch.setattr(app_config, "CONFIG_JSON_PATH", str(chemin_json))
    monkeypatch.setattr(app_config, "CONFIG_EXEMPLE_PATH", str(chemin_exemple))
    return chemin_json, chemin_exemple, dossier


# -- load_config : premier lancement, fusion, migration ------------------------------

def test_load_config_premier_lancement_copie_le_gabarit(chemins):
    chemin_json, _chemin_exemple, _dossier = chemins
    assert not chemin_json.exists()
    config_data = config_manager.load_config()
    assert chemin_json.exists()
    assert config_data["phyc"] == {"idcontact": "", "motdepasse": ""}
    assert config_data["station"]["code_station"] == ""


def test_load_config_sans_station_identifiee_tout_reste_partage(chemins):
    """Tant que code_station est vide, aucun fichier par station n'est créé — le
    comportement reste celui d'avant ce correctif."""
    chemin_json, _chemin_exemple, dossier = chemins
    config_manager.load_config()
    assert list(dossier.glob("config_*.json")) == []


def test_load_config_fusionne_le_fichier_station_actif(chemins):
    chemin_json, _chemin_exemple, dossier = chemins
    chemin_json.write_text(json.dumps({
        "phyc": {"idcontact": "moi", "motdepasse": "x"},
        "station_active": "Y1612020001",
    }), encoding="utf-8")
    (dossier / "config_Y1612020001.json").write_text(json.dumps({
        "station": {"nom_station": "Moussoulens", "code_station": "Y1612020001"},
        "chemins": {"dossier_grp": "D:/GRP"},
    }), encoding="utf-8")

    config_data = config_manager.load_config()
    assert config_data["phyc"]["idcontact"] == "moi"
    assert config_data["station"]["nom_station"] == "Moussoulens"
    assert config_data["chemins"]["dossier_grp"] == "D:/GRP"


def test_load_config_migre_lancien_format_mixte_vers_le_fichier_station(chemins):
    """Un config.json d'AVANT ce correctif a encore ses clés par-station mélangées
    avec les clés partagées — la première relecture doit les extraire vers le
    fichier de la station active, sans rien perdre."""
    chemin_json, _chemin_exemple, dossier = chemins
    chemin_json.write_text(json.dumps({
        "phyc": {"idcontact": "moi", "motdepasse": "x"},
        "station": {"nom_station": "Moussoulens", "code_station": "Y1612020001"},
        "chemins": {"dossier_grp": "D:/GRP"},
        "parametrage": {"seuils_calage": [5.0]},
    }), encoding="utf-8")

    config_data = config_manager.load_config()

    # Fusion correcte au premier chargement.
    assert config_data["phyc"]["idcontact"] == "moi"
    assert config_data["chemins"]["dossier_grp"] == "D:/GRP"

    # Le fichier station a bien été créé, et config.json partagé nettoyé.
    chemin_station = dossier / "config_Y1612020001.json"
    assert chemin_station.exists()
    station_ecrite = json.loads(chemin_station.read_text(encoding="utf-8"))
    assert station_ecrite["chemins"]["dossier_grp"] == "D:/GRP"
    assert station_ecrite["parametrage"]["seuils_calage"] == [5.0]

    partage_ecrit = json.loads(chemin_json.read_text(encoding="utf-8"))
    assert "chemins" not in partage_ecrit
    assert "parametrage" not in partage_ecrit
    assert partage_ecrit["station_active"] == "Y1612020001"


def test_load_config_migration_idempotente(chemins):
    """Relire deux fois de suite un config.json d'ancien format ne doit pas planter
    ni écraser une seconde fois un fichier station déjà migré."""
    chemin_json, _chemin_exemple, _dossier = chemins
    chemin_json.write_text(json.dumps({
        "station": {"nom_station": "Moussoulens", "code_station": "Y1612020001"},
        "chemins": {"dossier_grp": "D:/GRP"},
    }), encoding="utf-8")
    config_manager.load_config()
    config_data_2 = config_manager.load_config()
    assert config_data_2["chemins"]["dossier_grp"] == "D:/GRP"


def test_load_config_migration_repetee_ne_perd_pas_une_cle_reapparue(chemins):
    """Reproduit un bug réel constaté (7 septembre 2026) : deux instances de l'outil
    ouvertes en même temps, l'une encore sur l'ANCIEN code (d'avant ce correctif)
    continuant à tout réécrire dans l'unique config.json entre deux migrations
    successives faites par la NOUVELLE instance. La 2e migration ne doit JAMAIS
    perdre une clé qui a réapparu dans config.json entre les deux, même si le
    fichier station existe déjà depuis la 1ère migration."""
    chemin_json, _chemin_exemple, dossier = chemins
    chemin_json.write_text(json.dumps({
        "station": {"nom_station": "Moussoulens", "code_station": "Y1612020001"},
        "chemins": {"dossier_grp": "D:/GRP"},
    }), encoding="utf-8")
    config_manager.load_config()  # 1ère migration : chemins -> fichier station

    # Un ANCIEN process (code pré-migration) réécrit l'unique config.json avec le
    # format complet d'avant, chemins INCLUS — comme si la migration n'avait jamais
    # eu lieu de son point de vue (il n'a jamais rechargé le fichier depuis son
    # propre démarrage), plus un nouveau champ "parametrage" saisi entretemps.
    chemin_json.write_text(json.dumps({
        "station": {"nom_station": "Moussoulens", "code_station": "Y1612020001"},
        "chemins": {"dossier_grp": "D:/GRP"},
        "parametrage": {"seuils_calage": [5.0]},
    }), encoding="utf-8")

    config_manager.load_config()  # 2e migration : ne doit PAS faire disparaître "parametrage"

    chemin_station = dossier / "config_Y1612020001.json"
    station_ecrite = json.loads(chemin_station.read_text(encoding="utf-8"))
    assert station_ecrite["chemins"]["dossier_grp"] == "D:/GRP"
    assert station_ecrite["parametrage"]["seuils_calage"] == [5.0]


def test_load_config_migre_chaque_station_de_affluents_par_station(chemins):
    """affluents_par_station (format du 27/08 au 7/09/2026) peut contenir PLUSIEURS
    stations — chacune doit recevoir sa propre clé "affluents", pas seulement la
    station active."""
    chemin_json, _chemin_exemple, dossier = chemins
    chemin_json.write_text(json.dumps({
        "station": {"nom_station": "Moussoulens", "code_station": "Y1612020001"},
        "affluents_par_station": {
            "Y1612020001": {"liste": [{"nom": "Affl A"}]},
            "Y1422030001": {"liste": [{"nom": "Affl B"}]},
        },
    }), encoding="utf-8")

    config_data = config_manager.load_config()
    assert config_data["affluents"]["liste"][0]["nom"] == "Affl A"

    chemin_autre_station = dossier / "config_Y1422030001.json"
    assert chemin_autre_station.exists()
    autre = json.loads(chemin_autre_station.read_text(encoding="utf-8"))
    assert autre["affluents"]["liste"][0]["nom"] == "Affl B"

    partage_ecrit = json.loads(chemin_json.read_text(encoding="utf-8"))
    assert "affluents_par_station" not in partage_ecrit


# -- save_config -----------------------------------------------------------------------

def test_save_config_repartit_vers_les_2_fichiers(chemins):
    chemin_json, _chemin_exemple, dossier = chemins
    config_data = {
        "phyc": {"idcontact": "moi"},
        "alertes": {"active": True},
        "station": {"code_station": "Y1612020001", "nom_station": "Moussoulens"},
        "chemins": {"dossier_grp": "D:/GRP"},
    }
    config_manager.save_config(config_data)

    partage = json.loads(chemin_json.read_text(encoding="utf-8"))
    assert partage["phyc"]["idcontact"] == "moi"
    assert partage["station_active"] == "Y1612020001"
    assert "station" not in partage
    assert "chemins" not in partage

    station = json.loads((dossier / "config_Y1612020001.json").read_text(encoding="utf-8"))
    assert station["station"]["nom_station"] == "Moussoulens"
    assert station["chemins"]["dossier_grp"] == "D:/GRP"


def test_save_config_sans_station_garde_tout_partage(chemins):
    """code_station vide (avant toute identification PHyC) : comportement historique,
    tout reste dans config.json, aucun fichier station créé."""
    chemin_json, _chemin_exemple, dossier = chemins
    config_data = {"phyc": {"idcontact": "moi"}, "station": {"code_station": ""},
                    "chemins": {"dossier_grp": "D:/GRP"}}
    config_manager.save_config(config_data)

    assert list(dossier.glob("config_*.json")) == []
    partage = json.loads(chemin_json.read_text(encoding="utf-8"))
    assert partage["chemins"]["dossier_grp"] == "D:/GRP"


def test_aller_retour_save_puis_load(chemins):
    config_data = {
        "phyc": {"idcontact": "moi"},
        "station": {"code_station": "Y1612020001", "nom_station": "Moussoulens"},
        "parametrage": {"seuils_calage": [5.0, 10.0]},
        "score": {"agregation": "moyenne"},
    }
    config_manager.save_config(config_data)
    relu = config_manager.load_config()
    assert relu["phyc"]["idcontact"] == "moi"
    assert relu["parametrage"]["seuils_calage"] == [5.0, 10.0]
    assert relu["score"]["agregation"] == "moyenne"


# -- basculer_vers_station ---------------------------------------------------------------

def test_basculer_vers_station_sauvegarde_lancienne_et_mute_en_place(chemins):
    """Vérifie explicitement la mutation EN PLACE (voir docstring) : la référence du
    sous-dict "parametrage" doit rester le MÊME objet Python après la bascule — un
    onglet qui l'aurait capturé par fermeture (ui/tab_parametrage.py) ne doit jamais
    se retrouver désynchronisé."""
    _chemin_json, _chemin_exemple, dossier = chemins
    config_data = {
        "station": {"code_station": "Y1612020001", "nom_station": "Moussoulens"},
        "parametrage": {"seuils_calage": [5.0]},
        "crues_selectionnees": ["2024-01-01T00:00:00"],
    }
    ref_parametrage = config_data["parametrage"]
    ref_crues = config_data["crues_selectionnees"]

    config_manager.basculer_vers_station(config_data, "Y1612020001", "Y1422030001",
                                           dossier=str(dossier))

    # Bascule vers une station jamais configurée : tout redevient vide (à part le
    # catalogue de "parametrage", pré-rempli depuis le gabarit — voir le test dédié
    # test_basculer_vers_station_pre_remplit_le_catalogue_dune_nouvelle_station ;
    # celui du fixture "chemins" est volontairement vide, donc sans effet visible
    # ici), mais toujours les MÊMES objets Python (mutation en place, pas remplacement).
    assert config_data["parametrage"] is ref_parametrage
    assert config_data["parametrage"].get("seuils_calage") is None
    assert config_data["crues_selectionnees"] is ref_crues
    assert config_data["crues_selectionnees"] == []

    # L'ancienne station a bien été sauvegardée avant le vidage.
    ancienne = json.loads((dossier / "config_Y1612020001.json").read_text(encoding="utf-8"))
    assert ancienne["parametrage"]["seuils_calage"] == [5.0]
    assert ancienne["crues_selectionnees"] == ["2024-01-01T00:00:00"]


def test_basculer_vers_station_recharge_une_station_deja_connue(chemins):
    _chemin_json, _chemin_exemple, dossier = chemins
    (dossier / "config_Y1422030001.json").write_text(json.dumps({
        "parametrage": {"seuils_calage": [1.0]},
        "chemins": {"dossier_grp": "D:/Trebes"},
    }), encoding="utf-8")
    config_data = {"station": {"code_station": "Y1612020001"},
                    "parametrage": {"seuils_calage": [5.0]}}
    ref_parametrage = config_data["parametrage"]

    config_manager.basculer_vers_station(config_data, "Y1612020001", "Y1422030001",
                                           dossier=str(dossier))

    assert config_data["parametrage"] is ref_parametrage
    assert config_data["parametrage"]["seuils_calage"] == [1.0]
    assert config_data["chemins"]["dossier_grp"] == "D:/Trebes"


def test_basculer_vers_station_meme_station_ne_fait_rien(chemins):
    _chemin_json, _chemin_exemple, dossier = chemins
    config_data = {"station": {"code_station": "Y1612020001"},
                    "parametrage": {"seuils_calage": [5.0]}}
    config_manager.basculer_vers_station(config_data, "Y1612020001", "Y1612020001",
                                           dossier=str(dossier))
    assert config_data["parametrage"]["seuils_calage"] == [5.0]
    assert list(dossier.glob("config_*.json")) == []


def test_basculer_vers_station_depuis_aucune_station_ne_sauvegarde_rien(chemins):
    """ancien_code_station vide (toute première identification) : rien à sauvegarder
    pour une "ancienne" station qui n'existait pas. Un fichier est tout de même créé
    pour la NOUVELLE station — voir _completer_parametrage_si_nouvelle_station,
    déclenché dès qu'un catalogue est à pré-remplir (sans effet concret ici, le
    gabarit du fixture "chemins" étant volontairement vide)."""
    _chemin_json, _chemin_exemple, dossier = chemins
    config_data = {"station": {"code_station": ""}, "parametrage": {}}
    config_manager.basculer_vers_station(config_data, "", "Y1612020001", dossier=str(dossier))
    fichiers = list(dossier.glob("config_*.json"))
    assert [f.name for f in fichiers] == ["config_Y1612020001.json"]


def test_basculer_vers_station_pre_remplit_le_catalogue_dune_nouvelle_station(tmp_path, monkeypatch):
    """Demandé explicitement (8 septembre 2026, incident Quillan) : une station JAMAIS
    configurée sur ce poste ne doit plus repartir avec un catalogue pas_de_temps/
    horizons_par_pdt totalement vide (ressaisie manuelle trop lourde, 8 pas de temps
    à définir un par un) — le catalogue du gabarit config.exemple.json sert de point
    de départ, modifiable ensuite comme avant. Une sélection déjà personnalisée
    (horizons_selectionnes, seuils_calage...) n'est en revanche jamais écrasée."""
    dossier = tmp_path / "config"
    dossier.mkdir()
    chemin_exemple = dossier / "config.exemple.json"
    chemin_exemple.write_text(json.dumps({
        "parametrage": {
            "pas_de_temps": [{"code": "00J00H15M", "libelle": "15 min"}],
            "horizons_par_pdt": {"00J00H15M": ["00J01H00M", "01J00H00M"]},
            "horizons_selectionnes": {}, "seuils_calage": [0.0],
            "methodes_selectionnees": ["T"], "decalages_pic_heures": [],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(app_config, "CONFIG_JSON_PATH", str(dossier / "config.json"))
    monkeypatch.setattr(app_config, "CONFIG_EXEMPLE_PATH", str(chemin_exemple))

    config_data = {"station": {"code_station": ""}}
    config_manager.basculer_vers_station(config_data, "", "Y111201001", dossier=str(dossier))

    assert config_data["parametrage"]["pas_de_temps"] == [{"code": "00J00H15M", "libelle": "15 min"}]
    assert config_data["parametrage"]["horizons_par_pdt"] == {"00J00H15M": ["00J01H00M", "01J00H00M"]}
    # Persisté immédiatement sur disque (pas seulement en mémoire) — contrairement à
    # load_config(), il n'y a ici aucun risque de conflit avec une migration.
    ecrit = json.loads((dossier / "config_Y111201001.json").read_text(encoding="utf-8"))
    assert ecrit["parametrage"]["pas_de_temps"] == [{"code": "00J00H15M", "libelle": "15 min"}]


def test_load_config_pre_remplit_le_catalogue_dune_nouvelle_station(tmp_path, monkeypatch):
    """Même vérification que ci-dessus, mais au chargement initial (load_config)
    plutôt qu'au changement de station en cours de session."""
    dossier = tmp_path / "config"
    dossier.mkdir()
    chemin_json = dossier / "config.json"
    chemin_exemple = dossier / "config.exemple.json"
    chemin_exemple.write_text(json.dumps({"parametrage": {
        "pas_de_temps": [{"code": "00J00H15M", "libelle": "15 min"}],
    }}), encoding="utf-8")
    chemin_json.write_text(json.dumps({"station_active": "Y111201001"}), encoding="utf-8")
    monkeypatch.setattr(app_config, "CONFIG_JSON_PATH", str(chemin_json))
    monkeypatch.setattr(app_config, "CONFIG_EXEMPLE_PATH", str(chemin_exemple))

    config_data = config_manager.load_config()
    assert config_data["parametrage"]["pas_de_temps"] == [{"code": "00J00H15M", "libelle": "15 min"}]
    # Non persisté par load_config() lui-même (voir sa docstring/commentaire) — reste
    # seulement en mémoire tant qu'aucune sauvegarde n'a eu lieu.
    assert not (dossier / "config_Y111201001.json").exists()


def test_completer_parametrage_ne_touche_pas_un_catalogue_deja_personnalise(tmp_path, monkeypatch):
    """Un catalogue déjà présent (même minimal) n'est JAMAIS écrasé par celui du
    gabarit — condition explicite de _completer_parametrage_si_nouvelle_station."""
    dossier = tmp_path / "config"
    dossier.mkdir()
    chemin_exemple = dossier / "config.exemple.json"
    chemin_exemple.write_text(json.dumps({"parametrage": {
        "pas_de_temps": [{"code": "00J00H15M", "libelle": "15 min"}],
    }}), encoding="utf-8")
    monkeypatch.setattr(app_config, "CONFIG_JSON_PATH", str(dossier / "config.json"))
    monkeypatch.setattr(app_config, "CONFIG_EXEMPLE_PATH", str(chemin_exemple))

    config_station = {"parametrage": {"pas_de_temps": [{"code": "01J00H00M", "libelle": "1 j (perso)"}]}}
    a_complete = config_manager._completer_parametrage_si_nouvelle_station(config_station)
    assert a_complete is False
    assert config_station["parametrage"]["pas_de_temps"] == [{"code": "01J00H00M", "libelle": "1 j (perso)"}]
