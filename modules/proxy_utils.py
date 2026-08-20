# -*- coding: utf-8 -*-
"""Détection du proxy réseau SPCMO/RIE pour les appels HTTP effectués directement par
l'outil (PHyC — voir modules.phyc_client).

pip/git ont leur propre détection de proxy (voir Test_pr_install.py::detecter_proxy,
même logique), mais les appels `requests`/`zeep` internes à l'application ne lisent PAS
automatiquement le proxy système Windows configuré via les Options Internet — seules les
variables d'environnement HTTP_PROXY/HTTPS_PROXY sont lues nativement par `requests`. Sur
le réseau SPCMO/RIE, où le proxy est réglé au niveau système (WinINET) et pas via ces
variables d'environnement, un appel PHyC sans proxy explicite échoue typiquement par une
erreur de résolution DNS (le nom de service PHyC n'est joignable qu'via le proxy).
"""

import os
import urllib.request

import config as app_config


def detecter_proxy():
    """Retourne l'URL du proxy à utiliser, ou None si aucun ne semble nécessaire.

    Priorité : variable d'environnement HTTPS_PROXY/HTTP_PROXY (si l'utilisateur en a
    défini une, elle prime) > proxy système détecté (registre Windows via urllib,
    fonctionne même sans variable d'environnement) > proxy RIE connu par défaut
    (config.PROXY_RIE, dernier recours pour que ça fonctionne même sans détection).
    """
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    try:
        proxies = urllib.request.getproxies()
        for key in ("https", "http"):
            if key in proxies and proxies[key]:
                return proxies[key]
    except Exception:
        pass
    return getattr(app_config, "PROXY_RIE", None) or None


def dict_proxies():
    """Retourne {"http": ..., "https": ...} prêt à passer à requests/zeep (paramètre
    `proxies=` ou `Session.proxies`), ou un dict vide si aucun proxy à utiliser."""
    proxy = detecter_proxy()
    return {"http": proxy, "https": proxy} if proxy else {}
