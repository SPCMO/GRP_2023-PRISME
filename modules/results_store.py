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
    -- 'reference' = comportement historique (rejeu positionné à la date de début GRP de
    -- la crue, ~2j avant le pic selon NJ) ; 'H-24'/'H-12'/'H-6'/... = instants
    -- supplémentaires positionnés par rapport au pic RÉEL de la crue (voir
    -- modules.run_orchestrator et Aide.html > "Rejeu à plusieurs instants avant le
    -- pic"). Purement additif : toutes les requêtes qui pilotent la reprise sur échec,
    -- les badges de couverture et le score composite restent filtrées sur 'reference'
    -- pour ne jamais changer de comportement vis-à-vis de l'existant.
    instant_label TEXT NOT NULL DEFAULT 'reference',
    statut TEXT NOT NULL DEFAULT 'pending'
        CHECK (statut IN ('pending', 'running', 'success', 'failed')),
    dqp REAL, dtp REAL, ve REAL, kge REAL,
    suspects TEXT,        -- indicateurs hors bornes plausibles, séparés par des virgules
    erreur TEXT,
    date_maj TEXT NOT NULL,
    UNIQUE (combinaison_id, crue_date, instant_label)
);

CREATE TABLE IF NOT EXISTS series_archivees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combinaison_id INTEGER NOT NULL REFERENCES combinaisons(id) ON DELETE CASCADE,
    crue_date TEXT NOT NULL,
    instant_label TEXT NOT NULL DEFAULT 'reference',  -- voir resultats_crues.instant_label
    type TEXT NOT NULL CHECK (type IN ('obs', 'sim')),
    point_date TEXT NOT NULL,
    debit REAL,
    pluie REAL
);
"""

# Index sur instant_label, VOLONTAIREMENT séparé du SCHEMA ci-dessus (bug latent trouvé
# par un test ajouté suite à l'audit de code du 1er septembre 2026, finding C1) : sur
# une base pré-27/08/2026 (avant l'ajout de instant_label), exécuter cet index dans le
# même executescript() que SCHEMA plantait avec "no such column: instant_label" — les
# `CREATE TABLE IF NOT EXISTS` ci-dessus n'ajoutent PAS la colonne à une table déjà
# existante, et cet index s'exécutait AVANT que _migrer_schema_multi_instants() n'ait
# eu la moindre chance de l'ajouter (voir init_db). Créé maintenant seulement APRÈS la
# migration, quand la colonne est garantie présente (base neuve ou déjà migrée).
_INDEX_SERIES_ARCHIVEES = """
CREATE INDEX IF NOT EXISTS idx_series_archivees
    ON series_archivees(combinaison_id, crue_date, instant_label, type);
"""

INSTANT_REFERENCE = "reference"


def dossier_data_effectif():
    """Dossier où vivent les bases `data/runs_<code_station>.sqlite3` — `app_config.
    DATA_DIR` (à l'intérieur du dossier d'installation de l'outil) par défaut, ou le
    dossier externe pointé par `app_config.FICHIER_POINTEUR_DATA` si ce fichier existe
    et contient un chemin non vide (réglage optionnel, voir ui.tab_config, bandeau
    "Dossier de stockage des bases de résultats").

    Ce pointeur vit dans %APPDATA%, HORS du dossier d'installation de l'outil : il
    survit ainsi à une réinstallation dans un nouveau dossier (contrairement à data/,
    gitignoré et donc absent de toute nouvelle copie/clone) — corrige un incident réel
    constaté (un utilisateur ayant recloné l'outil dans un nouveau dossier plutôt que
    de le mettre à jour en place s'est retrouvé avec une base de résultats vierge,
    l'ancienne étant restée dans l'ancien dossier sans qu'il en soit averti). Une fois
    ce dossier externe choisi une fois sur un poste, toute future réinstallation de
    l'outil sur ce même poste (même dans un tout nouveau dossier) retrouve
    automatiquement les mêmes bases.

    Ne lève jamais : un fichier pointeur absent, illisible ou vide retombe simplement
    sur le comportement par défaut (DATA_DIR)."""
    try:
        with open(app_config.FICHIER_POINTEUR_DATA, encoding="utf-8") as f:
            chemin = f.read().strip()
    except (FileNotFoundError, OSError):
        return app_config.DATA_DIR
    return chemin or app_config.DATA_DIR


def _chemin_db_par_defaut():
    """Un fichier `<dossier_data_effectif()>/runs_<code_station>.sqlite3` distinct par
    station configurée, plutôt qu'un unique `runs.sqlite3` partagé par toutes les
    stations.

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
    dossier = dossier_data_effectif()
    chemin_partage = os.path.join(dossier, "runs.sqlite3")
    try:
        config_data = config_manager.load_config()
    except (FileNotFoundError, ValueError):
        return chemin_partage
    code_station = (config_data.get("station", {}).get("code_station") or "").strip()
    if not code_station:
        return chemin_partage
    chemin_station = os.path.join(dossier, f"runs_{code_station}.sqlite3")
    _migrer_ancienne_base_partagee_si_necessaire(chemin_station, chemin_partage)
    return chemin_station


def _migrer_ancienne_base_partagee_si_necessaire(chemin_station, chemin_partage):
    """Migration ponctuelle, exécutée au plus une seule fois en tout et pour tout :
    tant que l'ancien fichier partagé `runs.sqlite3` existe encore (dans le dossier
    data effectif actuel — voir dossier_data_effectif) ET qu'aucune base dédiée n'a
    déjà été créée pour la station actuellement configurée, on le renomme vers cette
    base dédiée plutôt que de laisser croire à une perte de données. Une fois ce
    renommage effectué, `runs.sqlite3` n'existe plus : aucune autre station configurée
    ultérieurement ne pourra plus jamais hériter par erreur de cet historique — la
    migration ne peut donc profiter qu'à la toute première station active après la
    mise à jour de l'outil (typiquement celle déjà en cours d'usage)."""
    if os.path.exists(chemin_partage) and not os.path.exists(chemin_station):
        os.makedirs(os.path.dirname(chemin_station), exist_ok=True)
        os.replace(chemin_partage, chemin_station)
        logger.info("Migration de l'ancienne base partagée %s vers %s (1 fois, station "
                    "active au moment de la migration)", chemin_partage, chemin_station)


def _colonne_existe(conn, table, colonne):
    # PRAGMA table_info : (cid, name, type, notnull, dflt_value, pk) — colonne d'index 1.
    # Accès positionnel plutôt que par nom (conn.row_factory n'est pas garanti être
    # sqlite3.Row ici : init_db() ne le configure pas, contrairement à db_session()).
    return any(row[1] == colonne for row in conn.execute(f"PRAGMA table_info({table})"))


def _migrer_schema_multi_instants(conn):
    """Migration ponctuelle (rejeu à plusieurs instants avant le pic, 27/08/2026) :
    `resultats_crues`/`series_archivees` n'avaient qu'une ligne par (combinaison, crue)
    — un seul instant de rejeu possible. SQLite ne permet pas de modifier une
    contrainte UNIQUE sur une table existante : reconstruction complète (renommer,
    créer la nouvelle table avec `instant_label`, recopier, supprimer l'ancienne),
    dans UNE SEULE transaction explicite (BEGIN/COMMIT du script), avec toutes les
    lignes déjà en base migrées vers `instant_label='reference'` — comportement
    strictement identique à avant pour ces lignes existantes, aucune perte.

    Sans effet (retour immédiat) si la colonne existe déjà : base neuve créée
    directement avec le nouveau schéma, ou migration déjà effectuée lors d'un appel
    précédent."""
    if _colonne_existe(conn, "resultats_crues", "instant_label"):
        return

    logger.info("Migration du schéma resultats_crues/series_archivees (ajout de "
                "instant_label pour le rejeu à plusieurs instants avant le pic)...")
    conn.executescript("""
        BEGIN;

        ALTER TABLE resultats_crues RENAME TO resultats_crues_ancien;
        CREATE TABLE resultats_crues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            combinaison_id INTEGER NOT NULL REFERENCES combinaisons(id) ON DELETE CASCADE,
            crue_date TEXT NOT NULL,
            instant_label TEXT NOT NULL DEFAULT 'reference',
            statut TEXT NOT NULL DEFAULT 'pending'
                CHECK (statut IN ('pending', 'running', 'success', 'failed')),
            dqp REAL, dtp REAL, ve REAL, kge REAL,
            suspects TEXT,
            erreur TEXT,
            date_maj TEXT NOT NULL,
            UNIQUE (combinaison_id, crue_date, instant_label)
        );
        INSERT INTO resultats_crues
            (id, combinaison_id, crue_date, instant_label, statut, dqp, dtp, ve, kge,
             suspects, erreur, date_maj)
        SELECT id, combinaison_id, crue_date, 'reference', statut, dqp, dtp, ve, kge,
               suspects, erreur, date_maj
        FROM resultats_crues_ancien;
        DROP TABLE resultats_crues_ancien;

        ALTER TABLE series_archivees RENAME TO series_archivees_ancien;
        CREATE TABLE series_archivees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            combinaison_id INTEGER NOT NULL REFERENCES combinaisons(id) ON DELETE CASCADE,
            crue_date TEXT NOT NULL,
            instant_label TEXT NOT NULL DEFAULT 'reference',
            type TEXT NOT NULL CHECK (type IN ('obs', 'sim')),
            point_date TEXT NOT NULL,
            debit REAL,
            pluie REAL
        );
        INSERT INTO series_archivees
            (id, combinaison_id, crue_date, instant_label, type, point_date, debit, pluie)
        SELECT id, combinaison_id, crue_date, 'reference', type, point_date, debit, pluie
        FROM series_archivees_ancien;
        DROP TABLE series_archivees_ancien;
        CREATE INDEX IF NOT EXISTS idx_series_archivees
            ON series_archivees(combinaison_id, crue_date, instant_label, type);

        COMMIT;
    """)
    logger.info("Migration terminée — toutes les lignes existantes conservées avec "
                "instant_label='reference'.")


def init_db(db_path=None):
    db_path = db_path or _chemin_db_par_defaut()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        _migrer_schema_multi_instants(conn)
        # _INDEX_SERIES_ARCHIVEES APRÈS la migration, jamais avant (voir sa docstring) :
        # sur une base pré-27/08/2026, la colonne instant_label n'existe qu'à partir
        # d'ici (ajoutée par _migrer_schema_multi_instants ci-dessus).
        conn.executescript(_INDEX_SERIES_ARCHIVEES)
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
                          ve=None, kge=None, suspects=None, erreur=None,
                          instant_label=INSTANT_REFERENCE):
    """Crée ou remet à jour le résultat d'une crue pour une combinaison donnée, à
    l'instant de rejeu `instant_label` ('reference' par défaut = comportement
    historique, un seul résultat par (combinaison, crue) ; voir modules.run_orchestrator
    pour les instants supplémentaires positionnés par rapport au pic)."""
    crue_date_str = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
    suspects_str = ",".join(suspects) if suspects else None
    conn.execute(
        """
        INSERT INTO resultats_crues
            (combinaison_id, crue_date, instant_label, statut, dqp, dtp, ve, kge,
             suspects, erreur, date_maj)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(combinaison_id, crue_date, instant_label) DO UPDATE SET
            statut = excluded.statut, dqp = excluded.dqp, dtp = excluded.dtp,
            ve = excluded.ve, kge = excluded.kge, suspects = excluded.suspects,
            erreur = excluded.erreur, date_maj = excluded.date_maj
        """,
        (combinaison_id, crue_date_str, instant_label, statut, dqp, dtp, ve, kge,
         suspects_str, erreur, _horodatage()),
    )


def etat_combinaisons(conn):
    """Retourne l'état actuel connu en base pour chaque combinaison déjà rencontrée,
    indexé par (horizon, seuil_c1, methode) -> {"statut", "crues_ok", "crues_ko"}.

    Utilisé par l'onglet Campagne pour initialiser le tableau des combinaisons avec les
    VRAIS derniers statuts connus (au lieu de tout remettre à "pending" à chaque
    lancement) — sans ça, une "Relancer les échecs" affichait comme "pending" des
    combinaisons en réalité déjà réussies (et non retouchées par ce lancement), ce qui
    laissait croire à tort que la campagne entière recommençait de zéro."""
    # Jointure filtrée sur instant_label='reference' : les instants supplémentaires
    # (rejeu à plusieurs instants avant le pic) sont purement additifs et ne doivent
    # jamais changer ce que la reprise sur échec / les badges considèrent comme
    # "cette crue est faite" pour la campagne principale.
    lignes = conn.execute(
        """
        SELECT c.horizon, c.seuil_c1, c.methode, c.statut,
               SUM(CASE WHEN r.statut = 'success' THEN 1 ELSE 0 END) AS crues_ok,
               SUM(CASE WHEN r.statut = 'failed' THEN 1 ELSE 0 END) AS crues_ko
        FROM combinaisons c
        LEFT JOIN resultats_crues r ON r.combinaison_id = c.id AND r.instant_label = 'reference'
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
                # instant_label='reference' : voir etat_combinaisons ci-dessus, même
                # principe — les instants supplémentaires ne comptent jamais ici.
                r = conn.execute(
                    "SELECT statut FROM resultats_crues "
                    "WHERE combinaison_id = ? AND crue_date = ? AND instant_label = 'reference'",
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
    # instant_label='reference' : ignore les séries des instants supplémentaires (rejeu
    # à plusieurs instants avant le pic), pour ne jamais changer l'échelle Y du
    # graphique "Détail par crue" existant — ces instants ont leur propre visualisation
    # dédiée (voir ui/tab_dashboard.py).
    row = conn.execute(
        "SELECT MAX(debit) AS m FROM series_archivees WHERE type = 'sim' AND instant_label = 'reference'"
    ).fetchone()
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
    # instant_label='reference' : voir etat_combinaisons — même principe, les instants
    # supplémentaires ne doivent pas gonfler ces compteurs de couverture.
    lignes = conn.execute(
        """
        SELECT c.horizon, c.seuil_c1, c.methode, c.statut,
               COUNT(r.id) AS nb_crues,
               SUM(CASE WHEN r.statut = 'success' THEN 1 ELSE 0 END) AS crues_ok
        FROM combinaisons c
        LEFT JOIN resultats_crues r ON r.combinaison_id = c.id AND r.instant_label = 'reference'
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
    # instant_label='reference' : voir etat_combinaisons — même principe.
    return conn.execute(
        """
        SELECT c.horizon, c.seuil_c1, c.methode, c.date_maj,
               COUNT(r.id) AS nb_crues,
               SUM(CASE WHEN r.statut = 'success' THEN 1 ELSE 0 END) AS crues_ok
        FROM combinaisons c
        JOIN resultats_crues r ON r.combinaison_id = c.id AND r.instant_label = 'reference'
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


def list_instants_resultat(conn, combinaison_id, crue_date):
    """Tous les résultats disponibles pour (combinaison, crue), un par instant de rejeu
    testé — 'reference' (comportement historique) puis les instants supplémentaires
    positionnés par rapport au pic (voir modules.run_orchestrator) — pour la
    visualisation multi-instants de Dashboard > Détail par crue. 'reference' toujours
    en premier, puis ordre alphabétique des autres labels (ex. H-12 avant H-24 avant
    H-6 — sans importance pour l'affichage, qui les trie lui-même par décalage réel)."""
    crue_date_str = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
    return conn.execute(
        """
        SELECT instant_label, statut, dqp, dtp, ve, kge, suspects, erreur, date_maj
        FROM resultats_crues
        WHERE combinaison_id = ? AND crue_date = ?
        ORDER BY CASE WHEN instant_label = 'reference' THEN 0 ELSE 1 END, instant_label
        """,
        (combinaison_id, crue_date_str),
    ).fetchall()


def list_resultats_avec_combinaison(conn):
    """Jointure complète — une ligne par (combinaison, crue), utilisée par le dashboard
    (bloc 6) pour croiser horizon × seuil × méthode sans requêtes séparées.
    `combinaison_id` est inclus pour retrouver la série archivée correspondante
    (voir archiver_serie/charger_serie).

    Filtrée sur instant_label='reference' : le score composite et tous les graphiques
    du Dashboard ne doivent JAMAIS mélanger les instants supplémentaires (rejeu à
    plusieurs instants avant le pic) avec le résultat de référence d'une campagne —
    une même crue physique compterait sinon plusieurs fois avec des indicateurs
    différents, faussant toutes les statistiques."""
    return conn.execute(
        """
        SELECT c.id AS combinaison_id, c.horizon, c.seuil_c1, c.methode,
               c.statut AS statut_combinaison,
               r.crue_date, r.statut AS statut_crue, r.dqp, r.dtp, r.ve, r.kge, r.suspects,
               r.erreur AS note
        FROM combinaisons c
        JOIN resultats_crues r ON r.combinaison_id = c.id
        WHERE r.instant_label = 'reference'
        ORDER BY c.horizon, c.seuil_c1, c.methode, r.crue_date
        """
    ).fetchall()


def archiver_serie(conn, combinaison_id, crue_date, type_serie, points,
                    instant_label=INSTANT_REFERENCE):
    """Archive une série observée ('obs') ou simulée ('sim') — `points` : itérable de
    (datetime, debit, pluie), typiquement le retour de modules.grp_series.parser_*.

    Remplace toute archive précédente pour ce (combinaison_id, crue_date, instant_label,
    type) plutôt que d'accumuler des doublons si la même crue est rejouée plusieurs fois
    (reprise sur échec, nouvelle campagne testant à nouveau la même combinaison).

    `instant_label` ('reference' par défaut) : la série observée ('obs') est identique
    quel que soit l'instant de rejeu (c'est la même chronique historique) — l'appelant
    l'archive donc toujours sous 'reference' même en traitant un instant supplémentaire,
    seule la série simulée ('sim') varie réellement d'un instant à l'autre (voir
    modules.run_orchestrator)."""
    crue_date_str = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
    conn.execute(
        "DELETE FROM series_archivees "
        "WHERE combinaison_id = ? AND crue_date = ? AND instant_label = ? AND type = ?",
        (combinaison_id, crue_date_str, instant_label, type_serie),
    )
    conn.executemany(
        """
        INSERT INTO series_archivees
            (combinaison_id, crue_date, instant_label, type, point_date, debit, pluie)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (combinaison_id, crue_date_str, instant_label, type_serie,
             date.isoformat() if hasattr(date, "isoformat") else date, debit, pluie)
            for date, debit, pluie in points
        ],
    )


def charger_serie(conn, combinaison_id, crue_date, type_serie, instant_label=INSTANT_REFERENCE):
    """Recharge une série archivée — retourne une liste de (datetime, debit, pluie)
    triée chronologiquement, vide si rien n'a été archivé pour ce (combinaison, crue,
    instant, type) (ex. rejeu antérieur à l'ajout de cette fonctionnalité, ou séries GRP
    absentes au moment du run — voir modules.grp_series)."""
    from datetime import datetime as _datetime

    crue_date_str = crue_date.isoformat() if hasattr(crue_date, "isoformat") else crue_date
    lignes = conn.execute(
        """
        SELECT point_date, debit, pluie FROM series_archivees
        WHERE combinaison_id = ? AND crue_date = ? AND instant_label = ? AND type = ?
        ORDER BY point_date
        """,
        (combinaison_id, crue_date_str, instant_label, type_serie),
    ).fetchall()
    return [(_datetime.fromisoformat(l["point_date"]), l["debit"], l["pluie"]) for l in lignes]
