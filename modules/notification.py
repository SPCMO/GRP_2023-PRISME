# -*- coding: utf-8 -*-
"""Alerte de fin de campagne par notification push (ntfy.sh) — demandé explicitement
par l'utilisateur (2 septembre 2026) : être prévenu sur son téléphone qu'une campagne
de calage (souvent longue, lancée sans surveillance) est terminée, sans avoir à
rouvrir l'outil pour le savoir.

Canal retenu après comparatif mené dans une session dédiée (dossier hors-dépôt
« Alerte SMS/ », `notify.py` y sert de prototype/référence — jamais importé ici, ce
module en reprend uniquement la logique ntfy pour ne pas coupler PRISME à un dossier
externe) :
  - ntfy.sh — gratuit, sans compte, indépendant de l'opérateur mobile (contrainte
    forte de l'utilisateur : les portables d'astreinte peuvent changer d'opérateur
    sans que ça ne casse l'alerte) ;
  - le proxy RIE laisse passer ntfy.sh en HTTPS (HTTP 200) sans démarche DISI —
    vérifié en conditions réelles depuis un poste SPCMO, voir modules/proxy_utils.py
    pour la détection du proxy elle-même (à réutiliser ici via dict_proxies()).

Ce module ne dépend JAMAIS de ui/ (voir CLAUDE.md) : toutes les fonctions sont pures
et testables sans Tkinter (tests/test_notification.py, requests mocké). L'appel
réseau est volontairement best-effort côté appelant (ui/tab_orchestration.py,
ui/tab_config.py) — un échec d'envoi ne doit jamais faire échouer/planter une
campagne de calage, seulement être loggué et signalé discrètement.
"""

import logging
import re
import secrets
import unicodedata

import requests

logger = logging.getLogger("grp_2023.notification")

SERVEUR_NTFY_PAR_DEFAUT = "https://ntfy.sh"

# Priorités acceptées par l'API ntfy — voir https://docs.ntfy.sh/publish/#message-
# priority. "default" et "high" sont les 2 seules utilisées par PRISME (fin normale /
# fin en échec), mais les 5 sont acceptées pour rester ouvert à un réglage plus fin
# plus tard (config.json). En publication JSON (voir envoyer_alerte_ntfy), l'API
# attend un ENTIER 1-5, pas ces alias texte — table de correspondance ci-dessous.
PRIORITES_NTFY = ("min", "low", "default", "high", "urgent")
_PRIORITES_NTFY_VERS_ENTIER = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


class NotificationError(Exception):
    """Échec d'envoi d'une alerte ntfy — réseau, proxy, ou réponse HTTP en erreur, ou
    paramètres invalides (serveur/topic manquant, priorité inconnue). Toujours
    best-effort côté appelant : ne doit jamais remonter jusqu'à faire échouer une
    campagne de calage (voir ui/tab_orchestration.py::_envoyer_alerte_fin_campagne)."""


def generer_topic_ntfy(nom_ou_code_station):
    """Génère un sujet (topic) ntfy propre à UNE installation PRISME, au format
    "prisme-<slug station>-<8 hex aléatoires>" — demandé explicitement : l'utilisateur
    ne veut pas mélanger ses alertes avec celles d'une autre installation PRISME (un
    autre SPC, un autre bassin) utilisant elle aussi ntfy.sh.

    Le sujet fait office de MOT DE PASSE côté ntfy (quiconque le connaît peut publier
    ou lire dessus, voir https://docs.ntfy.sh/publish/#authentication — ntfy.sh public
    n'a pas d'authentification par défaut) : les 8 caractères hexadécimaux sont générés
    par `secrets.token_hex` (générateur cryptographiquement sûr, pas `random`) pour
    rester non devinables même si le préfixe (nom de station, potentiellement public)
    est connu. Le nom de station est translittéré et réduit à [a-z0-9-] : ntfy
    n'accepte que `[-_A-Za-z0-9]{1,64}` comme sujet."""
    brut = (nom_ou_code_station or "").strip()
    normalise = unicodedata.normalize("NFKD", brut).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalise).strip("-").lower()
    prefixe = f"prisme-{slug}-" if slug else "prisme-"
    return prefixe + secrets.token_hex(4)


def envoyer_alerte_ntfy(serveur, topic, titre, message, priorite="default",
                         proxies=None, tags=None, timeout=10):
    """Envoie une notification push via un serveur ntfy (ntfy.sh par défaut, ou une
    instance auto-hébergée compatible).

    Publication en JSON (`POST <serveur>/` avec {"topic", "title", "message", ...}
    dans le CORPS de la requête, voir https://docs.ntfy.sh/publish/#publish-as-json)
    plutôt qu'en en-têtes HTTP (Title/Priority/Tags) comme le fait le prototype
    `Alerte SMS/notify.py` — corrige un bug réel constaté en conditions réelles : un
    titre contenant un caractère hors Latin-1 (ex. le tiret cadratin "—", très courant
    dans les titres générés par PRISME) fait lever une UnicodeEncodeError par la
    bibliothèque HTTP sous-jacente (les en-têtes HTTP s'encodent par défaut en
    Latin-1, PAS en UTF-8) — silencieusement, cette exception n'étant pas une
    requests.RequestException, elle n'était catchée nulle part et laissait
    l'utilisateur sans aucun message, ni succès ni échec. Le corps JSON, lui, encode
    nativement en UTF-8 (voir `requests.post(..., json=...)`) : plus aucune
    restriction de caractères sur titre/message/tags.

    Lève NotificationError si les paramètres sont invalides ou si l'envoi échoue
    (réseau, proxy, réponse HTTP non 2xx) — TOUJOURS à l'appelant de décider du
    traitement best-effort (voir docstring du module), cette fonction elle-même ne
    logge et n'avale rien silencieusement.

    `proxies` : dict {"http": ..., "https": ...} tel que retourné par
    modules.proxy_utils.dict_proxies() — à passer explicitement (jamais résolu ici,
    ce module ne dépend d'aucun autre module PRISME pour rester testable seul)."""
    if not serveur or not topic:
        raise NotificationError("Serveur ou sujet (topic) ntfy manquant.")
    if priorite not in PRIORITES_NTFY:
        raise NotificationError(
            f"Priorité ntfy inconnue : {priorite!r} (attendu parmi {PRIORITES_NTFY}).")
    url = f"{serveur.rstrip('/')}/"
    payload = {"topic": topic, "title": titre, "message": message,
               "priority": _PRIORITES_NTFY_VERS_ENTIER[priorite]}
    if tags:
        payload["tags"] = list(tags)
    try:
        reponse = requests.post(url, json=payload, proxies=proxies, timeout=timeout)
        reponse.raise_for_status()
    except requests.RequestException as e:
        # Le topic ne doit JAMAIS apparaître dans un message d'erreur potentiellement
        # affiché/loggué largement (il fait office de mot de passe, voir
        # generer_topic_ntfy) — seul le nom du serveur est mentionné ici.
        raise NotificationError(f"Échec d'envoi de la notification vers {serveur} : {e}") from e
    return reponse
