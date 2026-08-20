# -*- coding: utf-8 -*-
"""Chargement/sauvegarde de la configuration JSON de l'outil (config/config.json).

Pattern repris d'OPALE v2/modules/config_manager.py. Si config.json n'existe pas encore
(premier lancement), on part de config.exemple.json plutôt que d'échouer — l'utilisateur
complète ensuite les champs vides via l'interface (onglet Configuration).
"""

import json
import os
import shutil

import config as app_config


def load_config(path=None):
    """Charge config/config.json — le crée depuis config.exemple.json s'il est absent."""
    path = path or app_config.CONFIG_JSON_PATH
    if not os.path.isfile(path):
        if not os.path.isfile(app_config.CONFIG_EXEMPLE_PATH):
            raise FileNotFoundError(
                f"Ni {path} ni {app_config.CONFIG_EXEMPLE_PATH} n'existent — "
                "installation incomplète de l'outil."
            )
        shutil.copyfile(app_config.CONFIG_EXEMPLE_PATH, path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_config(config_data, path=None):
    """Sauvegarde config_data dans config/config.json (créé si besoin)."""
    path = path or app_config.CONFIG_JSON_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config_data, fh, indent=2, ensure_ascii=False)
