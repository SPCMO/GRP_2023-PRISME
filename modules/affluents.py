# -*- coding: utf-8 -*-
"""Stations affluentes (onglet "Analyse crues affl.") — persistance de la liste
d'affluents (nom, surface de BV, temps de propagation P10/P50/P90, fichier de débits,
couleur de tracé), lecture de leurs fichiers de débits et calculs dérivés (Qmax/volume
transité, bandes de propagation).

Format des fichiers de débits affluents (vérifié sur les fichiers réels du dossier
00_Donnees_Moussoulens/Autres_Q/*.csv) : CSV séparé par ';', en-tête "date;res", une
ligne par pas de temps, date au format AAAAMMJJHHMM (même format que les fichiers GRP
EVxxxx.DAT — voir modules.criteres_perf.FORMAT_DATE), valeur en m³/s. Série longue et
continue (plusieurs années), contrairement aux EVxxxx.DAT qui sont un fichier par crue
déjà découpé par GRP : on filtre ici à la volée sur la fenêtre DateDeb/DateFin de la
crue affichée.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

FORMAT_DATE = "%Y%m%d%H%M"


class AffluentError(Exception):
    """Erreur explicite de lecture/format sur un fichier de débits affluent."""


@dataclass
class Affluent:
    nom: str = ""
    surface_bv_km2: Optional[float] = None
    p10_min: Optional[int] = None   # temps de propagation, en minutes
    p50_min: Optional[int] = None   # obligatoire pour tracer une bande de propagation
    p90_min: Optional[int] = None
    fichier: Optional[str] = None   # chemin absolu du fichier de débits
    couleur: Optional[str] = None   # code hex — attribuée par défaut à la création


def config_affluents_par_defaut():
    return {"dossier_import": "", "liste": []}


def affluent_depuis_dict(d):
    return Affluent(
        nom=d.get("nom", ""), surface_bv_km2=d.get("surface_bv_km2"),
        p10_min=d.get("p10_min"), p50_min=d.get("p50_min"), p90_min=d.get("p90_min"),
        fichier=d.get("fichier"), couleur=d.get("couleur"),
    )


def affluent_vers_dict(a):
    return {"nom": a.nom, "surface_bv_km2": a.surface_bv_km2, "p10_min": a.p10_min,
            "p50_min": a.p50_min, "p90_min": a.p90_min, "fichier": a.fichier,
            "couleur": a.couleur}


def minutes_vers_hhmm(minutes):
    """None -> chaîne vide (champ facultatif non renseigné)."""
    if minutes is None:
        return ""
    h, m = divmod(int(round(minutes)), 60)
    return f"{h:02d}:{m:02d}"


def hhmm_vers_minutes(texte):
    """Parse 'hh:mm' -> minutes (int). Chaîne vide/blanche -> None (P10/P90
    facultatifs). Lève ValueError si le texte n'est pas vide mais mal formé."""
    texte = (texte or "").strip()
    if not texte:
        return None
    if ":" not in texte:
        raise ValueError(f"Format attendu hh:mm, reçu : {texte!r}")
    h_str, m_str = texte.split(":", 1)
    h, m = int(h_str), int(m_str)
    if h < 0 or m < 0 or m > 59:
        raise ValueError(f"Temps de propagation invalide : {texte!r}")
    return h * 60 + m


def charger_serie_affluent(chemin, date_deb=None, date_fin=None):
    """Lit un fichier de débits affluent (CSV ';', en-tête "date;res"). Retourne une
    liste de (datetime, valeur) triée chronologiquement, filtrée sur [date_deb, date_fin]
    si fournis (bornes incluses). Best-effort ligne par ligne : une ligne mal formée est
    ignorée plutôt que de faire échouer tout le chargement (fichier réel très long,
    potentiellement mis à jour en continu par un autre processus)."""
    if not chemin or not os.path.isfile(chemin):
        raise FileNotFoundError(f"Fichier de débits affluent introuvable : {chemin}")
    with open(chemin, "rb") as fh:
        data = fh.read()
    try:
        texte = data.decode("utf-8")
    except UnicodeDecodeError:
        texte = data.decode("cp1252")

    serie = []
    for ligne in texte.splitlines()[1:]:  # ligne 1 = en-tête "date;res"
        ligne = ligne.strip()
        if not ligne:
            continue
        champs = ligne.split(";")
        if len(champs) != 2:
            continue
        try:
            date = datetime.strptime(champs[0], FORMAT_DATE)
            valeur = float(champs[1])
        except ValueError:
            continue
        if date_deb is not None and date < date_deb:
            continue
        if date_fin is not None and date > date_fin:
            continue
        serie.append((date, valeur))
    serie.sort(key=lambda p: p[0])
    return serie


def valeur_au_plus_proche(serie, date_cible):
    """Retourne (valeur, date_trouvee) au point de `serie` dont l'horodatage est le
    plus proche de `date_cible`, ou (None, None) si la série est vide ou `date_cible`
    est None. Utilisé pour la rétropropagation : la valeur d'un affluent au moment où
    l'eau qu'il a fournie atteint (en théorie) le pic de l'exutoire n'existe pas
    forcément exactement dans son échantillonnage — on prend le point le plus proche."""
    if not serie or date_cible is None:
        return None, None
    date_trouvee, valeur = min(serie, key=lambda p: abs((p[0] - date_cible).total_seconds()))
    return valeur, date_trouvee


def qmax_et_horodatage(serie):
    """serie : liste de (datetime, valeur). Retourne (qmax, date_qmax), ou (None, None)
    si la série est vide."""
    if not serie:
        return None, None
    date_max, valeur_max = max(serie, key=lambda p: p[1])
    return valeur_max, date_max


def volume_m3(serie):
    """Volume transité (m³) par intégration trapézoïdale du débit (m³/s) sur le temps
    (s) entre points successifs. None si moins de 2 points (non calculable)."""
    if len(serie) < 2:
        return None
    total = 0.0
    for (d1, v1), (d2, v2) in zip(serie, serie[1:]):
        dt_s = (d2 - d1).total_seconds()
        if dt_s <= 0:
            continue
        total += (v1 + v2) / 2 * dt_s
    return total


def bornes_bande_propagation(date_pic_affluent, affluent):
    """À partir de l'horodatage du pic de l'affluent (sur la crue affichée) et de ses
    temps de propagation P10/P50/P90, retourne (date_p10, date_p50, date_p90) à
    afficher sur l'hydrogramme de la station exutoire — None pour une borne dont le
    temps de propagation correspondant n'a pas été saisi. (None, None, None) si le pic
    de l'affluent est inconnu ou si P50 n'a pas été renseigné (pas de bande sans lui)."""
    if date_pic_affluent is None or affluent.p50_min is None:
        return None, None, None
    date_p10 = (date_pic_affluent + timedelta(minutes=affluent.p10_min)
                if affluent.p10_min is not None else None)
    date_p50 = date_pic_affluent + timedelta(minutes=affluent.p50_min)
    date_p90 = (date_pic_affluent + timedelta(minutes=affluent.p90_min)
                if affluent.p90_min is not None else None)
    return date_p10, date_p50, date_p90
