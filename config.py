# -*- coding: utf-8 -*-
"""Constantes globales de l'outil GRP_2023 (chemins internes, proxy réseau)."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_JSON_PATH = os.path.join(BASE_DIR, "config", "config.json")
CONFIG_EXEMPLE_PATH = os.path.join(BASE_DIR, "config", "config.exemple.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(DATA_DIR, "runs.sqlite3")

# Proxy sortant obligatoire sur le réseau SPCMO/RIE pour toute connexion internet (pip,
# git push, PHyC) : contrairement au navigateur, ces outils ne le détectent pas
# automatiquement. En dur ici (même valeur que GMAO/config.py et OPALE v2) pour que ça
# fonctionne aussi sur le poste des collègues sans configuration préalable. Une variable
# d'environnement HTTPS_PROXY, si définie, reste prioritaire (voir modules/proxy_utils.py).
PROXY_RIE = "http://pfrie-std.proxy.e2.rie.gouv.fr:8080"
