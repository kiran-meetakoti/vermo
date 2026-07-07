"""Backend-agnostic database connections: SQLite (default) or Postgres.

The whole app was written against sqlite3 (qmark placeholders, string
timestamps, float money, truthy 0/1 booleans). This module lets those call
sites run unchanged against Supabase Postgres:

  * connect() returns a sqlite3.Connection, or a wrapper around a psycopg
    connection that translates `?` placeholders to `%s` at execute time.
  * psycopg loaders are registered so Postgres returns the same Python types
    SQLite does: numeric -> float, timestamptz/date/uuid -> str, jsonb -> str.
    (dict_row makes row["col"] and dict(row) work like sqlite3.Row.)
  * Both backends commit on clean `with connect() as conn:` exit.

Backend selection is EXPLICIT: Postgres only when VERMO_BACKEND=postgres
(env var, or .env line) AND DATABASE_URL is set. Anything else is SQLite —
so tests and local dev never accidentally touch the production database.
Passing an explicit sqlite_file always forces SQLite (tests use tmp files).

Schema management differs by backend on purpose: the SQLite init/migration
functions in main.py / streamlit_app.py are skipped on Postgres, where the
schema comes from migrations/postgres/*.sql (see docs/DATA_MODEL.md).
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_FILE = BASE_DIR / "data" / "portfolio.db"
_ENV_FILE = BASE_DIR / ".env"


def _env(key: str) -> str:
    """Environment variable, falling back to the gitignored .env file."""
    value = os.environ.get(key, "")
    if value:
        return value
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def is_postgres() -> bool:
    return _env("VERMO_BACKEND").lower() == "postgres" and bool(_env("DATABASE_URL"))


class _PgConnection:
    """Thin psycopg wrapper so sqlite3-style call sites work unchanged:
    qmark placeholders, connection.execute(), commit-on-with-exit."""

    def __init__(self, conn):
        self._conn = conn

    # sqlite3 named style ":param" → psycopg "%(param)s". The lookbehind keeps
    # Postgres "::type" casts and drive-letter-like tokens out of the match.
    _NAMED_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")

    @classmethod
    def _translate(cls, sql: str, params) -> str:
        sql = sql.replace("%", "%%")
        if isinstance(params, dict):
            return cls._NAMED_PARAM.sub(r"%(\1)s", sql)
        return sql.replace("?", "%s")

    def execute(self, sql: str, params=()):
        if not params:
            # No params → pass through untouched; psycopg then does no
            # client-side formatting, so literal % (LIKE 'fx_%') is safe.
            return self._conn.execute(sql)
        return self._conn.execute(self._translate(sql, params), params)

    def executemany(self, sql: str, seq_of_params):
        seq_of_params = list(seq_of_params)
        first = seq_of_params[0] if seq_of_params else ()
        cur = self._conn.cursor()
        cur.executemany(self._translate(sql, first), seq_of_params)
        return cur

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # psycopg's own context manager: commit on success, rollback on error,
        # then close. sqlite3's leaves the connection open, but every caller
        # opens a fresh connection per operation, so closing is safe.
        return self._conn.__exit__(exc_type, exc, tb)


def _pg_connect():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.numeric import FloatLoader
    from psycopg.types.string import TextLoader

    conn = psycopg.connect(_env("DATABASE_URL"), row_factory=dict_row, connect_timeout=15)
    # Return SQLite-shaped Python types (see module docstring).
    for typename in ("timestamptz", "timestamp", "date", "uuid", "jsonb"):
        conn.adapters.register_loader(typename, TextLoader)
    conn.adapters.register_loader("numeric", FloatLoader)
    return _PgConnection(conn)


def connect(sqlite_file: Path | str | None = None):
    """Open a connection to the configured backend.

    sqlite_file=None means "use the configured backend" (Postgres when
    VERMO_BACKEND=postgres, else the default SQLite file). An explicit
    sqlite_file always forces SQLite on that file.
    """
    if sqlite_file is None and is_postgres():
        return _pg_connect()
    path = Path(sqlite_file) if sqlite_file is not None else DEFAULT_SQLITE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection
