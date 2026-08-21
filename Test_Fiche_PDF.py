# -*- coding: utf-8 -*-
"""
Test_Fiche_PDF.py — Diagnostic autonome d'un PDF Fiche_controle_<code_site>_<pas_de_temps>.

Usage :  python Test_Fiche_PDF.py "chemin\\vers\\GRP(...) Fiche_controle_Y1612020_00J00H15M.pdf"

Affiche le nombre de pages et le texte brut extrait de chacune (sans rien interpréter),
pour vérifier que modules.fiche_controle_pdf.extraire_resultat lit la bonne ligne
d'indicateurs dQP/dTP/VE/KGE — utile depuis que ce PDF s'est révélé tenir sur une seule
page (au lieu des 2 pages supposées jusqu'ici, jamais vérifiées faute d'un rejeu abouti).
Ne modifie jamais le PDF, lecture seule.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfplumber

from modules.fiche_controle_pdf import FicheControleError, extraire_resultat

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python Test_Fiche_PDF.py <chemin_du_pdf>")
        sys.exit(1)

    chemin_pdf = sys.argv[1]
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
