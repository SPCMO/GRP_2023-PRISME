# -*- coding: utf-8 -*-
"""Constantes globales de GRP_2023-PRISME (chemins internes, proxy réseau)."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_JSON_PATH = os.path.join(BASE_DIR, "config", "config.json")
CONFIG_EXEMPLE_PATH = os.path.join(BASE_DIR, "config", "config.exemple.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(DATA_DIR, "runs.sqlite3")

# Pointeur optionnel vers un dossier de stockage des bases EXTERNE au dossier
# d'installation de l'outil (voir modules.results_store.dossier_data_effectif,
# ui.tab_config bandeau "Dossier de stockage des bases") — corrige un incident réel :
# data/ est gitignoré (trop volumineux/spécifique au poste), donc toute réinstallation
# de l'outil dans un NOUVEAU dossier (mise à jour faite en clonant à côté plutôt qu'en
# place) repart avec une base vierge, sans que l'ancienne ne soit ni retrouvée ni
# signalée comme manquante — l'utilisateur croit alors avoir perdu ses résultats de
# campagne alors qu'ils sont juste restés dans l'ancien dossier. Ce pointeur vit dans
# %APPDATA%, donc HORS de tout dossier d'installation de l'outil : il survit ainsi à
# n'importe quel nouveau clone/nouvelle copie, contrairement à data/ lui-même.
DOSSIER_CONFIG_UTILISATEUR = os.path.join(
    os.environ.get("APPDATA") or BASE_DIR, "GRP_2023-PRISME")
FICHIER_POINTEUR_DATA = os.path.join(DOSSIER_CONFIG_UTILISATEUR, "data_emplacement.txt")

# Proxy sortant obligatoire sur le réseau SPCMO/RIE pour toute connexion internet (pip,
# git push, PHyC) : contrairement au navigateur, ces outils ne le détectent pas
# automatiquement. En dur ici (même valeur que GMAO/config.py et OPALE v2) pour que ça
# fonctionne aussi sur le poste des collègues sans configuration préalable. Une variable
# d'environnement HTTPS_PROXY, si définie, reste prioritaire (voir modules/proxy_utils.py).
PROXY_RIE = "http://pfrie-std.proxy.e2.rie.gouv.fr:8080"
