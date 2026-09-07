# -*- coding: utf-8 -*-
"""Chargement/sauvegarde de la configuration JSON de l'outil.

Deux fichiers depuis le 7 septembre 2026 (demande explicite de l'utilisateur — "il
faut que le fichier config.json enregistre l'intégralité des champs... pour pouvoir
récupérer chaque paramétrage de chaque calage", suite à un usage multi-stations où
rebasculer sur une station écrasait silencieusement le paramétrage complet d'une
autre) :

  - `config/config.json` — PARTAGÉ entre toutes les stations : identifiants PHyC
    (`phyc`), réglage d'alerte ntfy (`alertes`), et `station_active` (le code_station
    actuellement sélectionné, pour savoir quel fichier station charger).
  - `config/config_<code_station>.json` — PROPRE à chaque station : identification
    (`station`), seuils de vigilance PHyC (`seuils_q`), dossiers de travail
    (`chemins`), paramétrage de campagne complet (`parametrage`), crues sélectionnées
    (`crues_selectionnees`, `crues_inclure_pluie`), pondération du score (`score`), et
    configuration des affluents (`affluents`, ex-`affluents_par_station[code_station]`
    — plus besoin d'indexer par station, le fichier l'est déjà). Voir CLES_PAR_STATION.

`load_config()` fusionne transparemment les deux fichiers en UN SEUL dict, pour que le
reste de l'outil (ui/tab_*.py) continue à lire/écrire `app.config_data` exactement
comme avant ce correctif — AUCUN changement requis là où la config est simplement
lue/modifiée en place. `save_config()` répartit chaque clé vers le bon fichier au
moment d'écrire, résolue depuis `config_data["station"]["code_station"]`.

Le VRAI changement de station en cours de session (l'utilisateur identifie une
NOUVELLE station via PHyC) nécessite en revanche un geste explicite — voir
`basculer_vers_station()` ci-dessous, appelée par ui/tab_config.py AVANT d'écraser
`config_data["station"]`, pour ne jamais mélanger les deux stations dans un seul
fichier ni perdre le paramétrage de celle qu'on quitte.

Tant que `code_station` est vide (avant toute identification PHyC), le comportement
reste celui d'avant ce correctif : tout dans `config.json` — comme la station n'est
pas encore identifiée, il n'y a de toute façon rien à isoler.
"""

import json
import os
import shutil
import tempfile

import config as app_config

# Clés dont la valeur est PROPRE à chaque station — voir docstring du module. Tout ce
# qui n'est pas dans cette liste (phyc, alertes, station_active) reste dans
# config.json, partagé par toutes les stations.
CLES_PAR_STATION = (
    "station", "seuils_q", "chemins", "parametrage",
    "crues_selectionnees", "crues_inclure_pluie", "score", "affluents",
)


def _chemin_config_station(code_station, dossier):
    return os.path.join(dossier, f"config_{code_station}.json")


def _lire_json(path):
    """Lit un fichier JSON, {} s'il n'existe pas — jamais une erreur bloquante pour un
    fichier optionnel (station pas encore identifiée sur ce poste, ou jamais
    configurée). Lève ValueError si le fichier existe mais est corrompu, avec un
    message explicite plutôt qu'un json.JSONDecodeError brut."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Le fichier de configuration {path} est corrompu (JSON invalide) : {e}\n"
                "Restaurez une sauvegarde de ce fichier, ou supprimez-le pour repartir "
                "d'une configuration vierge pour cette station (ou ce réglage partagé)."
            ) from e


def _ecrire_json_atomique(data, path):
    """Écriture atomique : le JSON est d'abord écrit dans un fichier temporaire du
    même dossier, puis os.replace() bascule vers le fichier final en une seule
    opération système — un crash pendant l'écriture ne peut plus laisser le fichier à
    moitié écrit/corrompu (le fichier existant reste intact tant que le remplacement
    n'a pas réussi intégralement)."""
    dossier = os.path.dirname(path)
    os.makedirs(dossier, exist_ok=True)
    fd, chemin_tmp = tempfile.mkstemp(dir=dossier, prefix=".config_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(chemin_tmp, path)
    except Exception:
        if os.path.exists(chemin_tmp):
            os.remove(chemin_tmp)
        raise


def load_config(path=None):
    """Charge la configuration complète (fusion de config.json et du
    config_<code_station>.json de la station active) — le crée depuis
    config.exemple.json s'il est absent (premier lancement).

    Migration automatique et unique : un config.json d'AVANT ce correctif a encore ses
    clés "par station" mélangées avec les clés partagées — la toute première fois
    qu'il est relu, elles sont extraites vers le fichier de la station actuellement
    identifiée (si une l'est), sans rien perdre ni demander quoi que ce soit à
    l'utilisateur. Sans effet si déjà migré (les clés par-station sont alors déjà
    absentes de config.json) ou si aucune station n'a jamais été identifiée sur ce
    poste (rien à isoler).

    Lève ValueError (message explicite) si un des fichiers existe mais n'est pas du
    JSON valide, plutôt que de laisser remonter un json.JSONDecodeError brut."""
    path = path or app_config.CONFIG_JSON_PATH
    dossier = os.path.dirname(path)
    if not os.path.isfile(path):
        if not os.path.isfile(app_config.CONFIG_EXEMPLE_PATH):
            raise FileNotFoundError(
                f"Ni {path} ni {app_config.CONFIG_EXEMPLE_PATH} n'existent — "
                "installation incomplète de l'outil."
            )
        shutil.copyfile(app_config.CONFIG_EXEMPLE_PATH, path)

    config_partage = _lire_json(path)

    # Migration depuis affluents_par_station (format introduit le 27/08/2026, avant le
    # config par station de ce module) : CHAQUE station qui y avait une entrée reçoit
    # sa propre clé "affluents" dans SON fichier config_<code_station>.json — pas
    # seulement la station actuellement active, pour ne perdre la configuration
    # d'affluents d'aucune station déjà étudiée sur ce poste (voir
    # ui/tab_analyse_affluents.py, qui lit désormais directement "affluents").
    affluents_par_station_ancien = config_partage.pop("affluents_par_station", None)
    if affluents_par_station_ancien:
        for code, cfg_affluents in affluents_par_station_ancien.items():
            chemin_station = _chemin_config_station(code, dossier)
            config_station_existante = _lire_json(chemin_station)
            if "affluents" not in config_station_existante:
                config_station_existante["affluents"] = cfg_affluents
                _ecrire_json_atomique(config_station_existante, chemin_station)
        _ecrire_json_atomique(config_partage, path)

    code_station_ancien_format = ((config_partage.get("station") or {}).get("code_station") or "").strip()
    cles_a_migrer = [cle for cle in CLES_PAR_STATION if cle in config_partage]
    if code_station_ancien_format and cles_a_migrer:
        chemin_station = _chemin_config_station(code_station_ancien_format, dossier)
        config_station_existante = _lire_json(chemin_station)
        # Fusionne SANS écraser une clé déjà migrée (le fichier station, une fois créé,
        # fait foi pour cette station) — mais ne DROP jamais une clé encore présente
        # dans config.json même si le fichier existe déjà : bug réel constaté (deux
        # process de l'outil ouverts en même temps, l'un avec le code d'avant ce
        # correctif continuant à tout réécrire dans l'unique config.json entre deux
        # migrations successives — la 2e migration effaçait alors silencieusement ce
        # que ce vieux process venait de réécrire, sans jamais le sauvegarder nulle
        # part). Idempotent : une migration déjà faite, rejouée sans rien de nouveau à
        # migrer, ne touche à aucun des deux fichiers.
        a_ecrire = False
        for cle in cles_a_migrer:
            if cle not in config_station_existante:
                config_station_existante[cle] = config_partage[cle]
                a_ecrire = True
        if a_ecrire:
            _ecrire_json_atomique(config_station_existante, chemin_station)
        for cle in CLES_PAR_STATION:
            config_partage.pop(cle, None)
        config_partage["station_active"] = code_station_ancien_format
        _ecrire_json_atomique(config_partage, path)

    code_station = (config_partage.get("station_active") or "").strip()
    config_station = {}
    if code_station:
        config_station = _lire_json(_chemin_config_station(code_station, dossier))

    return {**config_partage, **config_station}


def save_config(config_data, path=None):
    """Sauvegarde config_data — répartit chaque clé vers config.json (partagé) ou
    config_<code_station>.json (station active, résolue depuis
    config_data["station"]["code_station"]) selon CLES_PAR_STATION. Tant que
    code_station est vide, les clés par-station restent dans config.json — comme
    avant ce correctif, rien à isoler tant qu'aucune station n'existe."""
    path = path or app_config.CONFIG_JSON_PATH
    dossier = os.path.dirname(path)
    code_station = ((config_data.get("station") or {}).get("code_station") or "").strip()

    config_partage = {cle: val for cle, val in config_data.items() if cle not in CLES_PAR_STATION}
    if code_station:
        config_partage["station_active"] = code_station
        config_station = {cle: val for cle, val in config_data.items() if cle in CLES_PAR_STATION}
        _ecrire_json_atomique(config_station, _chemin_config_station(code_station, dossier))
    else:
        config_partage.update({cle: val for cle, val in config_data.items() if cle in CLES_PAR_STATION})

    _ecrire_json_atomique(config_partage, path)


def basculer_vers_station(config_data, ancien_code_station, nouveau_code_station, dossier=None):
    """Change la station active EN PLACE dans config_data (dict MUTÉ, jamais
    remplacé — pour que app.config_data, référencé tel quel par tous les onglets déjà
    construits, reste synchronisé sans réassignation) : sauvegarde d'abord l'état
    par-station courant sous ANCIEN_code_station (s'il était renseigné, pour ne jamais
    le perdre), puis charge (ou initialise vide, si cette station n'a encore jamais
    été configurée sur ce poste) les clés par-station pour NOUVEAU_code_station.

    À appeler AVANT toute modification de config_data["station"] — au moment de
    l'appel, ce sont encore les valeurs de l'ANCIENNE station qui doivent être
    sauvegardées (voir ui/tab_config.py::_identifier). Le flux normal qui suit
    (assignation du nouveau code_station puis app.persist_config()) se charge ensuite
    d'écrire station_active correctement dans config.json via save_config() ci-dessus
    — cette fonction n'a donc PAS besoin d'y toucher elle-même.

    Sans effet si ancien_code_station == nouveau_code_station (rien à basculer, ex.
    ré-identification de la même station).

    ⚠️ Mute chaque sous-dict/liste de premier niveau EN PLACE (clear()+update(), ou
    slice-assignment pour une liste) plutôt que de remplacer sa référence dans
    config_data — plusieurs onglets (ui/tab_parametrage.py notamment) capturent une
    référence DIRECTE au sous-dict au moment de leur construction (ex. `parametrage =
    app.config_data.setdefault("parametrage", {})`) : un simple `config_data[cle] =
    nouvelle_valeur` désynchroniserait silencieusement ces fermetures de l'état
    réellement actif, qui continueraient à lire/modifier l'ANCIEN objet."""
    if ancien_code_station == nouveau_code_station:
        return
    dossier = dossier or os.path.dirname(app_config.CONFIG_JSON_PATH)

    if ancien_code_station:
        etat_ancien = {cle: config_data[cle] for cle in CLES_PAR_STATION if cle in config_data}
        _ecrire_json_atomique(etat_ancien, _chemin_config_station(ancien_code_station, dossier))

    nouvel_etat = (_lire_json(_chemin_config_station(nouveau_code_station, dossier))
                   if nouveau_code_station else {})

    for cle in CLES_PAR_STATION:
        valeur_actuelle = config_data.get(cle)
        valeur_cible = nouvel_etat.get(cle)
        if isinstance(valeur_actuelle, dict):
            valeur_actuelle.clear()
            if isinstance(valeur_cible, dict):
                valeur_actuelle.update(valeur_cible)
        elif isinstance(valeur_actuelle, list):
            valeur_actuelle[:] = valeur_cible if isinstance(valeur_cible, list) else []
        elif cle in config_data or cle in nouvel_etat:
            config_data[cle] = valeur_cible
