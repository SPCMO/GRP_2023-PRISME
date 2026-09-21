# -*- coding: utf-8 -*-
"""Constantes globales de GRP_2023-PRISME (chemins internes, proxy réseau)."""

import os

# Version affichée dans le titre de la fenêtre (main.py::TITRE_BASE) et Aide.html —
# demandé par l'utilisateur pour repérer d'un coup d'œil la version en cours d'usage
# sur un poste donné (ex. distinguer un collègue resté sur une version plus ancienne).
# Format MAJEUR.MINEUR (pas de patch) : MAJEUR incrémenté à un changement structurel de
# l'outil (ex. v2.0 = isolation des bases de résultats par station, 26 août 2026 ; v3.0
# = recalcul automatique du dQP/dTP + score composite en médiane, 1er septembre 2026 —
# des changements qui redéfinissent le fonctionnement/la fiabilité de tout l'outil, pas
# juste un onglet) ; MINEUR incrémenté à chaque lot de déploiement suivant le dernier
# changement majeur (un jour peut donc contenir plusieurs incréments MINEUR — voir
# Aide.html > Architecture > "Numéro de version" pour l'historique détaillé ; cette
# version n'est qu'un repère macro, pas un décompte exhaustif du changelog). À
# incrémenter manuellement ici lors du prochain déploiement significatif — jamais
# recalculé automatiquement.
VERSION = "3.22"

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

# Chemin réseau du manifeste de version déployée (VERSION.json, publié à côté de
# Aide.html à chaque déploiement — voir modules/verif_version.py et
# main.py::App._verifier_version_disponible_en_arriere_plan). MÊME dossier réseau que
# la copie de déploiement de l'outil (voir la commande PowerShell du skill
# deploy-grp2023-prisme) : ce fichier y est copié automatiquement comme tout autre
# fichier suivi par git, aucune étape de déploiement séparée à retenir. Vérification
# purement informative et non bloquante — un chemin réseau inaccessible ne doit jamais
# empêcher l'outil de démarrer (voir modules/verif_version.py, jamais d'exception).
CHEMIN_VERSION_JSON_RESEAU = (
    r"\\oc-spc-11.dreal-oc.ad.e2.rie.gouv.fr\SUIVI\Pole_prevision"
    r"\40_Modèles Opérationnels\40_Modeles_GRP\02_calage\scripts"
    r"\GRP_2023-PRISME\VERSION.json"
)
