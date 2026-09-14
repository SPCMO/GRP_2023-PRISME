# -*- coding: utf-8 -*-
"""Conversion code station → code site (convention PHyC/BNBV), commune à tous les
bassins gérés par l'outil (Moussoulens n'est qu'un exemple parmi d'autres — voir
Aide.html > Réutilisation pour un autre bassin).

Un **code station** identifie un point de mesure précis : 1 lettre + 9 chiffres (10
caractères), ex. "Y161202001" — c'est ce que l'utilisateur saisit dans l'onglet
Configuration (même convention que dans OPALE v2).

Un **code site** identifie le site hydrométrique dans son ensemble (peut regrouper
plusieurs stations) : 1 lettre + 7 chiffres (8 caractères), ex. "Y1612020" — c'est ce
code qu'attendent les appels PHyC (voir modules.phyc_client.PhycClient.get_seuils_vigilance,
qui documente explicitement "CODE SITE, 7 chiffres après la lettre"), et c'est aussi ce
code qui apparaît tel quel dans LISTE_BASSINS.DAT ainsi que dans les noms de
dossiers/fichiers générés par GRP (ex. Evenements/Y1612020_00J00H15M/).

Le code site s'obtient en retirant les 2 derniers chiffres du code station.
"""

import re

_MOTIF_CODE_STATION = re.compile(r"^[A-Za-z]\d{9}$")
_MOTIF_CODE_SITE = re.compile(r"^[A-Za-z]\d{7}$")


class CodeStationError(ValueError):
    """Levée quand un code station saisi ne respecte pas le format attendu — message
    explicite immédiatement à la saisie, plutôt qu'une erreur PHyC énigmatique plus tard
    ou un mauvais code site silencieusement utilisé partout dans l'outil."""


def valider_code_station(code_station):
    """Normalise (majuscules, espaces retirés) et valide un code station. Lève
    CodeStationError si le format (1 lettre + 9 chiffres) n'est pas respecté."""
    code_station = (code_station or "").strip().upper()
    if not _MOTIF_CODE_STATION.match(code_station):
        raise CodeStationError(
            f"Code station {code_station!r} invalide — format attendu : 1 lettre suivie "
            "de 9 chiffres (ex. Y161202001)."
        )
    return code_station


def code_site_depuis_station(code_station):
    """Dérive le code site (8 caractères) depuis un code station (10 caractères) — le
    code station est validé au passage (voir valider_code_station)."""
    return valider_code_station(code_station)[:-2]


def code_station_par_defaut_depuis_site(code_site):
    """Dérive un code station "par défaut" (10 caractères) depuis un code site (8
    caractères) en lui ajoutant le suffixe "01" — demandé explicitement pour le
    bandeau "Rechercher un code site" de l'onglet Configuration (voir
    ui.tab_config.py et modules.index_bv_phyc), qui ne connaît que le code SITE d'un
    bassin versant (issu de bv_phyc.csv), jamais le code STATION précis attendu par
    l'outil.

    "01" n'est qu'un point de départ RAISONNABLE, pas une garantie : un même site
    peut regrouper plusieurs stations distinctes (ex. Y161201001/Y161202001/
    Y161203001 partagent tous le code site Y1612020) — l'utilisateur reste libre de
    corriger les 2 derniers chiffres après le pré-remplissage si la station visée
    n'est pas la "01" de ce site. Inverse de code_site_depuis_station() ci-dessus,
    qui RETIRE ces 2 chiffres plutôt que de les ajouter.

    Lève CodeStationError si code_site n'a pas le format attendu (1 lettre + 7
    chiffres) — même exception que valider_code_station, pour un traitement uniforme
    côté appelant."""
    code_site = (code_site or "").strip().upper()
    if not _MOTIF_CODE_SITE.match(code_site):
        raise CodeStationError(
            f"Code site {code_site!r} invalide — format attendu : 1 lettre suivie de "
            "7 chiffres (ex. Y1612020)."
        )
    return f"{code_site}01"
