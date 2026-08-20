# -*- coding: utf-8 -*-
"""Détection automatique des crues (bloc 4) — lecture des fichiers déjà générés par GRP
dans 00_Resultats_<station>/03-Hydrogrammes_Prevus/Evenements/<code>_<pdt>/ :

  - SELECTION_EVT.DAT : dates de début des événements de débit ET de pluie détectés par
    GRP, avec Qmax et pluie cumulée 24h max de chacun.
  - CRITERES_PERF.DAT : une ligne par événement, colonnes NumEvt/TypEvt/Conf/DateDeb/
    DateFin/DateQmax/Qmax/dQP/dTP/VE/KGE — stats de RÉFÉRENCE pour la configuration de
    calage actuellement en place dans LISTE_BASSINS.DAT (PAS le résultat de la campagne
    testée, qui vient exclusivement de modules.fiche_controle_pdf via le rejeu
    opérationnel — voir le plan, ces deux sources mesurent des choses différentes).
  - <code>-EV000N.DAT : série observée (Pobs, Qobs) sur la fenêtre de l'événement N, pour
    les courbes du dashboard (bloc 6).

Usage en LECTURE SEULE : ce module ne modifie jamais ces fichiers, générés uniquement par
les exécutables GRP (01-Calage_GRP.exe / 03-Trace_Hydrogrammes_Prevus.exe).
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from modules.fiche_controle_pdf import BORNES_PLAUSIBLES

FORMAT_DATE = "%Y%m%d%H%M"


class CriteresPerfError(Exception):
    """Erreur explicite de lecture/format sur CRITERES_PERF.DAT, SELECTION_EVT.DAT ou une
    série d'événement — toujours avec le chemin et, si possible, le numéro de ligne."""


def _lire_lignes_tolerant(path):
    """Lit un fichier texte GRP en tentant l'UTF-8 en priorité, puis le cp1252 en repli.

    Constaté sur les fichiers réels du dépôt : SELECTION_EVT.DAT (texte français accentué)
    est en UTF-8, alors que LISTE_BASSINS.DAT et config_prevision.ini sont en cp1252 (voir
    modules.liste_bassins/modules.config_prevision) — les différentes étapes du pipeline
    GRP (exécutables Fortran vs script R) n'utilisent pas le même encodage de sortie. Un
    décodage UTF-8 strict échoue de façon fiable sur du texte cp1252 réellement accentué
    (séquences d'octets invalides en UTF-8), ce qui rend cette détection robuste plutôt
    qu'une simple supposition sur le nom du fichier.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        texte = data.decode("utf-8")
    except UnicodeDecodeError:
        texte = data.decode("cp1252")
    return texte.splitlines(keepends=True)


def _parse_date_grp(valeur, contexte):
    try:
        return datetime.strptime(valeur, FORMAT_DATE)
    except ValueError as e:
        raise CriteresPerfError(
            f"{contexte} : date '{valeur}' non conforme au format attendu AAAAMMJJHHMM."
        ) from e


@dataclass
class EvenementPerf:
    num_evt: int
    typ_evt: str          # "Q" (crue de débit) ou "P" (événement de pluie)
    conf: str              # ex. "SMN_TAN" — configuration de calage en place au moment du run
    date_deb: datetime
    date_fin: datetime
    date_qmax: datetime
    qmax: float
    dqp: Optional[float]
    dtp: Optional[float]
    ve: Optional[float]
    kge: Optional[float]
    suspects: list = field(default_factory=list)

    @property
    def est_crue(self):
        return self.typ_evt == "Q"


def parse_criteres_perf(path):
    """Lit CRITERES_PERF.DAT — retourne la liste des EvenementPerf (toutes valeurs
    typées, dates converties, plausibilité vérifiée). Lève CriteresPerfError explicite
    si une ligne ne peut pas être interprétée, en indiquant le numéro de ligne."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CRITERES_PERF.DAT introuvable : {path}")

    lignes = _lire_lignes_tolerant(path)

    if not lignes:
        raise CriteresPerfError(f"{path} est vide.")

    evenements = []
    # Première ligne = en-tête de colonnes ("NumEvt TypEvt Conf DateDeb ..."), ignorée.
    for numero_ligne, ligne in enumerate(lignes[1:], start=2):
        champs = ligne.split()
        if not champs:
            continue  # ligne vide en fin de fichier
        if len(champs) != 11:
            raise CriteresPerfError(
                f"{path} ligne {numero_ligne} : {len(champs)} champs trouvés, 11 attendus "
                f"(NumEvt TypEvt Conf DateDeb DateFin DateQmax Qmax dQP dTP VE KGE) : "
                f"{ligne!r}"
            )
        contexte = f"{path} ligne {numero_ligne}"
        try:
            num_evt = int(champs[0])
            typ_evt = champs[1]
            conf = champs[2]
            date_deb = _parse_date_grp(champs[3], contexte)
            date_fin = _parse_date_grp(champs[4], contexte)
            date_qmax = _parse_date_grp(champs[5], contexte)
            qmax = float(champs[6])
            # GRP note "NA" quand un indicateur n'est pas calculable — on le représente
            # explicitement par None plutôt que par un float invalide.
            dqp, dtp, ve, kge = (
                None if v.upper() == "NA" else float(v) for v in champs[7:11]
            )
        except ValueError as e:
            raise CriteresPerfError(f"{contexte} : valeur non interprétable ({e}) : {ligne!r}") from e

        evt = EvenementPerf(num_evt, typ_evt, conf, date_deb, date_fin, date_qmax, qmax,
                             dqp, dtp, ve, kge)
        for nom in ("dqp", "dtp", "ve", "kge"):
            valeur = getattr(evt, nom)
            bornes = BORNES_PLAUSIBLES.get(nom)
            if valeur is not None and bornes and not (bornes[0] <= valeur <= bornes[1]):
                evt.suspects.append(nom)
        evenements.append(evt)

    return evenements


@dataclass
class SelectionEvt:
    date_debut: datetime
    date_qmax: datetime
    qmax: float
    pcum24h_max: float
    type_evt: str  # "Q" ou "P", selon la section du fichier où la ligne a été trouvée


def parse_selection_evt(path):
    """Lit SELECTION_EVT.DAT — retourne la liste des SelectionEvt (débit puis pluie)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SELECTION_EVT.DAT introuvable : {path}")

    lignes = _lire_lignes_tolerant(path)

    motif = re.compile(
        r"^(?P<debut>\d{12})\s*\(Qmax\s*:\s*(?P<date_qmax>\d{12})\s+"
        r"(?P<qmax>[-+]?\d+\.?\d*)\s*m3/s\s*-\s*Pcum24h max:\s*(?P<pcum>[-+]?\d+\.?\d*)\s*mm\)"
    )

    resultats = []
    type_courant = "Q"
    for numero_ligne, ligne in enumerate(lignes, start=1):
        ligne_strip = ligne.strip()
        if "événements de pluie" in ligne_strip or "evenements de pluie" in ligne_strip:
            type_courant = "P"
            continue
        if "événements de débits" in ligne_strip or "evenements de d" in ligne_strip:
            type_courant = "Q"
            continue
        m = motif.match(ligne_strip)
        if not m:
            continue  # ligne d'en-tête/légende, pas une date d'événement
        contexte = f"{path} ligne {numero_ligne}"
        resultats.append(SelectionEvt(
            date_debut=_parse_date_grp(m.group("debut"), contexte),
            date_qmax=_parse_date_grp(m.group("date_qmax"), contexte),
            qmax=float(m.group("qmax")),
            pcum24h_max=float(m.group("pcum")),
            type_evt=type_courant,
        ))
    return resultats


def parse_evenement_serie(path):
    """Lit un fichier <code>-EV000N.DAT (série Pobs/Qobs sur la fenêtre d'un événement).
    Retourne une liste de tuples (datetime, pobs_mm_h, qobs_m3s), triée chronologiquement.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Fichier événement introuvable : {path}")

    lignes = _lire_lignes_tolerant(path)

    if not lignes:
        raise CriteresPerfError(f"{path} est vide.")

    serie = []
    for numero_ligne, ligne in enumerate(lignes[1:], start=2):  # ligne 1 = en-tête
        champs = ligne.split()
        if not champs:
            continue
        if len(champs) != 3:
            raise CriteresPerfError(
                f"{path} ligne {numero_ligne} : {len(champs)} champs trouvés, 3 attendus "
                f"(Date Pobs Qobs) : {ligne!r}"
            )
        contexte = f"{path} ligne {numero_ligne}"
        date = _parse_date_grp(champs[0], contexte)
        try:
            pobs = float(champs[1])
            qobs = float(champs[2])
        except ValueError as e:
            raise CriteresPerfError(f"{contexte} : valeur Pobs/Qobs non numérique : {e}") from e
        serie.append((date, pobs, qobs))

    return serie
