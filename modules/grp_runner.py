# -*- coding: utf-8 -*-
"""Lancement des exécutables GRP (calage BDTR + rejeu opérationnel).

Corrige le bug critique du script d'origine : `bat_file` y pointait vers le DOSSIER
`Temps_Reel` au lieu du fichier `GRP_PREVISION.BAT` lui-même. Sur Windows, exécuter via
`shell=True` une commande qui est un chemin de dossier valide sans rien après revient à
un `cd` implicite dans ce dossier — la commande "réussit" (code retour 0) sans jamais
lancer GRP_PREVISION.BAT, donc le script relisait un PDF périmé sans jamais s'en
apercevoir. Ce module ferme cette faille par deux garde-fous indépendants :
  1. `run_prevision_bat` exige explicitement un chemin de FICHIER (`Path.is_file()`),
     jamais un dossier — erreur immédiate et explicite sinon.
  2. Après exécution, on vérifie qu'un nouveau PDF est bien apparu dans le dossier des
     Fiches_Controle, avec une date de modification postérieure au lancement — un
     returncode 0 seul ne suffit pas à prouver que GRP a réellement tourné.
"""

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

ENCODING_CONSOLE_GRP = "cp1252"  # exécutables GRP : sorties console en cp1252 (Windows FR)


class GrpRunError(Exception):
    """Erreur explicite lors du lancement d'un exécutable/.bat GRP — toujours accompagnée
    du contexte (chemin, returncode, stdout/stderr le cas échéant) pour un diagnostic
    immédiat, jamais un simple "quelque chose a échoué"."""


def run_calage(exe04_path, timeout=1800):
    """Lance 04-Creation_Base_Temps_reel_GRP.exe (calage + régénération de la BDTR).

    Reprend la séquence stdin du script d'origine ("2\\no\\n" — validation des options
    par défaut de l'exe interactif), mais vérifie explicitement le code retour au lieu de
    l'ignorer.

    ⚠️ Un 3ᵉ prompt interactif ("Pressez la touche Entrée pour continuer") apparaît à la
    toute fin de l'exécution, une fois le calage réellement terminé avec succès (constaté
    en conditions réelles : PARAM.DAT écrit, fiches et incertitudes générées...). Le
    script d'origine ne l'envoyait pas ("2\\no\\n" seulement) : stdin se fermait avant que
    GRP ne le lise, provoquant un plantage Fortran ("End of file" sur l'unité stdin) et un
    code retour non nul — alors que le calage avait en réalité abouti. D'où le "\\n" final
    ci-dessous, absent du script d'origine.
    """
    exe04_path = Path(exe04_path)
    if not exe04_path.is_file():
        raise GrpRunError(f"Exécutable de calage introuvable : {exe04_path}")

    try:
        result = subprocess.run(
            [str(exe04_path)],
            input="2\no\n\n",
            capture_output=True,
            text=True,
            encoding=ENCODING_CONSOLE_GRP,
            errors="replace",
            timeout=timeout,
            cwd=exe04_path.parent,
        )
    except subprocess.TimeoutExpired as e:
        raise GrpRunError(
            f"L'exécutable de calage {exe04_path.name} n'a pas terminé en {timeout}s."
        ) from e

    if result.returncode != 0:
        raise GrpRunError(
            f"L'exécutable de calage {exe04_path.name} a retourné le code "
            f"{result.returncode}.\n--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return result


def _nettoyer_dossier_pdf(dossier):
    """Vide le dossier des Fiches_Controle avant un run, pour ne jamais avoir à deviner
    "quel est le PDF qui vient d'être produit" par tri sur la date de modification parmi
    plusieurs candidats — approche fragile du script d'origine."""
    if not os.path.isdir(dossier):
        return
    for nom in os.listdir(dossier):
        if nom.lower().endswith(".pdf"):
            try:
                os.remove(os.path.join(dossier, nom))
            except OSError as e:
                raise GrpRunError(
                    f"Impossible de nettoyer l'ancien PDF {nom} avant le run "
                    f"(fichier verrouillé ?) : {e}"
                ) from e


def run_prevision_bat(bat_path, fiches_controle_dir, timeout=600):
    """Lance GRP_PREVISION.BAT (rejeu opérationnel, config_prevision.ini déjà positionné
    par modules.config_prevision.set_prevision) et retourne le chemin du PDF Fiche_controle
    produit.

    `bat_path` DOIT être le fichier .BAT lui-même (voir docstring du module — c'est le
    bug historique corrigé ici). Lève GrpRunError explicite si :
      - bat_path n'est pas un fichier existant ;
      - le .bat se termine en erreur (returncode != 0) ;
      - le .bat se termine en code 0 mais qu'aucun nouveau PDF n'apparaît (le garde-fou
        qui aurait détecté le bug historique même sans corriger le chemin).

    ⚠️ Constaté en conditions réelles (inspection directe des 2 PDF produits par un vrai
    rejeu, via Test_Fiche_PDF.py) : GRP_PREVISION.BAT produit systématiquement DEUX PDF
    par rejeu. Contrairement à une première hypothèse basée uniquement sur leur nom
    (corrigée ici) :
      - `GRP(<horodatage>) Fiche_controle_Hydrogrammes.pdf` — malgré son nom
        "générique", c'est CELUI-CI qui contient le tableau des indicateurs dQP/dTP/VE/
        KGE (page 2, "Valeurs des critères de performance"), en plus des infos station
        (page 1) ;
      - `GRP(<horodatage>) Fiche_controle_<code_site>_<pas_de_temps>.pdf` — ne contient
        en réalité QUE des graphiques (courbes, axes), aucun tableau de résultats.
    On choisit donc sans ambiguïté celui dont le nom contient "Hydrogrammes" ; sinon (ou
    si aucun/plusieurs PDF ne correspondent), on refuse de deviner et on lève une erreur
    explicite listant les candidats — mieux vaut un échec visible qu'un résultat lu dans
    le mauvais PDF.
    """
    bat_path = Path(bat_path)
    if not bat_path.is_file():
        raise GrpRunError(
            f"GRP_PREVISION.BAT introuvable ou n'est pas un fichier : {bat_path}\n"
            "(vérifiez que le chemin pointe bien sur le .BAT lui-même, pas sur son dossier)"
        )

    _nettoyer_dossier_pdf(fiches_controle_dir)
    t0 = datetime.now()

    try:
        result = subprocess.run(
            [str(bat_path)],
            cwd=str(bat_path.parent),
            capture_output=True,
            text=True,
            encoding=ENCODING_CONSOLE_GRP,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise GrpRunError(f"{bat_path.name} n'a pas terminé en {timeout}s.") from e

    if result.returncode != 0:
        raise GrpRunError(
            f"{bat_path.name} a retourné le code {result.returncode}.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    if not os.path.isdir(fiches_controle_dir):
        raise GrpRunError(
            f"{bat_path.name} s'est terminé en code 0 mais le dossier de sortie attendu "
            f"n'existe pas : {fiches_controle_dir}"
        )
    pdfs_recents = [
        os.path.join(fiches_controle_dir, f)
        for f in os.listdir(fiches_controle_dir)
        if f.lower().endswith(".pdf")
        and datetime.fromtimestamp(os.path.getmtime(os.path.join(fiches_controle_dir, f))) >= t0
    ]
    if not pdfs_recents:
        raise GrpRunError(
            f"{bat_path.name} s'est terminé en code 0 mais aucun nouveau PDF n'a été "
            f"produit dans {fiches_controle_dir} — le rejeu n'a probablement pas eu lieu "
            "(GRP a peut-être affiché une confirmation bloquante, ou une erreur interne "
            "non reflétée dans le code retour)."
        )
    if len(pdfs_recents) > 1:
        candidats_hydrogrammes = [p for p in pdfs_recents
                                   if "hydrogrammes" in os.path.basename(p).lower()]
        if len(candidats_hydrogrammes) == 1:
            return candidats_hydrogrammes[0]
        raise GrpRunError(
            f"{bat_path.name} a produit {len(pdfs_recents)} PDF simultanément dans "
            f"{fiches_controle_dir}, impossible de déterminer sans ambiguïté lequel "
            f"correspond à ce run : {pdfs_recents}"
        )
    return pdfs_recents[0]


def nettoyer_bddtr(dossier_bddtr, max_tentatives=5, delai=3):
    """Supprime intégralement le dossier BDDTR de travail en fin de campagne (confirmé
    sans risque par l'utilisateur : entièrement régénéré par l'exe 04 à chaque calage).
    Reprend le mécanisme de retry du script d'origine (fichiers parfois encore verrouillés
    juste après l'exécution d'un exe GRP), mais journalise chaque tentative explicitement
    au lieu d'un message générique."""
    if not os.path.exists(dossier_bddtr):
        return
    derniere_erreur = None
    for tentative in range(1, max_tentatives + 1):
        try:
            shutil.rmtree(dossier_bddtr)
            return
        except OSError as e:
            derniere_erreur = e
            if tentative < max_tentatives:
                time.sleep(delai)
    raise GrpRunError(
        f"Impossible de supprimer {dossier_bddtr} après {max_tentatives} tentatives : "
        f"{derniere_erreur}"
    )
