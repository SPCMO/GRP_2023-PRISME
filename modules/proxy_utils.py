# -*- coding: utf-8 -*-
"""Détection du proxy réseau SPCMO/RIE, pour un éventuel appel HTTP sortant vers
l'internet public effectué directement par l'outil (aucun cas d'usage actuel — voir
remarque ci-dessous).

⚠️ **PAS utilisé par modules.phyc_client** : PHyC (services.schapi.e2.rie.gouv.fr) est un
service INTERNE au RIE, joignable en direct sans proxy sur un poste déjà raccordé au
réseau SPCMO — passer par le proxy sortant RIE casse au contraire la connexion (celui-ci
n'a pas de route vers ce nom interne, l'appel échoue par un 502 "notresolvable"). Constaté
en pratique : forcer ce proxy dans phyc_client.py provoquait l'échec alors qu'OPALE v2
(sans aucune gestion de proxy) fonctionnait au même instant, sur le même poste — voir le
correctif dans phyc_client.py. Ce module reste disponible pour un futur appel HTTP vers
un site public (par analogie avec pip/git, qui ont besoin du proxy sortant pour ça — voir
Test_pr_install.py::detecter_proxy, même logique), pas pour les services RIE-internes.
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
