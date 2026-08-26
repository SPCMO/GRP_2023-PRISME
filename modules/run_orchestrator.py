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
from datetime import datetime
from itertools import product
from typing import Callable, List, Optional, Tuple

from modules import grp_series, results_store
from modules.config_prevision import ConfigPrevisionError, set_prevision
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
    etape: str      # "calage", "rejeu" ou "campagne" (événement global, ex. annulation)
    statut: str      # "running" / "success" / "failed" / "annule"
    message: str = ""


ProgressionCallback = Callable[[ProgressionEvent], None]


def generer_combinaisons(horizons: List[str], seuils: List[float], methodes: List[str]
                          ) -> List[Tuple[str, float, str]]:
    """Produit la matrice de test à partir des sélections de l'onglet Paramétrage
    (bloc 3). Ordre déterministe (utile pour les tests et la lecture des logs)."""
    return sorted(product(horizons, seuils, methodes))


def _combinaisons_a_traiter(conn, combinaisons, crues_dates, seulement_echecs):
    """Filtre la matrice complète si `seulement_echecs` : garde une combinaison si son
    calage n'a pas réussi, OU si au moins une des crues sélectionnées n'a pas encore
    réussi sous cette combinaison (le calage ne sera alors pas relancé — voir
    `calage_deja_ok` dans lancer_campagne — seules les crues manquantes le seront)."""
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
        if _crues_a_traiter(conn, row["id"], crues_dates, seulement_echecs=True):
            a_traiter.append((horizon, seuil_c1, methode))
    return a_traiter


def _crues_a_traiter(conn, combinaison_id, crues_dates, seulement_echecs):
    if not seulement_echecs:
        return crues_dates
    a_refaire = []
    for crue_date in crues_dates:
        row = conn.execute(
            "SELECT statut FROM resultats_crues WHERE combinaison_id = ? AND crue_date = ?",
            (combinaison_id, crue_date.isoformat()),
        ).fetchone()
        if row is None or row["statut"] != "success":
            a_refaire.append(crue_date)
    return a_refaire


def lancer_campagne(paths: GrpPaths, pas_de_temps: str,
                     combinaisons: List[Tuple[str, float, str]],
                     crues_dates: List[datetime],
                     db_path: Optional[str] = None,
                     callback: Optional[ProgressionCallback] = None,
                     seulement_echecs: bool = False,
                     annulation=None):
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
                                                        seulement_echecs)

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
        calage_deja_ok = (
            seulement_echecs and row_existant is not None and row_existant["statut"] == "success"
            and os.path.isfile(paths.config_prevision_ini)
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
            crues_a_faire = _crues_a_traiter(conn, combinaison_id, crues_dates, seulement_echecs)

        for crue_date in crues_a_faire:
            if _annule():
                _notifier(ProgressionEvent(horizon, seuil_c1, methode, None, "campagne",
                                            "annule", "Campagne annulée par l'utilisateur."))
                _nettoyer_bddtr_final()
                return
            _notifier(ProgressionEvent(horizon, seuil_c1, methode, crue_date, "rejeu", "running"))
            try:
                set_prevision(paths.config_prevision_ini, instpr=crue_date)
                chemin_pdf = run_prevision_bat(paths.grp_prevision_bat, paths.fiches_controle_dir)
                resultat = extraire_resultat(chemin_pdf)
            except (ConfigPrevisionError, GrpRunError, FicheControleError, FileNotFoundError) as e:
                message = str(e)
                logger.error("Crue %s sous %s/%s/%s — %s\n%s", crue_date, horizon, seuil_c1,
                             methode, message, traceback.format_exc())
                with results_store.db_session(db_path) as conn:
                    results_store.upsert_resultat_crue(
                        conn, combinaison_id, crue_date, statut="failed", erreur=message)
                _notifier(ProgressionEvent(horizon, seuil_c1, methode, crue_date, "rejeu",
                                            "failed", message))
                continue  # crue suivante — jamais d'arrêt complet de la combinaison

            with results_store.db_session(db_path) as conn:
                results_store.upsert_resultat_crue(
                    conn, combinaison_id, crue_date, statut="success",
                    dqp=resultat.dqp, dtp=resultat.dtp, ve=resultat.ve, kge=resultat.kge,
                    suspects=resultat.suspects,
                )
            message = "suspect (hors bornes plausibles)" if resultat.est_suspect else ""

            # Archivage best-effort des séries observée/simulée pour le dashboard (bloc 6
            # > Détail par crue) — <BDDTR>/Temps_Reel/Sorties/ n'expose que le DERNIER
            # rejeu effectué, donc sans cet archivage immédiat la série serait perdue dès
            # la crue suivante. Un échec ici ne remet pas en cause le résultat dQP/dTP/VE/
            # KGE déjà obtenu et persisté ci-dessus (source primaire des résultats) — la
            # crue reste "success", juste sans courbe simulée disponible dans le dashboard.
            try:
                obs = grp_series.parser_observations(paths.sorties_dir)
                sim = grp_series.parser_previsions(paths.sorties_dir)
                with results_store.db_session(db_path) as conn:
                    results_store.archiver_serie(conn, combinaison_id, crue_date, "obs", obs)
                    results_store.archiver_serie(conn, combinaison_id, crue_date, "sim", sim)
            except (FileNotFoundError, GrpSerieError) as e:
                logger.warning("Archivage des séries observée/simulée impossible pour crue %s "
                                "sous %s/%s/%s : %s", crue_date, horizon, seuil_c1, methode, e)
                message = (message + " — " if message else "") + f"série non archivée : {e}"

            _notifier(ProgressionEvent(horizon, seuil_c1, methode, crue_date, "rejeu",
                                        "success", message))

    logger.info("=== Fin de campagne : %s combinaison(s) traitée(s) ===", len(combinaisons_a_faire))
    _nettoyer_bddtr_final()
