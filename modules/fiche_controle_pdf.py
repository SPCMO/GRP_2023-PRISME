# -*- coding: utf-8 -*-
"""Extraction des indicateurs dQP/dTP/VE/KGE depuis un PDF "Fiche_controle" — source
primaire des résultats de la campagne (rejeu opérationnel, voir modules.grp_runner).

Reprend l'extraction pdfplumber du script d'origine (isolée ici dans son propre module),
en ajoutant :
  - une validation de plausibilité explicite (bornes raisonnables sur chaque indicateur),
    demandée par l'utilisateur pour fiabiliser la lecture des résultats GRP ;
  - une gestion propre des dates/valeurs non conformes (erreur explicite avec le nom du
    fichier concerné, jamais un None silencieux qui se propagerait plus loin).
"""

import re
from dataclasses import dataclass, field
from typing import Optional

import pdfplumber

# Bornes de plausibilité (larges mais informatives) — un indicateur hors bornes n'est PAS
# rejeté (l'utilisateur veut voir toute donnée produite par GRP), mais marqué `suspect`
# et signalé en évidence dans l'UI/les logs plutôt que traité silencieusement comme fiable.
BORNES_PLAUSIBLES = {
    "dqp": (-100.0, 300.0),   # %
    "dtp": (-200, 200),        # pas de temps (entier)
    "ve": (-200.0, 200.0),     # %
    "kge": (-20.0, 1.0),       # KGE théoriquement <= 1
}


class FicheControleError(Exception):
    """Levée quand un PDF Fiche_controle ne peut pas être lu ou ne contient pas les
    indicateurs attendus — toujours avec le chemin du fichier en cause."""


@dataclass
class ResultatFicheControle:
    chemin_pdf: str
    station: Optional[str] = None
    date_pic_prev: Optional[str] = None
    dqp: Optional[float] = None
    dtp: Optional[float] = None
    ve: Optional[float] = None
    kge: Optional[float] = None
    suspects: list = field(default_factory=list)  # noms des indicateurs hors bornes plausibles

    @property
    def est_suspect(self):
        return bool(self.suspects)


def _valider_plausibilite(resultat: ResultatFicheControle):
    """Marque `suspects` pour chaque indicateur hors bornes plausibles — ne lève jamais,
    ne modifie jamais la valeur : l'utilisateur doit voir la vraie donnée extraite, avec
    juste un avertissement visuel dessus."""
    for nom_indicateur, (mini, maxi) in BORNES_PLAUSIBLES.items():
        valeur = getattr(resultat, nom_indicateur)
        if valeur is not None and not (mini <= valeur <= maxi):
            resultat.suspects.append(nom_indicateur)


def extraire_resultat(chemin_pdf):
    """Extrait station/date de pic/dQP/dTP/VE/KGE depuis un PDF Fiche_controle.

    Lève FicheControleError si le PDF est illisible ou si les 4 indicateurs
    dQP/dTP/VE/KGE ne sont pas trouvés (jamais un résultat partiel silencieux : mieux
    vaut un échec explicite de cette crue que des statistiques faussées plus tard dans
    le dashboard).

    Suppose bien 2 pages (page 1 = infos station, page 2 = "Valeurs des critères de
    performance" avec dQP/dTP/VE/KGE) — confirmé par inspection directe d'un vrai PDF
    `Fiche_controle_Hydrogrammes.pdf` produit par un rejeu réel (voir Test_Fiche_PDF.py
    et modules.grp_runner.run_prevision_bat, qui sélectionne précisément ce PDF parmi
    les 2 produits par GRP_PREVISION.BAT — l'autre, nommé par station/pas de temps,
    ne contient que des graphiques, aucun tableau).
    """
    try:
        with pdfplumber.open(chemin_pdf) as pdf:
            if len(pdf.pages) < 2:
                raise FicheControleError(
                    f"{chemin_pdf} : PDF à {len(pdf.pages)} page(s), 2 attendues "
                    "(page 1 = infos station, page 2 = indicateurs de performance)."
                )
            texte_p1 = pdf.pages[0].extract_text() or ""
            texte_p2 = pdf.pages[1].extract_text() or ""
    except FicheControleError:
        raise
    except Exception as e:
        raise FicheControleError(f"Impossible de lire le PDF {chemin_pdf} : {e}") from e

    resultat = ResultatFicheControle(chemin_pdf=chemin_pdf)

    m_station = re.search(r"(?:Code|Station)\s*:\s*(\w+)", texte_p1, re.IGNORECASE)
    resultat.station = m_station.group(1) if m_station else None

    m_date = re.search(r"Sc\.\s+PP.*?\((?P<date>\d{2}/\d{2}\s+\d{2}:\d{2})\)", texte_p1)
    resultat.date_pic_prev = m_date.group("date") if m_date else None

    indicateurs_trouves = False
    for ligne in texte_p2.split("\n"):
        ligne_normalisee = (
            ligne.replace("‑", "-").replace("–", "-")
            .replace("—", "-").replace("−", "-").replace(",", ".")
        )
        scores = re.findall(r"[-+]?\d+\.\d+|[-+]?\d+", ligne_normalisee)
        if len(scores) >= 4:
            try:
                resultat.dqp = float(scores[0])
                resultat.dtp = float(scores[1])
                resultat.ve = float(scores[2])
                resultat.kge = float(scores[3])
            except ValueError as e:
                raise FicheControleError(
                    f"{chemin_pdf} : valeurs dQP/dTP/VE/KGE non numériques sur la ligne "
                    f"{ligne!r} : {e}"
                ) from e
            indicateurs_trouves = True
            break

    if not indicateurs_trouves:
        raise FicheControleError(
            f"{chemin_pdf} : indicateurs dQP/dTP/VE/KGE introuvables sur la page 2 — "
            "format de PDF inattendu (mise à jour de GRP ?)."
        )

    _valider_plausibilite(resultat)
    return resultat
