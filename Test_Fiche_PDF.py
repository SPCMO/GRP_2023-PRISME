# -*- coding: utf-8 -*-
"""
Test_Fiche_PDF.py — Diagnostic autonome d'un PDF Fiche_controle_<code_site>_<pas_de_temps>.

Usage le plus simple : ouvrir ce fichier dans Thonny (ou tout autre éditeur) et cliquer
sur Exécuter/Run (ou F5) — rien d'autre à faire, aucun chemin à taper. Le script détecte
tout seul le PDF le plus récent dans le dossier Fiches_Controle de config/config.json,
lit son contenu, l'affiche, et s'arrête proprement (jamais d'attente de saisie).

Usage avancé (terminal uniquement — PowerShell/Invite de commandes, jamais l'invite
">>>" d'IDLE, qui ne transmet aucun argument) : pour inspecter un PDF précis au lieu du
plus récent :
  python Test_Fiche_PDF.py "chemin\\vers\\le.pdf"

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

# La console Windows par défaut (cp1252) plante sur certains caractères que GRP produit
# dans ses PDF (ex. le signe moins unicode "−", U+2212, différent du tiret ASCII "-") —
# reconfigurée en UTF-8 avec repli explicite plutôt que de laisser le script planter sur
# un simple print() de diagnostic.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pdfplumber
except ImportError:
    print("Le paquet 'pdfplumber' n'est pas installé dans l'interpréteur Python utilisé "
          "par Thonny (Outils > Gérer les paquets... > installer 'pdfplumber'), ou "
          "Thonny utilise un interpréteur différent de celui de l'outil GRP_2023-PRISME "
          "(Exécuter > Configurer l'interpréteur...).")
    sys.exit(1)

from modules.fiche_controle_pdf import FicheControleError, extraire_resultat


def _pdf_le_plus_recent():
    """Cherche, dans <dossier_bddtr>/Temps_Reel/Sorties/Fiches_Controle (config.json),
    le PDF "Fiche_controle_Hydrogrammes.pdf" le plus récent — voir modules.grp_runner :
    2 PDF sont produits à chaque rejeu, et malgré son nom "générique", c'est CELUI-CI
    (pas celui nommé par station/pas de temps) qui contient le tableau d'indicateurs
    dQP/dTP/VE/KGE, confirmé par inspection directe d'un vrai rejeu."""
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
        if "hydrogrammes" in os.path.basename(p).lower()
    ]
    if not candidats:
        print(f"Aucun PDF Fiche_controle_Hydrogrammes trouvé dans {dossier_fiches}.\n"
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
