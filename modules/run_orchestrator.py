# -*- coding: utf-8 -*-
"""Orchestrateur de campagne — remplace la boucle magique d'origine.

Boucle sur (horizon × seuil_c1 × méthode) × crue_sélectionnée. Pour chaque combinaison :
écrit LISTE_BASSINS.DAT une seule fois, lance le calage (exe 04), puis rejoue chaque crue
sélectionnée (exe 04 + GRP_PREVISION.BAT corrigé) et extrait dQP/dTP/VE/KGE du PDF produit.

Fiabilisation demandée explicitement par l'utilisateur :
  - chaque exception est capturée AU NIVEAU DE LA COMBINAISON OU DE LA CRUE INDIVIDUELLE
    (jamais globalement) — jamais de `except Exception: pass`/`print` silencieux comme
    dans le script d'origine ;
  - chaque échec est logué avec la stack trace complète ET remonté à l'appelant (callback
    de progression), pour que l'UI affiche un message explicite ;
  - reprise sur échec : `lancer_campagne(..., seulement_echecs=True)` ne relance que les
    combinaisons/crues dont le dernier statut connu est "failed" (ou absentes de la base).
"""

import logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import product
from typing import Callable, Dict, List, Optional, Tuple

from modules import grp_series, results_store
from modules.config_prevision import ConfigPrevisionError, set_prevision
from modules.criteres_perf import CriteresPerfError, parse_criteres_perf, parse_evenement_serie
from modules.fiche_controle_pdf import FicheControleError, extraire_resultat
from modules.grp_paths import GrpPaths
from modules.grp_runner import GrpRunError, nettoyer_bddtr, run_calage, run_prevision_bat
from modules.grp_series import GrpSerieError
from modules.liste_bassins import ListeBassinsFormatError, parse_liste_bassins, set_calage_params, write_liste_bassins

logger = logging.getLogger("grp_2023.orchestrateur")


@dataclass
class ProgressionEvent:
    """Émis à chaque étape franchie, pour que l'UI (onglet Campagne) affiche une
    progression fine et des messages d'erreur explicites en temps réel."""
    horizon: str
    seuil_c1: float
    methode: str
    crue_date: Optional[datetime]
    # "calage"/"rejeu" : étapes de la campagne principale (comptées dans la barre de
    # progression et les compteurs crues_ok/crues_ko — comportement historique
    # inchangé). "rejeu_instant" : instant supplémentaire avant le pic (voir
    # lancer_campagne), journalisé mais volontairement PAS compté dans ces compteurs —
    # purement additif, ne doit jamais faire dévier la progression affichée de ce que
    # results_store (filtré sur instant_label='reference') montrera après coup.
    # "campagne" : événement global (ex. annulation).
    etape: str
    statut: str      # "running" / "success" / "failed" / "annule"
    message: str = ""
    instant_label: str = results_store.INSTANT_REFERENCE


ProgressionCallback = Callable[[ProgressionEvent], None]


def generer_combinaisons(horizons: List[str], seuils: List[float], methodes: List[str]
                          ) -> List[Tuple[str, float, str]]:
    """Produit la matrice de test à partir des sélections de l'onglet Paramétrage
    (bloc 3). Ordre déterministe (utile pour les tests et la lecture des logs)."""
    return sorted(product(horizons, seuils, methodes))


def _combinaisons_a_traiter(conn, combinaisons, crues_dates, seulement_echecs,
                             decalages_pic_heures=None):
    """Filtre la matrice complète si `seulement_echecs` : garde une combinaison si son
    calage n'a pas réussi, OU si au moins une des crues sélectionnées n'a pas encore
    réussi sous cette combinaison (le calage ne sera alors pas relancé — voir
    `calage_deja_ok` dans lancer_campagne — seules les crues manquantes le seront), OU
    s'il lui manque un instant de rejeu supplémentaire actuellement configuré (voir
    `_crues_a_traiter`).

    `decalages_pic_heures` DOIT être transmis ici, pas seulement à l'appel de
    `_crues_a_traiter` fait plus loin dans `lancer_campagne` : sans lui, cette fonction
    conclut à tort qu'une combinaison déjà entièrement réussie (référence) n'a rien à
    faire, et l'exclut de `combinaisons_a_faire` AVANT même d'atteindre le second appel
    — les instants supplémentaires ne seraient alors jamais rejoués (bug constaté en
    conditions réelles : "Compléter la campagne" terminait instantanément sans qu'aucun
    instant ne soit traité)."""
    if not seulement_echecs:
        return combinaisons
    a_traiter = []
    for horizon, seuil_c1, methode in combinaisons:
        row = conn.execute(
            "SELECT id, statut FROM combinaisons WHERE horizon = ? AND seuil_c1 = ? AND methode = ?",
            (horizon, seuil_c1, methode),
        ).fetchone()
        if row is None or row["statut"] != "success":
            a_traiter.append((horizon, seuil_c1, methode))
            continue
        if _crues_a_traiter(conn, row["id"], crues_dates, seulement_echecs=True,
                             decalages_pic_heures=decalages_pic_heures):
            a_traiter.append((horizon, seuil_c1, methode))
    return a_traiter


def _serie_observee_complete(paths, pas_de_temps, crue_date):
    """Série observée COMPLÈTE de la crue (toute la fenêtre détectée par GRP, avant ET
    après son pic), pour recalculer dQP/dTP en repli quand le PDF ne les a pas reportés
    (voir _recalculer_dqp_dtp ci-dessous) — À NE JAMAIS CONFONDRE avec la série archivée
    sous type='obs' dans series_archivees (GRP*Obs.txt) : celle-ci ne couvre que la
    fenêtre AVANT l'instant de rejeu (l'historique fourni en entrée du modèle), jamais
    le pic lui-même si le rejeu est positionné avant — constaté en conditions réelles en
    comparant les deux, un premier essai de recalcul basé dessus donnait des dQP
    complètement aberrants (des centaines de %) faute de couvrir le bon intervalle.

    Best-effort : None si l'événement ou son fichier EVxxxx.DAT est introuvable —
    jamais une erreur bloquante pour un simple repli de calcul."""
    try:
        evenements = parse_criteres_perf(paths.criteres_perf_dat(pas_de_temps))
    except (FileNotFoundError, CriteresPerfError):
        return None
    evt = next((e for e in evenements if e.date_deb == crue_date), None)
    if evt is None:
        return None
    chemin = os.path.join(paths.evenements_dir(pas_de_temps),
                           f"{paths.code_site}-EV{evt.num_evt:04d}.DAT")
    try:
        return parse_evenement_serie(chemin)
    except (FileNotFoundError, CriteresPerfError):
        return None


def _recalculer_dqp_dtp(serie_obs_complete, serie_sim):
    """Repli quand GRP n'a pas reporté dQP/dTP dans le PDF pour cette crue (cellule
    vide sur la page 2 — voir modules.fiche_controle_pdf) alors que le rejeu a par
    ailleurs réussi : recalcule directement depuis les séries RÉELLES plutôt que de
    laisser un None silencieux. Mêmes définitions que le manuel GRP (§2.4.10, déjà
    utilisées par l'extraction PDF) : dQP = (QPsim − QPobs) / QPobs × 100 (%), dTP =
    (tQPsim − tQPobs) exprimé en nombre de pas de temps — déduit de l'écart entre 2
    points consécutifs de la série observée (jamais d'une valeur nominale de config),
    pour rester robuste à un pas de temps qui varierait.

    `serie_obs_complete` : voir _serie_observee_complete ci-dessus (PAS la série
    archivée type='obs', qui ne couvre pas le pic). `serie_sim` : série simulée
    archivée (date, débit, pluie), voir modules.grp_series.parser_previsions.

    Validé par comparaison directe avec des dizaines de résultats déjà extraits du PDF
    sur la base réelle de l'utilisateur : concordance à la précision d'arrondi près
    (dTP identique à l'unité près, dQP à ~0.05 point de % près). Retourne (dqp, dtp),
    chacun None si non calculable (série trop courte, QPobs nul)."""
    if len(serie_obs_complete) < 2 or not serie_sim:
        return None, None
    qp_obs, t_obs = max((qobs, d) for d, _pobs, qobs in serie_obs_complete)
    qp_sim, t_sim = max((debit, d) for d, debit, _pluie in serie_sim)
    if not qp_obs:
        return None, None
    dqp = (qp_sim - qp_obs) / qp_obs * 100
    intervalle_s = (serie_obs_complete[1][0] - serie_obs_complete[0][0]).total_seconds()
    dtp = round((t_sim - t_obs).total_seconds() / intervalle_s) if intervalle_s > 0 else None
    return dqp, dtp


def _calage_deja_charge(paths, horizon, seuil_c1, methode):
    """Vérifie que LISTE_BASSINS.DAT reflète PHYSIQUEMENT la combinaison demandée —
    condition supplémentaire à la présence de config_prevision.ini (voir docstring de
    calage_deja_ok ci-dessous), indispensable pour corriger un bug réel constaté sur
    plusieurs campagnes "Compléter la campagne" : le dossier BDTR est UNIQUE et PARTAGÉ
    par toutes les combinaisons d'une campagne, traitées l'une après l'autre. Sauter le
    calage d'une combinaison au seul motif qu'elle a réussi PAR LE PASSÉ (statut
    "success" en base) ne garantit en rien que le calage ACTUELLEMENT chargé en BDTR
    est bien le sien : il peut très bien s'agir de celui d'une AUTRE combinaison déjà
    traitée juste avant, dans cette même reprise, qui n'a jamais eu besoin d'être
    recalée non plus. Plusieurs combinaisons horizon/seuil différentes se sont ainsi
    retrouvées à rejouer EXACTEMENT le même hydrogramme simulé (mêmes dQP/dTP/VE/KGE) —
    on vérifie donc en plus que la ligne LISTE_BASSINS.DAT du site correspond bien, en
    l'état, à l'horizon/seuil/méthode qu'on s'apprête à traiter."""
    try:
        _lignes_brutes, bassins = parse_liste_bassins(paths.liste_bassins_dat)
    except (ListeBassinsFormatError, FileNotFoundError):
        return False
    ligne = bassins.get(paths.code_site)
    if ligne is None:
        return False
    try:
        seuil_charge = float(ligne.seuil_c1)
    except ValueError:
        return False
    return (ligne.hor1 == horizon and seuil_charge == float(seuil_c1)
            and ligne.methode_active == methode)


def _crues_a_traiter(conn, combinaison_id, crues_dates, seulement_echecs,
                      decalages_pic_heures=None):
    """`decalages_pic_heures` (optionnel) : une crue dont le rejeu de référence a déjà
    réussi est quand même reprise si un des instants supplémentaires actuellement
    configurés (voir Paramétrage > "Instants de rejeu supplémentaires") lui manque
    encore — permet d'ajouter des instants à une campagne déjà entièrement réussie
    sans passer par une reprise sur échec classique (qui ne trouverait sinon rien à
    refaire, puisque la référence de chaque crue est déjà un succès). Ne s'applique
    qu'en mode reprise (`seulement_echecs=True`) : un lancement complet retraite de
    toute façon tout, référence et instants supplémentaires inclus."""
    if not seulement_echecs:
        return crues_dates
    labels_attendus = {f"H-{h:g}" for h in (decalages_pic_heures or [])}
    a_refaire = []
    for crue_date in crues_dates:
        crue_iso = crue_date.isoformat()
        # instant_label='reference' : sans ce filtre, une ligne d'instant supplémentaire
        # pourrait être retournée par fetchone() à la place de la référence (même bug de
        # principe que list_resultats_avec_combinaison, voir modules.results_store).
        row = conn.execute(
            "SELECT statut FROM resultats_crues "
            "WHERE combinaison_id = ? AND crue_date = ? AND instant_label = 'reference'",
            (combinaison_id, crue_iso),
        ).fetchone()
        if row is None or row["statut"] != "success":
            a_refaire.append(crue_date)
            continue
        if labels_attendus:
            labels_ok = {r["instant_label"] for r in conn.execute(
                "SELECT instant_label FROM resultats_crues "
                "WHERE combinaison_id = ? AND crue_date = ? AND statut = 'success'",
                (combinaison_id, crue_iso),
            ).fetchall()}
            if not labels_attendus <= labels_ok:
                a_refaire.append(crue_date)
    return a_refaire


def lancer_campagne(paths: GrpPaths, pas_de_temps: str,
                     combinaisons: List[Tuple[str, float, str]],
                     crues_dates: List[datetime],
                     db_path: Optional[str] = None,
                     callback: Optional[ProgressionCallback] = None,
                     seulement_echecs: bool = False,
                     annulation=None,
                     decalages_pic_heures: Optional[List[float]] = None,
                     dates_qmax: Optional[Dict[datetime, datetime]] = None):
    """Lance la campagne complète. N'interrompt jamais la boucle sur une erreur
    individuelle : chaque combinaison/crue en échec est loguée et signalée, la campagne
    continue avec la suivante — c'est à l'utilisateur de décider, une fois la campagne
    terminée, s'il relance les échecs (`seulement_echecs=True`).

    `annulation` (threading.Event optionnel) : vérifié entre chaque étape (calage ou
    rejeu d'une crue) — s'il est activé, la campagne s'arrête proprement à la prochaine
    étape (jamais en cours d'exécution d'un exécutable GRP, pour ne pas laisser un run
    interrompu produire des fichiers à moitié écrits). Les combinaisons/crues non
    encore traitées restent au statut où elles étaient (pending, ou success si déjà
    faites lors d'un passage précédent) — une reprise ultérieure les reprendra
    normalement.

    `decalages_pic_heures` (ex. [24, 12, 6]) : pour chaque crue dont le rejeu de
    référence a réussi, rejoue EN PLUS à chaque instant (pic réel de la crue − N
    heures) — sans reprendre le calage (même BDTR déjà calibrée, voir
    _calage_deja_charge), pour visualiser comment le comportement du modèle évolue
    selon qu'il démarre bien en amont du pic ou en pleine montée de crue (demande
    explicite de l'utilisateur, 27/08/2026). `dates_qmax` associe chaque `crue_date`
    (date de début, la clé habituelle) à l'horodatage réel de son pic (`DateQmax` de
    CRITERES_PERF.DAT) — une crue absente de ce dict voit ses instants supplémentaires
    ignorés (avec un avertissement logué), jamais une erreur bloquante. Ces rejeux
    supplémentaires sont PUREMENT ADDITIFS : stockés sous un `instant_label` distinct
    ('reference' reste inchangé), jamais comptés dans la reprise sur échec, les badges
    de couverture ni le score composite (voir modules.results_store), et notifiés via
    l'étape "rejeu_instant" plutôt que "rejeu" pour ne pas fausser la barre de
    progression de la campagne principale.
    """
    results_store.init_db(db_path)

    def _annule():
        return annulation is not None and annulation.is_set()

    def _notifier(evt: ProgressionEvent):
        logger.info("%s | %s x %s (%s) crue=%s : %s — %s",
                    evt.etape, evt.horizon, evt.seuil_c1, evt.methode, evt.crue_date,
                    evt.statut, evt.message)
        if callback:
            callback(evt)

    def _nettoyer_bddtr_final():
        """Supprime le dossier BDDTR de travail — confirmé sans risque par l'utilisateur
        (entièrement régénéré par l'exe 04 à chaque calage). Appelé à toute sortie de
        cette fonction (fin normale, annulation) : les résultats déjà obtenus sont de
        toute façon persistés en base avant cet appel, un échec de nettoyage ne doit pas
        faire perdre la campagne — juste être signalé."""
        try:
            nettoyer_bddtr(paths.dossier_bddtr)
        except GrpRunError as e:
            logger.error("Nettoyage final de %s échoué : %s", paths.dossier_bddtr, e)
            _notifier(ProgressionEvent("", 0.0, "", None, "campagne", "failed",
                                        f"Nettoyage final du dossier BDTR échoué : {e}"))

    with results_store.db_session(db_path) as conn:
        combinaisons_a_faire = _combinaisons_a_traiter(conn, combinaisons, crues_dates,
                                                        seulement_echecs, decalages_pic_heures)

    logger.info("=== Début de campagne : %s combinaison(s) à traiter sur %s au total, "
                "%s crue(s), reprise_echecs=%s ===",
                len(combinaisons_a_faire), len(combinaisons), len(crues_dates), seulement_echecs)

    for horizon, seuil_c1, methode in combinaisons_a_faire:
        if _annule():
            _notifier(ProgressionEvent(horizon, seuil_c1, methode, None, "campagne",
                                        "annule", "Campagne annulée par l'utilisateur."))
            _nettoyer_bddtr_final()
            return
        with results_store.db_session(db_path) as conn:
            row_existant = conn.execute(
                "SELECT id, statut FROM combinaisons WHERE horizon = ? AND seuil_c1 = ? AND methode = ?",
                (horizon, seuil_c1, methode),
            ).fetchone()

        # En reprise, si le calage de cette combinaison a déjà réussi précédemment, on ne
        # le relance pas (inutile, potentiellement long) : seules les crues manquantes le
        # seront ci-dessous (_combinaisons_a_traiter n'a gardé cette combinaison que parce
        # qu'il reste au moins une crue non réussie).
        #
        # ⚠️ Constaté en conditions réelles : ce statut "success" en base ne garantit PAS
        # que le dossier BDTR est toujours dans l'état laissé par ce calage — le nettoyage
        # de fin de campagne (nettoyer_bddtr, voir plus bas) vide ENTIÈREMENT ce dossier,
        # y compris config_prevision.ini, à chaque fin de campagne (succès ou annulation),
        # qu'elle date d'une session précédente ou de la précédente reprise. Si on
        # relance ensuite "Relancer les échecs" sans jamais avoir refait tourner le
        # calage entretemps, config_prevision.ini n'existe plus et TOUS les rejeux de la
        # combinaison échouent immédiatement ("fichier introuvable"). On vérifie donc en
        # plus la présence physique du fichier — le statut en base seul ne suffit pas.
        #
        # ⚠️ Deuxième garde-fou, ajouté suite à un bug réel (plusieurs horizons/seuils
        # produisant EXACTEMENT le même hydrogramme simulé après un "Compléter la
        # campagne") : même config_prevision.ini présent, encore faut-il que
        # LISTE_BASSINS.DAT charge bien CETTE combinaison précise, et pas celle d'une
        # autre combinaison traitée juste avant dans la même reprise — voir
        # _calage_deja_charge.
        calage_deja_ok = (
            seulement_echecs and row_existant is not None and row_existant["statut"] == "success"
            and os.path.isfile(paths.config_prevision_ini)
            and _calage_deja_charge(paths, horizon, seuil_c1, methode)
        )

        if calage_deja_ok:
            combinaison_id = row_existant["id"]
            _notifier(ProgressionEvent(horizon, seuil_c1, methode, None, "calage", "success",
                                        "calage déjà réussi précédemment, non relancé"))
        else:
            with results_store.db_session(db_path) as conn:
                combinaison_id = results_store.upsert_combinaison(
                    conn, horizon, seuil_c1, methode, statut="running")

            _notifier(ProgressionEvent(horizon, seuil_c1, methode, None, "calage", "running"))
            try:
                _lignes_brutes, bassins = parse_liste_bassins(paths.liste_bassins_dat)
                code = paths.code_site
                if code not in bassins:
                    raise GrpRunError(
                        f"Code site {code!r} absent de {paths.liste_bassins_dat} — "
                        "vérifiez la configuration (onglet Configuration)."
                    )
                set_calage_params(bassins[code], hor1=horizon, seuil_c1=seuil_c1, methode=methode)
                write_liste_bassins(paths.liste_bassins_dat, _lignes_brutes, bassins)
                run_calage(paths.exe_calage_bddtr)
            except (ListeBassinsFormatError, GrpRunError, FileNotFoundError) as e:
                message = f"Échec du calage : {e}"
                logger.error("Combinaison %s/%s/%s — %s\n%s", horizon, seuil_c1, methode,
                             message, traceback.format_exc())
                with results_store.db_session(db_path) as conn:
                    results_store.set_statut_combinaison(conn, combinaison_id, "failed", str(e))
                _notifier(ProgressionEvent(horizon, seuil_c1, methode, None, "calage", "failed", message))
                continue  # combinaison suivante — jamais d'arrêt complet de la campagne
            else:
                with results_store.db_session(db_path) as conn:
                    results_store.set_statut_combinaison(conn, combinaison_id, "success")
                _notifier(ProgressionEvent(horizon, seuil_c1, methode, None, "calage", "success"))

        with results_store.db_session(db_path) as conn:
            crues_a_faire = _crues_a_traiter(conn, combinaison_id, crues_dates, seulement_echecs,
                                              decalages_pic_heures)

        def _rejeu_instant(crue_date, instant_label, instpr, etape_notif):
            """Un rejeu unique à l'instant `instpr`, archivé sous `instant_label`.
            Réutilisée pour l'instant de référence ET les instants supplémentaires
            avant le pic — même pipeline (set_prevision -> .bat -> extraction PDF ->
            persistance), seul l'instant et l'étiquette de stockage changent. Retourne
            True/False (succès), jamais levée : chaque échec est capturé et notifié
            individuellement, comme pour le reste de l'orchestrateur."""
            _notifier(ProgressionEvent(horizon, seuil_c1, methode, crue_date, etape_notif,
                                        "running", instant_label=instant_label))
            try:
                set_prevision(paths.config_prevision_ini, instpr=instpr)
                chemin_pdf = run_prevision_bat(paths.grp_prevision_bat, paths.fiches_controle_dir)
                resultat = extraire_resultat(chemin_pdf)
            except (ConfigPrevisionError, GrpRunError, FicheControleError, FileNotFoundError) as e:
                message = str(e)
                logger.error("Crue %s (instant %s) sous %s/%s/%s — %s\n%s", crue_date,
                             instant_label, horizon, seuil_c1, methode, message,
                             traceback.format_exc())
                with results_store.db_session(db_path) as conn:
                    results_store.upsert_resultat_crue(
                        conn, combinaison_id, crue_date, statut="failed", erreur=message,
                        instant_label=instant_label)
                _notifier(ProgressionEvent(horizon, seuil_c1, methode, crue_date, etape_notif,
                                            "failed", message, instant_label=instant_label))
                return False

            # Archivage best-effort des séries observée/simulée pour le dashboard (bloc 6
            # > Détail par crue) — <BDDTR>/Temps_Reel/Sorties/ n'expose que le DERNIER
            # rejeu effectué, donc sans cet archivage immédiat la série serait perdue dès
            # le rejeu suivant (crue ou instant). Fait AVANT la persistance du résultat
            # (contrairement à avant) : la série simulée "sim" sert aussi de repli pour
            # recalculer dQP/dTP juste en dessous quand le PDF ne les a pas reportés.
            erreur_series = None
            try:
                obs = grp_series.parser_observations(paths.sorties_dir)
                sim = grp_series.parser_previsions(paths.sorties_dir)
            except (FileNotFoundError, GrpSerieError) as e:
                obs, sim = [], []
                erreur_series = str(e)

            # Repli quand GRP n'a pas reporté dQP/dTP dans le PDF pour cette crue
            # (cellule vide sur la page 2 — voir modules.fiche_controle_pdf, cas
            # constaté en conditions réelles, distinct d'un échec d'extraction) : la
            # crue reste un rejeu RÉUSSI (VE/KGE, indicateurs globaux non liés à un
            # seul instant de pic, restent d'ailleurs généralement disponibles), mais
            # ni dQP ni dTP n'étaient auparavant reportés nulle part — jamais signalé,
            # ni à l'utilisateur ni dans les logs (demandé explicitement : plus jamais
            # de valeur manquante silencieuse). Recalculée directement depuis les
            # séries RÉELLES (observée complète + simulée) quand c'est possible, avec
            # la provenance toujours indiquée explicitement (jamais confondu avec une
            # valeur extraite du PDF) — voir _recalculer_dqp_dtp pour la validation.
            dqp, dtp = resultat.dqp, resultat.dtp
            recalcules = []
            if (dqp is None or dtp is None) and sim:
                serie_obs_complete = _serie_observee_complete(paths, pas_de_temps, crue_date)
                if serie_obs_complete:
                    dqp_calc, dtp_calc = _recalculer_dqp_dtp(serie_obs_complete, sim)
                    if dqp is None and dqp_calc is not None:
                        dqp, recalcules = dqp_calc, recalcules + ["dQP"]
                    if dtp is None and dtp_calc is not None:
                        dtp, recalcules = dtp_calc, recalcules + ["dTP"]

            note = None
            if dqp is None or dtp is None:
                indicateurs_manquants = [n for n, v in (("dQP", dqp), ("dTP", dtp)) if v is None]
                note = (f"{'/'.join(indicateurs_manquants)} non fourni(s) par le PDF pour cette "
                        "crue (cellule vide) et non recalculable(s) depuis les séries")
                logger.warning("Crue %s (instant %s) sous %s/%s/%s : %s",
                                crue_date, instant_label, horizon, seuil_c1, methode, note)
            elif recalcules:
                note = f"{'/'.join(recalcules)} recalculé(s) depuis les séries (non fourni par le PDF)"

            with results_store.db_session(db_path) as conn:
                results_store.upsert_resultat_crue(
                    conn, combinaison_id, crue_date, statut="success",
                    dqp=dqp, dtp=dtp, ve=resultat.ve, kge=resultat.kge,
                    suspects=resultat.suspects, erreur=note, instant_label=instant_label,
                )

            message_parts = []
            if resultat.est_suspect:
                message_parts.append("suspect (hors bornes plausibles)")
            if note:
                message_parts.append(note)

            if erreur_series:
                logger.warning("Archivage des séries impossible pour crue %s (instant %s) "
                                "sous %s/%s/%s : %s", crue_date, instant_label, horizon,
                                seuil_c1, methode, erreur_series)
                message_parts.append(f"série non archivée : {erreur_series}")
            else:
                with results_store.db_session(db_path) as conn:
                    results_store.archiver_serie(conn, combinaison_id, crue_date, "obs", obs)
                    results_store.archiver_serie(conn, combinaison_id, crue_date, "sim", sim,
                                                  instant_label=instant_label)

            _notifier(ProgressionEvent(horizon, seuil_c1, methode, crue_date, etape_notif,
                                        "success", " — ".join(message_parts),
                                        instant_label=instant_label))
            return True

        for crue_date in crues_a_faire:
            if _annule():
                _notifier(ProgressionEvent(horizon, seuil_c1, methode, None, "campagne",
                                            "annule", "Campagne annulée par l'utilisateur."))
                _nettoyer_bddtr_final()
                return

            # La référence n'est reprise que si elle n'a pas déjà réussi — une crue peut
            # figurer dans crues_a_faire uniquement parce qu'il lui manque un instant
            # supplémentaire (voir _crues_a_traiter), auquel cas refaire la référence
            # serait un travail inutile déjà acquis. Silencieux dans ce cas (aucun
            # évènement "rejeu" émis) : même principe qu'une crue absente de
            # crues_a_faire aujourd'hui, pour ne jamais compter deux fois le même
            # succès dans les compteurs crues_ok de l'interface.
            with results_store.db_session(db_path) as conn:
                ligne_reference = conn.execute(
                    "SELECT statut FROM resultats_crues "
                    "WHERE combinaison_id = ? AND crue_date = ? AND instant_label = ?",
                    (combinaison_id, crue_date.isoformat(), results_store.INSTANT_REFERENCE),
                ).fetchone()
            reference_deja_ok = ligne_reference is not None and ligne_reference["statut"] == "success"

            if reference_deja_ok:
                succes_reference = True
            else:
                succes_reference = _rejeu_instant(
                    crue_date, results_store.INSTANT_REFERENCE, crue_date, "rejeu")

            if succes_reference and decalages_pic_heures:
                date_qmax = (dates_qmax or {}).get(crue_date)
                if date_qmax is None:
                    logger.warning(
                        "Pic (DateQmax) inconnu pour la crue %s — instants supplémentaires "
                        "avant le pic ignorés pour %s/%s/%s.",
                        crue_date, horizon, seuil_c1, methode)
                    continue

                with results_store.db_session(db_path) as conn:
                    labels_deja_ok = {r["instant_label"] for r in conn.execute(
                        "SELECT instant_label FROM resultats_crues "
                        "WHERE combinaison_id = ? AND crue_date = ? AND statut = 'success'",
                        (combinaison_id, crue_date.isoformat()),
                    ).fetchall()}

                for decalage_h in decalages_pic_heures:
                    if _annule():
                        _notifier(ProgressionEvent(horizon, seuil_c1, methode, None,
                                                    "campagne", "annule",
                                                    "Campagne annulée par l'utilisateur."))
                        _nettoyer_bddtr_final()
                        return
                    instant_label = f"H-{decalage_h:g}"
                    if instant_label in labels_deja_ok:
                        continue  # déjà réussi précédemment, jamais refait inutilement
                    _rejeu_instant(crue_date, instant_label,
                                    date_qmax - timedelta(hours=decalage_h), "rejeu_instant")

    logger.info("=== Fin de campagne : %s combinaison(s) traitée(s) ===", len(combinaisons_a_faire))
    _nettoyer_bddtr_final()
