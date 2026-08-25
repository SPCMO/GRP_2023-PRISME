# -*- coding: utf-8 -*-
"""Chargement/sauvegarde de la configuration JSON de l'outil (config/config.json).

Pattern repris d'OPALE v2/modules/config_manager.py. Si config.json n'existe pas encore
(premier lancement), on part de config.exemple.json plutôt que d'échouer — l'utilisateur
complète ensuite les champs vides via l'interface (onglet Configuration).
"""

import json
import os
import shutil
import tempfile

import config as app_config


def load_config(path=None):
    """Charge config/config.json — le crée depuis config.exemple.json s'il est absent.

    Lève ValueError (message explicite) si le fichier existe mais n'est pas du JSON
    valide, plutôt que de laisser remonter un json.JSONDecodeError brut — un config.json
    corrompu (coupure pendant l'écriture, édition manuelle malheureuse) ne doit pas
    planter l'outil sans explication au démarrage."""
    path = path or app_config.CONFIG_JSON_PATH
    if not os.path.isfile(path):
        if not os.path.isfile(app_config.CONFIG_EXEMPLE_PATH):
            raise FileNotFoundError(
                f"Ni {path} ni {app_config.CONFIG_EXEMPLE_PATH} n'existent — "
                "installation incomplète de l'outil."
            )
        shutil.copyfile(app_config.CONFIG_EXEMPLE_PATH, path)
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Le fichier de configuration {path} est corrompu (JSON invalide) : {e}\n"
                "Restaurez une sauvegarde de ce fichier, ou repartez de "
                f"{app_config.CONFIG_EXEMPLE_PATH} en le renseignant à nouveau."
            ) from e


def save_config(config_data, path=None):
    """Sauvegarde config_data dans config/config.json (créé si besoin).

    Écriture atomique : le JSON est d'abord écrit dans un fichier temporaire du même
    dossier, puis os.replace() bascule vers le fichier final en une seule opération
    système — un crash pendant l'écriture ne peut plus laisser config.json à moitié
    écrit/corrompu (le fichier existant reste intact tant que le remplacement n'a pas
    réussi intégralement)."""
    path = path or app_config.CONFIG_JSON_PATH
    dossier = os.path.dirname(path)
    os.makedirs(dossier, exist_ok=True)
    fd, chemin_tmp = tempfile.mkstemp(dir=dossier, prefix=".config_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config_data, fh, indent=2, ensure_ascii=False)
        os.replace(chemin_tmp, path)
    except Exception:
        if os.path.exists(chemin_tmp):
            os.remove(chemin_tmp)
        raise
