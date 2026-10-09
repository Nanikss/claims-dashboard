"""SQLite schema, loading helpers and the per-request connection."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from flask import current_app, g

# Money is stored as integer cents so sums are exact; dates are ISO-8601 text,
# which SQLite compares and does date math on correctly.
SCHEMA = """
DROP TABLE IF EXISTS claims;
DROP TABLE IF EXISTS policies;

CREATE TABLE policies (
    policy_id              TEXT PRIMARY KEY,
    state                  TEXT    NOT NULL,
    line                   TEXT    NOT NULL,
    effective_date         TEXT    NOT NULL,
    expiration_date        TEXT    NOT NULL,
    written_premium_cents  INTEGER NOT NULL CHECK (written_premium_cents >= 0),
    CHECK (expiration_date > effective_date)
);

CREATE TABLE claims (
    claim_id       TEXT PRIMARY KEY,
    policy_id      TEXT    NOT NULL REFERENCES policies (policy_id),
    loss_date      TEXT    NOT NULL,
    reported_date  TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('open', 'closed')),
    paid_cents     INTEGER NOT NULL CHECK (paid_cents >= 0),
    reserve_cents  INTEGER NOT NULL CHECK (reserve_cents >= 0),
    CHECK (reported_date >= loss_date)
);

CREATE INDEX ix_claims_loss_date ON claims (loss_date);
CREATE INDEX ix_claims_policy    ON claims (policy_id);
CREATE INDEX ix_policies_state_line ON policies (state, line);
"""

PolicyRow = tuple[str, str, str, str, str, int]
ClaimRow = tuple[str, str, str, str, str, int, int]


def load(db_path: str | Path, policies: Iterable[PolicyRow], claims: Iterable[ClaimRow]) -> None:
    """Recreate the schema and load rows in a single transaction."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # Explicit BEGIN so the DDL and the inserts commit (or roll back) together;
        # a failed reseed leaves the previous data in place.
        conn.executescript("BEGIN;" + SCHEMA)
        conn.executemany("INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?)", policies)
        conn.executemany("INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?)", claims)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    """Read-only connection for the current request (the API never writes)."""
    if "db" not in g:
        uri = Path(current_app.config["DATABASE"]).resolve().as_uri() + "?mode=ro"
        g.db = sqlite3.connect(uri, uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
