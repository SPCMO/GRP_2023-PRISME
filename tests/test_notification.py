# -*- coding: utf-8 -*-
"""Tests unitaires — modules/notification.py (alerte ntfy de fin de campagne, voir
onglet Configuration et ui/tab_orchestration.py::_envoyer_alerte_fin_campagne).

Fonction PURE, sans dépendance à Tkinter ni réseau réel — `requests.post` est
monkeypatché à chaque test (jamais un vrai appel HTTP ici), même principe que les
autres frontières externes mockées dans tests/test_run_orchestrator.py."""

import pytest
import requests

from modules import notification


class _ReponseFactice:
    """Simule un requests.Response minimal — status_code + raise_for_status()."""

    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


# -- generer_topic_ntfy -------------------------------------------------------------

def test_generer_topic_ntfy_format_attendu():
    topic = notification.generer_topic_ntfy("Moussoulens")
    assert topic.startswith("prisme-moussoulens-")
    suffixe = topic.rsplit("-", 1)[-1]
    assert len(suffixe) == 8
    assert all(c in "0123456789abcdef" for c in suffixe)


def test_generer_topic_ntfy_deux_appels_donnent_des_sujets_differents():
    # Les 8 caractères hexadécimaux (secrets.token_hex, cryptographiquement sûrs)
    # doivent varier à chaque appel — sinon le sujet redeviendrait devinable.
    assert notification.generer_topic_ntfy("Moussoulens") != notification.generer_topic_ntfy("Moussoulens")


def test_generer_topic_ntfy_translittere_les_accents_et_espaces():
    topic = notification.generer_topic_ntfy("Le Fresquel à Pezens")
    slug = topic.rsplit("-", 1)[0]  # tout sauf le suffixe hex
    assert slug == "prisme-le-fresquel-a-pezens"
    # Sujet ntfy : uniquement [-_A-Za-z0-9] (voir docstring) — aucun caractère
    # accentué ou espace ne doit fuiter.
    assert all(c.isalnum() or c == "-" for c in topic)


def test_generer_topic_ntfy_station_vide_retourne_quand_meme_un_sujet_exploitable():
    topic = notification.generer_topic_ntfy("")
    assert topic.startswith("prisme-")
    assert len(topic) == len("prisme-") + 8


# -- envoyer_alerte_ntfy --------------------------------------------------------------

def test_envoyer_alerte_ntfy_appelle_requests_post_avec_les_bons_parametres(monkeypatch):
    appels = []

    def _post_factice(url, json=None, proxies=None, timeout=None):
        appels.append({"url": url, "json": json, "proxies": proxies, "timeout": timeout})
        return _ReponseFactice(200)

    monkeypatch.setattr(notification.requests, "post", _post_factice)
    # Titre avec tiret cadratin (—, U+2014) — caractère qui plantait en mode
    # en-têtes HTTP (UnicodeEncodeError, Latin-1) avant le passage au mode JSON.
    notification.envoyer_alerte_ntfy(
        "https://ntfy.sh", "mon-topic", titre="PRISME — Titre é à ç",
        message="Message accentué", priorite="high",
        proxies={"https": "http://proxy:8080"}, tags=["warning"])

    assert len(appels) == 1
    appel = appels[0]
    assert appel["url"] == "https://ntfy.sh/"
    assert appel["json"]["topic"] == "mon-topic"
    assert appel["json"]["title"] == "PRISME — Titre é à ç"
    assert appel["json"]["message"] == "Message accentué"
    assert appel["json"]["priority"] == 4  # "high" -> 4, voir _PRIORITES_NTFY_VERS_ENTIER
    assert appel["json"]["tags"] == ["warning"]
    assert appel["proxies"] == {"https": "http://proxy:8080"}


def test_envoyer_alerte_ntfy_retire_le_slash_final_du_serveur(monkeypatch):
    appels = []
    monkeypatch.setattr(notification.requests, "post",
                         lambda url, **k: appels.append(url) or _ReponseFactice(200))
    notification.envoyer_alerte_ntfy("https://ntfy.sh/", "topic", "T", "M")
    assert appels[0] == "https://ntfy.sh/"


def test_envoyer_alerte_ntfy_sans_serveur_ou_topic_leve_erreur():
    with pytest.raises(notification.NotificationError):
        notification.envoyer_alerte_ntfy("", "topic", "T", "M")
    with pytest.raises(notification.NotificationError):
        notification.envoyer_alerte_ntfy("https://ntfy.sh", "", "T", "M")


def test_envoyer_alerte_ntfy_priorite_invalide_leve_erreur():
    with pytest.raises(notification.NotificationError):
        notification.envoyer_alerte_ntfy("https://ntfy.sh", "topic", "T", "M",
                                          priorite="tres-urgent")


def test_envoyer_alerte_ntfy_echec_reseau_leve_notification_error_sans_exposer_le_topic(monkeypatch):
    def _post_qui_echoue(*a, **k):
        raise requests.ConnectionError("impossible de joindre le serveur")

    monkeypatch.setattr(notification.requests, "post", _post_qui_echoue)
    with pytest.raises(notification.NotificationError) as exc_info:
        notification.envoyer_alerte_ntfy("https://ntfy.sh", "sujet-secret-a-ne-pas-divulguer",
                                          "T", "M")
    # Le topic fait office de mot de passe (voir generer_topic_ntfy) : il ne doit
    # jamais apparaître dans un message d'erreur potentiellement affiché/loggué.
    assert "sujet-secret-a-ne-pas-divulguer" not in str(exc_info.value)


def test_envoyer_alerte_ntfy_reponse_http_en_erreur_leve_notification_error(monkeypatch):
    monkeypatch.setattr(notification.requests, "post",
                         lambda *a, **k: _ReponseFactice(500))
    with pytest.raises(notification.NotificationError):
        notification.envoyer_alerte_ntfy("https://ntfy.sh", "topic", "T", "M")
