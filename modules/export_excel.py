# -*- coding: utf-8 -*-
"""Export Excel des résultats de campagne — généré à la demande depuis results_store
(pas pendant le run), en plus du dashboard interactif (décision validée avec l'utilisateur).
"""

import os

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from modules import results_store
from modules.score import calculer_scores

ENTETES_DETAIL = ("Horizon", "Seuil C1", "Méthode", "Date crue", "Statut", "dQP (%)",
                   "dTP (pdt)", "VE (%)", "KGE", "Suspect", "Erreur")
ENTETES_SYNTHESE = ("Horizon", "Seuil C1", "Méthode", "Score composite", "Nb crues",
                     "Moyenne |dQP| (%)", "Moyenne |dTP|", "Moyenne |VE| (%)", "Moyenne (1-KGE)")


def exporter(chemin_xlsx, db_path=None):
    """Génère un classeur à deux feuilles ("Détail" et "Synthèse") depuis les résultats
    actuellement en base. Lève une exception explicite si aucun résultat n'existe encore
    (plutôt qu'un fichier Excel vide et silencieusement inutile)."""
    with results_store.db_session(db_path) as conn:
        lignes = [dict(r) for r in results_store.list_resultats_avec_combinaison(conn)]

    if not lignes:
        raise ValueError("Aucun résultat de campagne en base — lancez d'abord une campagne "
                          "(onglet Campagne) avant d'exporter.")

    wb = Workbook()

    ws_detail = wb.active
    ws_detail.title = "Détail"
    ws_detail.append(ENTETES_DETAIL)
    for cell in ws_detail[1]:
        cell.font = Font(bold=True)
    for l in lignes:
        ws_detail.append((
            l["horizon"], l["seuil_c1"], l["methode"], l["crue_date"], l["statut_crue"],
            l["dqp"], l["dtp"], l["ve"], l["kge"], l["suspects"] or "",
            "",
        ))

    ws_synthese = wb.create_sheet("Synthèse")
    ws_synthese.append(ENTETES_SYNTHESE)
    for cell in ws_synthese[1]:
        cell.font = Font(bold=True)
    lignes_ok = [l for l in lignes if l["statut_crue"] == "success"]
    for s in calculer_scores(lignes_ok):
        ws_synthese.append((
            s.horizon, s.seuil_c1, s.methode,
            round(s.score, 4) if s.score is not None else None,
            s.nb_crues,
            round(s.moyennes_erreur["dqp"], 2) if s.moyennes_erreur.get("dqp") is not None else None,
            round(s.moyennes_erreur["dtp"], 2) if s.moyennes_erreur.get("dtp") is not None else None,
            round(s.moyennes_erreur["ve"], 2) if s.moyennes_erreur.get("ve") is not None else None,
            round(s.moyennes_erreur["kge"], 4) if s.moyennes_erreur.get("kge") is not None else None,
        ))

    for ws in (ws_detail, ws_synthese):
        for i, _ in enumerate(ws[1], start=1):
            ws.column_dimensions[get_column_letter(i)].width = 16

    os.makedirs(os.path.dirname(chemin_xlsx) or ".", exist_ok=True)
    wb.save(chemin_xlsx)
    return chemin_xlsx
