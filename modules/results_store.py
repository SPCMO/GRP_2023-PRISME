# -*- coding: utf-8 -*-
"""Persistance SQLite des résultats de campagne (data/runs.sqlite3).

Trois niveaux, qui reflètent le déroulement réel d'une campagne (voir
modules.run_orchestrator) :
  - `combinaisons` : une ligne par (horizon, seuil_c1, méthode) — le calage (exe 04)
    n'est lancé qu'UNE fois par combinaison, réutilisé pour toutes les crues rejouées.
  - `resultats_crues` : une ligne par (combinaison, crue rejouée) — statut et indicateurs
    dQP/dTP/VE/KGE du rejeu opérationnel de cette crue précise sous cette combinaison.
  - `series_archivees` : les points (date, débit, pluie) des séries observée et simulée
    d'un rejeu (voir modules.grp_series), archivés au moment du run — car
    <BDDTR>/Temps_Reel/Sorties/ n'expose que le dernier rejeu effectué, jamais assez pour
    retrouver après coup la simulation d'une combinaison/crue précise dans le dashboard.

Cette double granularité (combinaisons/résultats) permet la reprise sur échec demandée
par l'utilisateur : si le calage d'une combinaison échoue, aucune crue n'est tentée sous
cette combinaison ; si le calage réussit mais qu'une seule crue échoue (ex. .bat qui
plante), on peut relancer uniquement cette crue sans refaire le calage.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from statistics import median

import config as app_config
from modules import config_manager

logger = logging.getLogger("grp_2023.results_store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS combinaisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    horizon TEXT NOT NULL,
    seuil_c1 REAL NOT NULL,
    methode TEXT NOT NULL CHECK (methode IN ('T', 'R')),
    statut TEXT NOT NULL DEFAULT 'pending'
        CHECK (statut IN ('pending', 'running', 'success', 'failed')),
    erreur TEXT,
    date_maj TEXT NOT NULL,
    UNIQUE (horizon, seuil_c1, methode)
);

CREATE TABLE IF NOT EXISTS resultats_crues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combinaison_id INTEGER NOT NULL REFERENCES combinaisons(id) ON DELETE CASCADE,
    crue_date TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'pending'
        CHECK (statut IN ('pending', 'running', 'success', 'failed')),
    dqp REAL, dtp REAL, ve REAL, kge REAL,
    suspects TEXT,        -- indicateurs hors bornes plausibles, séparés par des virgules
    erreur TEXT,
    date_maj TEXT NOT NULL,
    UNIQUE (combinaison_id, crue_date)
);

CREATE TABLE IF NOT EXISTS series_archivees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combinaison_id INTEGER NOT NULL REFERENCES combinaisons(id) ON DELETE CASCADE,
    crue_date TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('obs', 'sim')),
    point_date TEXT NOT NULL,
    debit REAL,
    pluie REAL
);
CREATE INDEX IF NOT EXISTS idx_series_archivees
    ON series_archivees(combinaison_id, crue_date, type);
"""


def _chemin_db_par_defaut():
    """Un fichier `data/runs_<code_station>.sqlite3` distinct par station configurée,
    plutôt qu'un unique `data/runs.sqlite3` partagé par toutes les stations.

    Avant cette fonction, la table `combinaisons` n'a aucune colonne station — elle est
    identifiée uniquement par (horizon, seuil_c1, méthode), avec une contrainte UNIQUE
    sur ce triplet. Reconfigurer l'outil sur une autre station (ex. Trébès après
    Moussoulens) puis lancer une campagne avec un horizon/seuil/méthode déjà testé
    aurait réutilisé silencieusement la ligne existante de l'ancienne station
    (`ON CONFLICT ... DO UPDATE`, voir upsert_combinaison) — mélangeant les crues des 2
    stations sous le même identifiant de combinaison, sans erreur ni avertissement.

    Le `code_station` (identifiant hydrologique stable, 1 lettre + 9 chiffres saisi une
    fois dans l'onglet Configuration) est utilisé plutôt que le nom libre de la
    station : deux orthographes du même nom ("Trebes"/"Trèbes"/"Trebe") ne doivent
    jamais produire 2 bases différentes pour une seule et même station, ni l'inverse.

    Si la configuration est illisible ou que la station n'est pas encore renseignée, on
    retombe sur l'ancien nom générique `runs.sqlite3` — comportement historique,
    jamais bloquant."""
    try:
        config_data = config_manager.load_config()
    except (FileNotFoundError, ValueError):
        return app_config.DB_PATH
    code_station = (config_data.get("station", {}).get("code_station") or "").strip()
    if not code_station:
        return app_config.DB_PATH
    chemin_station = os.path.join(app_config.DATA_DIR, f"runs_{code_station}.sqlite3")
    _migrer_ancienne_base_partagee_si_necessaire(chemin_station)
    return chemin_station


def _migrer_ancienne_base_partagee_si_necessaire(chemin_station):
    """Migration ponctuelle, exécutée au plus une seule fois en tout et pour tout :
    tant que l'ancien fichier partagé `runs.sqlite3` existe encore ET qu'aucune base
    dédiée n'a déjà été créée pour la station actuellement configurée, on le renomme
    vers cette base dédiée plutôt que de laisser croire à une perte de données. Une
    fois ce renommage effectué, `runs.sqlite3` n'existe plus : aucune autre station
    configurée ultérieurement ne pourra plus jamais hériter par erreur de cet
    historique — la migration ne peut donc profiter qu'à la toute première station
    active après la mise à jour de l'outil (typiquement celle déjà en cours d'usage)."""
    if os.path.exists(app_config.DB_PATH) and not os.path.exists(chemin_station):
        os.makedirs(os.path.dirname(chemin_station), exist_ok=True)
        os.replace(app_config.DB_PATH, chemin_station)
        logger.info("Migration de l'ancienne base partagée %s vers %s (1 fois, station "
                    "active au moment de la migration)", app_config.DB_PATH, chemin_station)


def init_db(db_path=None):
    db_path = db_path or _chemin_db_par_defaut()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db_session(db_path=None):
    """Context manager : commit automatique en sortie normale, rollback si exception."""
    db_path = db_path or _chemin_db_par_defaut()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _horodatage():
    return datetime.now().isoformat(timespec="seconds")


def upsert_combinaison(conn, horizon, seuil_c1, methode, statut="pending", erreur=None):
    """Crée ou remet à jour une combinaison, retourne son id."""
    conn.execute(
        """
        INSERT INTO combinaisons (horizon, seuil_c1, methode, statut, erreur, date_maj)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(horizon, seuil_c1, methode) DO UPDATE SET
            statut = excluded.statut, erreur = excluded.erreur, date_maj = excluded.date_maj
        """,
        (horizon, seuil_c1, methode, statut, erreur, _horodatage()),
    )
    row = conn.execute(
        "SELECT id FROM combinaisons WHERE horizon = ? AND seuil_c1 = ? AND methode = ?",
        (horizon, seuil_c1, methode),
    ).fetchone()
    return row["id"]


def set_statut_combinaison(conn, combinaison_id, statut, erreur=None):
    conn.execute(
        "UPDATE combinaisons SET statut = ?, erreur = ?, date_maj = ? WHERE id = ?",
        (statut, erreur, _horodatage(), combinaison_id),
    )


def upsert_resultat_crue(conn, combinaison_id, crue_date, statut, dqp=None, dtp=None,
                          ve=None, kge=None, suspects=None, erreur=None):
    """Crée ou remet à jour le résultat d'une crue pour une combinaison donnée."""
    crue_date_str = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
    suspects_str = ",".join(suspects) if suspects else None
    conn.execute(
        """
        INSERT INTO resultats_crues
            (combinaison_id, crue_date, statut, dqp, dtp, ve, kge, suspects, erreur, date_maj)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(combinaison_id, crue_date) DO UPDATE SET
            statut = excluded.statut, dqp = excluded.dqp, dtp = excluded.dtp,
            ve = excluded.ve, kge = excluded.kge, suspects = excluded.suspects,
            erreur = excluded.erreur, date_maj = excluded.date_maj
        """,
        (combinaison_id, crue_date_str, statut, dqp, dtp, ve, kge, suspects_str, erreur,
         _horodatage()),
    )


def etat_combinaisons(conn):
    """Retourne l'état actuel connu en base pour chaque combinaison déjà rencontrée,
    indexé par (horizon, seuil_c1, methode) -> {"statut", "crues_ok", "crues_ko"}.

    Utilisé par l'onglet Campagne pour initialiser le tableau des combinaisons avec les
    VRAIS derniers statuts connus (au lieu de tout remettre à "pending" à chaque
    lancement) — sans ça, une "Relancer les échecs" affichait comme "pending" des
    combinaisons en réalité déjà réussies (et non retouchées par ce lancement), ce qui
    laissait croire à tort que la campagne entière recommençait de zéro."""
    lignes = conn.execute(
        """
        SELECT c.horizon, c.seuil_c1, c.methode, c.statut,
               SUM(CASE WHEN r.statut = 'success' THEN 1 ELSE 0 END) AS crues_ok,
               SUM(CASE WHEN r.statut = 'failed' THEN 1 ELSE 0 END) AS crues_ko
        FROM combinaisons c
        LEFT JOIN resultats_crues r ON r.combinaison_id = c.id
        GROUP BY c.id
        """
    ).fetchall()
    return {
        (l["horizon"], l["seuil_c1"], l["methode"]): {
            "statut": l["statut"], "crues_ok": l["crues_ok"] or 0, "crues_ko": l["crues_ko"] or 0,
        }
        for l in lignes
    }


def duree_moyenne_par_combinaison(conn, seuil_idle_minutes=35):
    """Estime la durée réelle (calage + rejeu de toutes ses crues) prise par chaque
    combinaison déjà traitée, à partir des horodatages `date_maj` déjà enregistrés —
    aucun chronométrage dédié n'existe, donc on reconstitue une frise chronologique
    unique de tous les événements (fin de calage d'une combinaison, fin de rejeu d'une
    crue), tous combinaisons confondues, triée par date. L'écart entre deux événements
    consécutifs est attribué comme durée de l'étape qui vient de se terminer — valide
    tant que le traitement reste strictement séquentiel (jamais deux combinaisons/crues
    en parallèle, voir modules.run_orchestrator).

    ⚠️ Une session fermée puis relancée en plein milieu du traitement d'une
    combinaison laisse un grand écart de temps qui n'a rien à voir avec un calcul réel
    (l'app était juste fermée) — `seuil_idle_minutes` sert de garde-fou : un écart plus
    grand n'est pas compté comme une durée d'étape. Une combinaison dont au moins une
    étape a ainsi été ignorée est exclue de la moyenne (mesure incomplète, potentiellement
    sous-estimée) plutôt que d'y contribuer avec un chiffre faussé.

    Retourne {"moyenne_minutes": float|None, "nb_mesurees": int,
    "moyenne_par_methode": {"T": float|None, "R": float|None}}."""
    combos = conn.execute("SELECT id, methode, date_maj FROM combinaisons").fetchall()
    crues = conn.execute("SELECT combinaison_id, date_maj FROM resultats_crues").fetchall()

    methode_par_id = {c["id"]: c["methode"] for c in combos}
    evenements = [(datetime.fromisoformat(c["date_maj"]), c["id"]) for c in combos]
    evenements += [(datetime.fromisoformat(r["date_maj"]), r["combinaison_id"]) for r in crues]
    evenements.sort(key=lambda e: e[0])

    nb_pas_total = {}
    for _date, cid in evenements:
        nb_pas_total[cid] = nb_pas_total.get(cid, 0) + 1

    durees_par_combo = {}
    for i in range(1, len(evenements)):
        t_prec, _cid_prec = evenements[i - 1]
        t_cur, cid_cur = evenements[i]
        delta_min = (t_cur - t_prec).total_seconds() / 60
        if 0 < delta_min <= seuil_idle_minutes:
            durees_par_combo.setdefault(cid_cur, []).append(delta_min)

    totaux, totaux_par_methode = [], {"T": [], "R": []}
    for cid, valeurs in durees_par_combo.items():
        if len(valeurs) != nb_pas_total.get(cid, 0):
            continue  # au moins une étape ignorée (coupure de session) -> mesure incomplète
        total = sum(valeurs)
        totaux.append(total)
        methode = methode_par_id.get(cid)
        if methode in totaux_par_methode:
            totaux_par_methode[methode].append(total)

    return {
        "moyenne_minutes": (sum(totaux) / len(totaux)) if totaux else None,
        "nb_mesurees": len(totaux),
        "moyenne_par_methode": {
            m: (sum(v) / len(v)) if v else None for m, v in totaux_par_methode.items()
        },
    }


def duree_par_etape(conn, seuil_idle_minutes=35):
    """Comme duree_moyenne_par_combinaison, mais garde les étapes SÉPARÉES (calage
    d'un côté, rejeu d'une crue de l'autre) au lieu de les sommer par combinaison —
    nécessaire pour estimer le temps d'une sélection qui n'a pas encore tourné (voir
    estimer_temps_restant ci-dessous) : le nombre de crues par combinaison varie d'une
    campagne à l'autre, donc une durée "totale par combinaison" ne s'extrapole pas
    correctement à une nouvelle sélection avec plus ou moins de crues.

    Utilise la MÉDIANE plutôt que la moyenne : constaté en conditions réelles, un
    rejeu isolé ralenti (contention disque, etc.) peut être 40x plus long qu'un rejeu
    normal (typiquement 20-30s) — une moyenne se laisse fausser par un seul cas pareil,
    la médiane beaucoup moins.

    Retourne {"calage": {"T": {"minutes": float|None, "nb_mesures": int}, "R": {...}},
    "rejeu": {"T": {...}, "R": {...}}}."""
    combos = conn.execute("SELECT id, methode, date_maj FROM combinaisons").fetchall()
    crues = conn.execute("SELECT combinaison_id, date_maj FROM resultats_crues").fetchall()
    methode_par_id = {c["id"]: c["methode"] for c in combos}

    evenements = [(datetime.fromisoformat(c["date_maj"]), "calage", c["id"]) for c in combos]
    evenements += [(datetime.fromisoformat(r["date_maj"]), "rejeu", r["combinaison_id"]) for r in crues]
    evenements.sort(key=lambda e: e[0])

    durees = {"calage": {"T": [], "R": []}, "rejeu": {"T": [], "R": []}}
    for i in range(1, len(evenements)):
        t_prec, _type_prec, _id_prec = evenements[i - 1]
        t_cur, type_cur, id_cur = evenements[i]
        delta_min = (t_cur - t_prec).total_seconds() / 60
        if 0 < delta_min <= seuil_idle_minutes:
            methode = methode_par_id.get(id_cur)
            if methode in durees[type_cur]:
                durees[type_cur][methode].append(delta_min)

    def _resume(valeurs):
        return {"minutes": median(valeurs) if valeurs else None, "nb_mesures": len(valeurs)}

    return {etape: {m: _resume(v) for m, v in par_methode.items()}
            for etape, par_methode in durees.items()}


def estimer_temps_restant(conn, combinaisons, crues_dates, duree_par_etape_data):
    """Estime le temps restant (en minutes) pour amener À COMPLÉTION la sélection
    `combinaisons` (liste de (horizon, seuil_c1, methode)) × `crues_dates` — ne compte
    QUE ce qui n'est pas déjà acquis en base, avec exactement la même logique de
    reprise que modules.run_orchestrator._combinaisons_a_traiter/_crues_a_traiter
    (calage déjà réussi → non recompté ; crue déjà réussie sous une combinaison → non
    recomptée), pour rester cohérent avec ce que "Relancer les échecs" ferait
    réellement.

    Retourne (minutes_estimees, nb_etapes_restantes, nb_etapes_total, incertain) —
    `incertain` est True si au moins une étape restante n'a aucune mesure disponible
    pour l'estimer (ex. méthode jamais testée) : le total est alors une sous-estimation
    à signaler, pas une simple absence de résultat."""
    minutes_estimees = 0.0
    nb_etapes_restantes = 0
    nb_etapes_total = 0
    incertain = False

    for horizon, seuil_c1, methode in combinaisons:
        row = conn.execute(
            "SELECT id, statut FROM combinaisons WHERE horizon = ? AND seuil_c1 = ? AND methode = ?",
            (horizon, seuil_c1, methode),
        ).fetchone()
        calage_deja_ok = row is not None and row["statut"] == "success"
        nb_etapes_total += 1
        if not calage_deja_ok:
            nb_etapes_restantes += 1
            mesure = duree_par_etape_data.get("calage", {}).get(methode, {})
            if mesure.get("minutes") is not None:
                minutes_estimees += mesure["minutes"]
            else:
                incertain = True

        combinaison_id = row["id"] if row is not None else None
        for crue_date in crues_dates:
            crue_deja_ok = False
            if combinaison_id is not None:
                crue_iso = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
                r = conn.execute(
                    "SELECT statut FROM resultats_crues WHERE combinaison_id = ? AND crue_date = ?",
                    (combinaison_id, crue_iso),
                ).fetchone()
                crue_deja_ok = r is not None and r["statut"] == "success"
            nb_etapes_total += 1
            if not crue_deja_ok:
                nb_etapes_restantes += 1
                mesure = duree_par_etape_data.get("rejeu", {}).get(methode, {})
                if mesure.get("minutes") is not None:
                    minutes_estimees += mesure["minutes"]
                else:
                    incertain = True

    return minutes_estimees, nb_etapes_restantes, nb_etapes_total, incertain


def max_debit_simule(conn):
    """Débit simulé maximal, tous horizons/seuils/méthodes/crues archivés confondus —
    utilisé par Dashboard > Détail par crue pour une échelle Y COMMUNE entre crues
    (voir ui/tab_dashboard.py) : agrégat SQL, plus rapide qu'une lecture Python de
    toutes les séries archivées. None si aucune série simulée n'est encore archivée."""
    row = conn.execute("SELECT MAX(debit) AS m FROM series_archivees WHERE type = 'sim'").fetchone()
    return row["m"] if row and row["m"] is not None else None


def compter_combinaisons(conn):
    """Nombre total de combinaisons enregistrées (tous statuts confondus) dans la base
    actuellement résolue — utilisé pour signaler à l'utilisateur, au moment où il
    identifie une station (voir ui/tab_config.py), si la campagne va démarrer sur une
    base vierge ou reprendre une base existante pour cette station."""
    return conn.execute("SELECT COUNT(*) AS n FROM combinaisons").fetchone()["n"]


def resume_couverture(conn):
    """Résumé de couverture des combinaisons déjà tentées, agrégé indépendamment par
    valeur de chaque dimension (horizon, seuil_c1, méthode) — ex. pour l'horizon
    "02J00H00M", toutes les combinaisons impliquant cet horizon, quel que soit le seuil
    ou la méthode associés. Utilisé par l'onglet Paramétrage pour visualiser, à côté de
    chaque case/valeur, ce qui a déjà été testé et réussi — support direct de la
    stratégie grille grossière puis affinage (voir NOTE_STRATEGIE dans
    ui/tab_parametrage.py) : repérer d'un coup d'œil les valeurs déjà bien couvertes
    pour ne pas les retester inutilement lors de l'affinage.

    Retourne {"horizons": {...}, "seuils": {...}, "methodes": {...}}, chaque sous-dict
    associant la valeur à {"tentes": nb de combinaisons impliquant cette valeur,
    "complets": nb de ces combinaisons entièrement réussies — calage + toutes les crues
    tentées, même définition que list_combinaisons_completes}.

    ⚠️ N'est PAS filtré par pas de temps (la table `combinaisons` n'a pas cette colonne
    — un horizon donné n'est en pratique utilisé que par un seul pas de temps à la
    fois). Si des codes d'horizon venaient à se recouper entre deux pas de temps
    différents, ce résumé les confondrait — situation qui ne s'est pas encore présentée
    (seul le pas de temps 15 min a des horizons renseignés à ce jour)."""
    lignes = conn.execute(
        """
        SELECT c.horizon, c.seuil_c1, c.methode, c.statut,
               COUNT(r.id) AS nb_crues,
               SUM(CASE WHEN r.statut = 'success' THEN 1 ELSE 0 END) AS crues_ok
        FROM combinaisons c
        LEFT JOIN resultats_crues r ON r.combinaison_id = c.id
        GROUP BY c.id
        """
    ).fetchall()

    par_horizon, par_seuil, par_methode = {}, {}, {}
    for l in lignes:
        complet = (l["statut"] == "success" and l["nb_crues"] > 0
                   and l["nb_crues"] == (l["crues_ok"] or 0))
        for dico, cle in ((par_horizon, l["horizon"]), (par_seuil, l["seuil_c1"]),
                          (par_methode, l["methode"])):
            entree = dico.setdefault(cle, {"tentes": 0, "complets": 0})
            entree["tentes"] += 1
            if complet:
                entree["complets"] += 1
    return {"horizons": par_horizon, "seuils": par_seuil, "methodes": par_methode}


def list_combinaisons_completes(conn):
    """Combinaisons dont le calage a réussi ET dont TOUTES les crues tentées sous cette
    combinaison ont réussi (aucun échec parmi les résultats déjà en base) — donc des
    résultats acquis et exploitables tels quels, sans attendre la fin d'une campagne
    entière. Affiché à l'utilisateur (onglet Campagne) pour qu'il sache ce qui est déjà
    fait avant de relancer une nouvelle campagne, potentiellement avec une sélection de
    crues différente."""
    return conn.execute(
        """
        SELECT c.horizon, c.seuil_c1, c.methode, c.date_maj,
               COUNT(r.id) AS nb_crues,
               SUM(CASE WHEN r.statut = 'success' THEN 1 ELSE 0 END) AS crues_ok
        FROM combinaisons c
        JOIN resultats_crues r ON r.combinaison_id = c.id
        WHERE c.statut = 'success'
        GROUP BY c.id
        HAVING COUNT(r.id) = SUM(CASE WHEN r.statut = 'success' THEN 1 ELSE 0 END)
        ORDER BY c.horizon, c.seuil_c1, c.methode
        """
    ).fetchall()


def list_combinaisons(conn, statut=None):
    if statut:
        return conn.execute(
            "SELECT * FROM combinaisons WHERE statut = ? ORDER BY horizon, seuil_c1, methode",
            (statut,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM combinaisons ORDER BY horizon, seuil_c1, methode"
    ).fetchall()


def list_resultats(conn, combinaison_id=None, statut=None):
    clauses, params = [], []
    if combinaison_id is not None:
        clauses.append("combinaison_id = ?")
        params.append(combinaison_id)
    if statut is not None:
        clauses.append("statut = ?")
        params.append(statut)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"SELECT * FROM resultats_crues {where} ORDER BY combinaison_id, crue_date", params
    ).fetchall()


def list_resultats_avec_combinaison(conn):
    """Jointure complète — une ligne par (combinaison, crue), utilisée par le dashboard
    (bloc 6) pour croiser horizon × seuil × méthode sans requêtes séparées.
    `combinaison_id` est inclus pour retrouver la série archivée correspondante
    (voir archiver_serie/charger_serie)."""
    return conn.execute(
        """
        SELECT c.id AS combinaison_id, c.horizon, c.seuil_c1, c.methode,
               c.statut AS statut_combinaison,
               r.crue_date, r.statut AS statut_crue, r.dqp, r.dtp, r.ve, r.kge, r.suspects
        FROM combinaisons c
        JOIN resultats_crues r ON r.combinaison_id = c.id
        ORDER BY c.horizon, c.seuil_c1, c.methode, r.crue_date
        """
    ).fetchall()


def archiver_serie(conn, combinaison_id, crue_date, type_serie, points):
    """Archive une série observée ('obs') ou simulée ('sim') — `points` : itérable de
    (datetime, debit, pluie), typiquement le retour de modules.grp_series.parser_*.

    Remplace toute archive précédente pour ce (combinaison_id, crue_date, type) plutôt
    que d'accumuler des doublons si la même crue est rejouée plusieurs fois (reprise sur
    échec, nouvelle campagne testant à nouveau la même combinaison).
    """
    crue_date_str = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
    conn.execute(
        "DELETE FROM series_archivees WHERE combinaison_id = ? AND crue_date = ? AND type = ?",
        (combinaison_id, crue_date_str, type_serie),
    )
    conn.executemany(
        """
        INSERT INTO series_archivees (combinaison_id, crue_date, type, point_date, debit, pluie)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (combinaison_id, crue_date_str, type_serie,
             date.isoformat() if hasattr(date, "isoformat") else date, debit, pluie)
            for date, debit, pluie in points
        ],
    )


def charger_serie(conn, combinaison_id, crue_date, type_serie):
    """Recharge une série archivée — retourne une liste de (datetime, debit, pluie)
    triée chronologiquement, vide si rien n'a été archivé pour ce (combinaison, crue,
    type) (ex. rejeu antérieur à l'ajout de cette fonctionnalité, ou séries GRP absentes
    au moment du run — voir modules.grp_series)."""
    from datetime import datetime as _datetime

    crue_date_str = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
    lignes = conn.execute(
        """
        SELECT point_date, debit, pluie FROM series_archivees
        WHERE combinaison_id = ? AND crue_date = ? AND type = ?
        ORDER BY point_date
        """,
        (combinaison_id, crue_date_str, type_serie),
    ).fetchall()
    return [(_datetime.fromisoformat(l["point_date"]), l["debit"], l["pluie"]) for l in lignes]
