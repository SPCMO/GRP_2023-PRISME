# -*- coding: utf-8 -*-
"""Persistance SQLite des résultats de campagne (data/runs.sqlite3).

Deux niveaux, qui reflètent le déroulement réel d'une campagne (voir
modules.run_orchestrator) :
  - `combinaisons` : une ligne par (horizon, seuil_c1, méthode) — le calage (exe 04)
    n'est lancé qu'UNE fois par combinaison, réutilisé pour toutes les crues rejouées.
  - `resultats_crues` : une ligne par (combinaison, crue rejouée) — statut et indicateurs
    dQP/dTP/VE/KGE du rejeu opérationnel de cette crue précise sous cette combinaison.

Cette double granularité permet la reprise sur échec demandée par l'utilisateur : si le
calage d'une combinaison échoue, aucune crue n'est tentée sous cette combinaison ; si le
calage réussit mais qu'une seule crue échoue (ex. .bat qui plante), on peut relancer
uniquement cette crue sans refaire le calage.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config as app_config

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
"""


def init_db(db_path=None):
    db_path = db_path or app_config.DB_PATH
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
    db_path = db_path or app_config.DB_PATH
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
    (bloc 6) pour croiser horizon × seuil × méthode sans requêtes séparées."""
    return conn.execute(
        """
        SELECT c.horizon, c.seuil_c1, c.methode, c.statut AS statut_combinaison,
               r.crue_date, r.statut AS statut_crue, r.dqp, r.dtp, r.ve, r.kge, r.suspects
        FROM combinaisons c
        JOIN resultats_crues r ON r.combinaison_id = c.id
        ORDER BY c.horizon, c.seuil_c1, c.methode, r.crue_date
        """
    ).fetchall()
