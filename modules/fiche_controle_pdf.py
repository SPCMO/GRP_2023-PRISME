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


_NOMS_INDICATEURS_PDF = (("dQP", "dqp"), ("dTP", "dtp"), ("VE", "ve"), ("KGE", "kge"))


def _normaliser_nombre(texte):
    texte = (texte.replace("‑", "-").replace("–", "-")
             .replace("—", "-").replace("−", "-").replace(",", "."))
    return texte if re.fullmatch(r"[-+]?\d+(\.\d+)?", texte) else None


def _extraire_indicateurs_page2(page2):
    """Associe chaque valeur numérique de la ligne de résultats (ex. "PP 35.3 −4 11.1
    0.05") à son indicateur par POSITION HORIZONTALE sur la page (colonnes dQP/dTP/VE/
    KGE), au lieu du simple ordre d'apparition dans le texte brut.

    ⚠️ Nécessaire car constaté en conditions réelles : quand GRP ne peut pas calculer un
    indicateur pour une crue donnée, la cellule correspondante du PDF est simplement
    VIDE (contrairement à CRITERES_PERF.DAT, qui écrit le texte "NA" — voir
    modules.criteres_perf) — un texte extrait à plat n'a alors plus que 2 ou 3 nombres
    au lieu de 4, et les lire dans l'ordre décale silencieusement chaque valeur restante
    vers le mauvais indicateur (ex. une KGE lue comme si c'était le dTP). L'alignement
    par colonne détecte la valeur manquante et la représente par None (comme
    modules.criteres_perf le fait déjà pour "NA"), sans jamais décaler les autres.

    Retourne un dict {"dqp": float|None, ...}, ou None si les en-têtes de colonnes
    eux-mêmes sont introuvables (page qui ne ressemble pas au format attendu).
    """
    mots = page2.extract_words()
    entetes_x = {}
    for texte_entete, cle in _NOMS_INDICATEURS_PDF:
        mot = next((w for w in mots if w["text"] == texte_entete), None)
        if mot is not None:
            entetes_x[cle] = mot["x0"]
    if len(entetes_x) < 4:
        return None

    top_entetes = min(w["top"] for w in mots if w["text"] in dict(_NOMS_INDICATEURS_PDF))
    # Ligne de valeurs : nettement sous la ligne d'en-tête (au moins ~15pt, tolérance
    # large car "Scénario" s'intercale parfois sur sa propre ligne entre les deux).
    sous_entetes = [w for w in mots if w["top"] > top_entetes + 15]
    if not sous_entetes:
        return {cle: None for _entete, cle in _NOMS_INDICATEURS_PDF}
    top_ligne_valeurs = min(w["top"] for w in sous_entetes)
    ligne_valeurs = [w for w in sous_entetes if abs(w["top"] - top_ligne_valeurs) < 5]

    resultat = {}
    for mot in ligne_valeurs:
        nombre = _normaliser_nombre(mot["text"])
        if nombre is None:
            continue  # ex. le label du scénario ("PP", "Obs"...), pas une valeur
        cle_colonne = min(entetes_x, key=lambda c: abs(entetes_x[c] - mot["x0"]))
        resultat[cle_colonne] = float(nombre)
    for _entete, cle in _NOMS_INDICATEURS_PDF:
        resultat.setdefault(cle, None)
    return resultat


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
            indicateurs = _extraire_indicateurs_page2(pdf.pages[1])
    except FicheControleError:
        raise
    except Exception as e:
        raise FicheControleError(f"Impossible de lire le PDF {chemin_pdf} : {e}") from e

    resultat = ResultatFicheControle(chemin_pdf=chemin_pdf)

    m_station = re.search(r"(?:Code|Station)\s*:\s*(\w+)", texte_p1, re.IGNORECASE)
    resultat.station = m_station.group(1) if m_station else None

    m_date = re.search(r"Sc\.\s+PP.*?\((?P<date>\d{2}/\d{2}\s+\d{2}:\d{2})\)", texte_p1)
    resultat.date_pic_prev = m_date.group("date") if m_date else None

    if indicateurs is None:
        # Les en-têtes de colonnes dQP/dTP/VE/KGE eux-mêmes sont introuvables — page qui
        # ne ressemble pas du tout au format attendu (mise à jour de GRP ?), à distinguer
        # d'un simple indicateur manquant (voir _extraire_indicateurs_page2). Le dossier
        # Fiches_Controle étant vidé avant chaque rejeu, ce PDF ne sera plus consultable
        # une fois la campagne passée à l'étape suivante — texte de la page 2 inclus
        # directement dans l'erreur pour un auto-diagnostic dans le journal de campagne.
        raise FicheControleError(
            f"{chemin_pdf} : en-têtes dQP/dTP/VE/KGE introuvables sur la page 2 — "
            "format de PDF inattendu (mise à jour de GRP ?).\n"
            f"--- Texte brut de la page 2 ---\n{texte_p2 or '(page 2 vide)'}"
        )
    resultat.dqp = indicateurs["dqp"]
    resultat.dtp = indicateurs["dtp"]
    resultat.ve = indicateurs["ve"]
    resultat.kge = indicateurs["kge"]
    if all(v is None for v in indicateurs.values()):
        # En-têtes trouvés mais aucune valeur alignée dessous — distinct d'un NA isolé
        # sur un seul indicateur (accepté, voir _extraire_indicateurs_page2), ici rien
        # d'exploitable du tout pour cette crue : mieux vaut un échec explicite qu'un
        # résultat entièrement vide silencieusement marqué "success" en base.
        raise FicheControleError(
            f"{chemin_pdf} : aucune valeur dQP/dTP/VE/KGE trouvée sous les en-têtes "
            f"sur la page 2 (toutes NA/vides ?).\n"
            f"--- Texte brut de la page 2 ---\n{texte_p2 or '(page 2 vide)'}"
        )

    _valider_plausibilite(resultat)
    return resultat
