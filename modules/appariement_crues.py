# -*- coding: utf-8 -*-
"""Appariement d'une même crue vue sous des dates de début différentes.

Contexte (constaté en conditions réelles, 24 septembre 2026, station Carcassonne_PV) :
la date de début (date_deb) d'un événement, telle que GRP la reporte dans
CRITERES_PERF.DAT, dépend de la durée de fenêtre d'événement — champ NJ de
LISTE_BASSINS.DAT (NJ=2 -> fenêtre de 48 h, pic à +24 h du début ; NJ=3 -> 72 h, pic à
+36 h). Passer NJ de 2 à 3 entre deux campagnes décale de 12 h le début de CHAQUE
épisode : la même crue physique apparaît alors sous deux clés différentes, alors que
toute la base de résultats (resultats_crues.crue_date), la sélection de crues et le
score sont indexés par date_deb. Résultat : sélecteur de crues du score plein de « ? »
et de tirets, crues comptées en double, dQP/dTP non recalculables, instants de rejeu
supplémentaires ignorés...

Ce qui, lui, ne change PAS quand NJ change, c'est l'heure du pic (DateQmax) : deux
fenêtres différentes d'un même épisode contiennent le même pic, au même instant, avec
le même Qmax. Ce module reconnaît donc une crue par son pic (à `TOLERANCE_PIC` près)
plutôt que par sa date de début — sans jamais réécrire une clé existante en base, donc
sans jamais perdre de résultat déjà calé.

Fonctions PURES (aucune dépendance à ui/ ni à SQLite) : voir tests/
test_appariement_crues.py.
"""

from dataclasses import dataclass, field
from datetime import timedelta

# Deux pics distants de moins que cette tolérance sont considérés comme celui d'une
# MÊME crue. 3 h : couvre les petits écarts observés en pratique (un pic tronqué en bord
# de fenêtre, ex. 341 m³/s à 09:15 dans une fenêtre de 48 h contre 355,7 m³/s à 11:00
# dans une fenêtre de 72 h, soit 1 h 45) sans risquer de confondre deux épisodes
# distincts, toujours séparés de plusieurs jours.
TOLERANCE_PIC = timedelta(hours=3)


def pic_et_metriques(points):
    """Pic (date + débit max), cumul de pluie et bornes d'une série observée archivée.

    `points` : itérable de (datetime, pluie, débit) — même ordre que
    modules.criteres_perf.parse_evenement_serie et results_store.
    charger_serie_observee_complete. Un débit ou une pluie None est ignoré. Retourne
    None si la série ne contient aucun débit exploitable. En cas d'égalité de débit
    maximal (plateau), le PREMIER instant est retenu."""
    points = list(points)
    debits = [(d, q) for d, _p, q in points if q is not None]
    if not debits:
        return None
    date_pic, qmax = debits[0]
    for d, q in debits[1:]:
        if q > qmax:
            date_pic, qmax = d, q
    return {
        "pic_date": date_pic,
        "qmax": qmax,
        "cumul_pluie": sum(p for _d, p, _q in points if p is not None),
        "debut": points[0][0],
        "fin": points[-1][0],
    }


def evenement_par_pic(pic, evenements, tolerance=TOLERANCE_PIC):
    """L'événement de `evenements` (EvenementPerf) dont date_qmax est la plus proche de
    `pic`, à `tolerance` près — None si aucun. `pic` None -> None."""
    if pic is None:
        return None
    candidats = [(abs(e.date_qmax - pic), e.num_evt, e) for e in evenements
                 if e.date_qmax is not None and abs(e.date_qmax - pic) <= tolerance]
    return min(candidats, key=lambda t: (t[0], t[1]))[2] if candidats else None


@dataclass
class BilanReconciliation:
    """Résultat de reconcilier_selection().

    dates_effectives : dates à utiliser réellement pour la campagne (même ordre que la
        sélection) — la date DÉJÀ EN BASE quand la crue y est connue sous une autre date
        de début, sinon la date sélectionnée telle quelle ;
    remplacements   : {date sélectionnée: date déjà en base} pour les crues reconnues
        par leur pic ;
    deja_connues    : dates sélectionnées déjà présentes telles quelles en base ;
    nouvelles       : dates sélectionnées jamais calées (aucun équivalent en base) ;
    sans_pic        : dates sélectionnées absentes de la base ET dont le pic est
        inconnu (absentes du CRITERES_PERF.DAT actuel) — non appariables."""
    dates_effectives: list = field(default_factory=list)
    remplacements: dict = field(default_factory=dict)
    deja_connues: list = field(default_factory=list)
    nouvelles: list = field(default_factory=list)
    sans_pic: list = field(default_factory=list)


def reconcilier_selection(dates_selectionnees, pics_selection, connues,
                          tolerance=TOLERANCE_PIC):
    """Rapproche la sélection de crues (dates de début courantes) de celles DÉJÀ en base.

    `dates_selectionnees` : liste de datetime (date_deb, ordre conservé) ;
    `pics_selection`      : {date_deb: pic (datetime)} pour les crues retrouvées dans le
                            CRITERES_PERF.DAT actuel ;
    `connues`             : {date_deb en base: pic (datetime)} — crues qui ONT des
                            résultats en base (et dont le pic est connu par l'archive).

    Une date sélectionnée déjà connue telle quelle est gardée. Sinon, si une crue connue
    a le même pic (à `tolerance` près, la plus proche l'emporte), c'est SA date qui est
    reprise — jamais l'inverse : les résultats existants ne sont jamais réindexés.
    Chaque date connue n'est attribuée qu'à UNE crue sélectionnée (pas de doublon), par
    écart de pic croissant sur l'ensemble de la sélection."""
    bilan = BilanReconciliation()
    choix = {}
    utilisees = {d for d in dates_selectionnees if d in connues}
    a_apparier = []
    for d in dates_selectionnees:
        if d in connues:
            bilan.deja_connues.append(d)
            choix[d] = d
        elif pics_selection.get(d) is None:
            bilan.sans_pic.append(d)
            choix[d] = d
        else:
            a_apparier.append(d)

    # Attribution GLOBALE par écart de pic croissant (et non dans l'ordre de la sélection) :
    # deux crues au pic voisin ne peuvent pas se « voler » la date en base, la paire la
    # plus proche est toujours servie en premier.
    paires = sorted(
        (abs(pic_connu - pics_selection[d]), rang, date_connue)
        for rang, d in enumerate(a_apparier)
        for date_connue, pic_connu in connues.items()
        if date_connue not in utilisees
        and abs(pic_connu - pics_selection[d]) <= tolerance
    )
    for _ecart, rang, date_connue in paires:
        d = a_apparier[rang]
        if d in choix or date_connue in utilisees:
            continue
        utilisees.add(date_connue)
        bilan.remplacements[d] = date_connue
        choix[d] = date_connue
    for d in a_apparier:
        if d not in choix:
            bilan.nouvelles.append(d)
            choix[d] = d

    bilan.dates_effectives = [choix[d] for d in dates_selectionnees]
    return bilan
