# -*- coding: utf-8 -*-
"""Édition ciblée de config_prevision.ini (00_BDDTR_<station>/Temps_Reel/Parametrage/).

Fichier .ini réel (sections [GENERAL]/[CHEMINS]/[OBSERVATIONS]/[SCENARIOS]/[SORTIES]) mais
à commentaires ";" abondants et documentation intercalée. On n'utilise volontairement PAS
`configparser` pour l'écriture : il perdrait tous les commentaires au moment du dump. On
reprend donc l'approche du script d'origine (remplacement ligne à ligne par préfixe de
clé), mais en vérifiant explicitement que chaque clé attendue a bien été trouvée et
modifiée — le script d'origine écrivait silencieusement un fichier sans jamais vérifier
que les remplacements avaient eu lieu.
"""

import os

ENCODING = "cp1252"


class ConfigPrevisionError(Exception):
    """Levée si une clé attendue de config_prevision.ini est introuvable — le fichier ne
    doit jamais être réécrit à moitié modifié sans que l'appelant le sache."""


def set_prevision(path, instpr, modfon="Temps_diff", confirm="NON", affobs="OUI",
                   encoding=ENCODING):
    """Positionne le rejeu opérationnel sur l'instant `instpr` (datetime) en mode temps
    différé. Lève ConfigPrevisionError si une des clés MODFON/INSTPR/CONFIRM/AFFOBS est
    absente du fichier (format inattendu) plutôt que d'écrire un fichier partiel.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"config_prevision.ini introuvable : {path}")

    valeurs = {
        "MODFON": modfon,
        "INSTPR": instpr.strftime("%Y-%m-%d %H:%M:%S"),
        "CONFIRM": confirm,
        "AFFOBS": affobs,
    }
    trouvees = {cle: False for cle in valeurs}

    with open(path, encoding=encoding) as fh:
        lignes = fh.readlines()

    nouvelles_lignes = []
    for ligne in lignes:
        cle_ligne = ligne.strip().split("=", 1)[0] if "=" in ligne else None
        if cle_ligne in valeurs:
            nouvelles_lignes.append(f"{cle_ligne}={valeurs[cle_ligne]}\n")
            trouvees[cle_ligne] = True
        else:
            nouvelles_lignes.append(ligne)

    manquantes = [cle for cle, ok in trouvees.items() if not ok]
    if manquantes:
        raise ConfigPrevisionError(
            f"Clé(s) {', '.join(manquantes)} introuvable(s) dans {path} — "
            "format de config_prevision.ini inattendu, fichier NON modifié."
        )

    with open(path, "w", encoding=encoding) as fh:
        fh.writelines(nouvelles_lignes)
