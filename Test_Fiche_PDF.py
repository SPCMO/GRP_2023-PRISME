# -*- coding: utf-8 -*-
"""
Test_Fiche_PDF.py — Diagnostic autonome d'un PDF Fiche_controle_<code_site>_<pas_de_temps>.

Usage :
  python Test_Fiche_PDF.py                     -> auto-détecte le PDF le plus récent
                                                    dans le dossier Fiches_Controle de
                                                    config/config.json (pratique depuis
                                                    une console interactive, sans avoir
                                                    à taper un chemin)
  python Test_Fiche_PDF.py "chemin\\vers\\le.pdf"  -> inspecte ce PDF précis

⚠️ L'usage avec chemin explicite ne fonctionne QUE lancé depuis un terminal (Invite de
commandes / PowerShell), jamais depuis l'invite interactive ">>>" d'IDLE ou d'une
console Python nue — celle-ci ne transmet aucun argument au script.

Affiche le nombre de pages et le texte brut extrait de chacune (sans rien interpréter),
pour vérifier que modules.fiche_controle_pdf.extraire_resultat lit la bonne ligne
d'indicateurs dQP/dTP/VE/KGE — utile depuis que ce PDF s'est révélé tenir sur une seule
page (au lieu des 2 pages supposées jusqu'ici, jamais vérifiées faute d'un rejeu abouti).
Ne modifie jamais le PDF, lecture seule.
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfplumber

from modules.fiche_controle_pdf import FicheControleError, extraire_resultat


def _pdf_le_plus_recent():
    """Cherche, dans <dossier_bddtr>/Temps_Reel/Sorties/Fiches_Controle (config.json),
    le PDF le plus récent qui n'est PAS le "Fiche_controle_Hydrogrammes.pdf" générique
    (voir modules.grp_runner — 2 PDF sont produits à chaque rejeu, celui-ci est
    l'autre : celui propre à la station/pas de temps)."""
    chemin_config = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "config", "config.json")
    try:
        with open(chemin_config, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Impossible de lire {chemin_config} : {e}")
        return None

    dossier_bddtr = cfg.get("chemins", {}).get("dossier_bddtr")
    if not dossier_bddtr:
        print("Aucun dossier_bddtr renseigné dans config/config.json (onglet Configuration).")
        return None

    dossier_fiches = os.path.join(dossier_bddtr, "Temps_Reel", "Sorties", "Fiches_Controle")
    candidats = [
        p for p in glob.glob(os.path.join(dossier_fiches, "*.pdf"))
        if "hydrogrammes" not in os.path.basename(p).lower()
    ]
    if not candidats:
        print(f"Aucun PDF Fiche_controle (hors Hydrogrammes) trouvé dans {dossier_fiches}.\n"
              "Lancez un rejeu depuis l'onglet Campagne au préalable, ou passez un chemin "
              "explicite en argument.")
        return None

    plus_recent = max(candidats, key=os.path.getmtime)
    print(f"Aucun chemin fourni — PDF le plus récent auto-détecté :\n  {plus_recent}\n")
    return plus_recent


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        chemin_pdf = sys.argv[1]
    else:
        chemin_pdf = _pdf_le_plus_recent()
        if chemin_pdf is None:
            sys.exit(1)

    if not os.path.isfile(chemin_pdf):
        print(f"Fichier introuvable : {chemin_pdf}")
        sys.exit(1)

    with pdfplumber.open(chemin_pdf) as pdf:
        print(f"Nombre de pages : {len(pdf.pages)}\n")
        for i, page in enumerate(pdf.pages, start=1):
            print(f"--- Page {i} ---")
            print(page.extract_text() or "(texte vide)")
            print()

    print("--- Résultat de extraire_resultat() ---")
    try:
        resultat = extraire_resultat(chemin_pdf)
        print(f"station      : {resultat.station}")
        print(f"date_pic_prev: {resultat.date_pic_prev}")
        print(f"dQP          : {resultat.dqp}")
        print(f"dTP          : {resultat.dtp}")
        print(f"VE           : {resultat.ve}")
        print(f"KGE          : {resultat.kge}")
        print(f"suspects     : {resultat.suspects}")
    except FicheControleError as e:
        print("[ECHEC]", e)
