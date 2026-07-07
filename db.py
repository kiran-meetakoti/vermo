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
import threading
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
    qmark placeholders, connection.execute(), commit-on-with-exit.

    Connections come from a process-wide pool and go back to it on
    close()/__exit__ instead of being torn down. This matters enormously
    for hosted deployments: a fresh TLS connection to the Supabase pooler
    costs several network round trips (~600 ms from the same continent,
    worse cross-Atlantic), and Streamlit reruns issue many queries per
    interaction — measured 612 → 60 ms per query when pooled."""

    def __init__(self, pool):
        self._pool = pool
        self._conn = pool.getconn()
        self._returned = False

    def _return_to_pool(self) -> None:
        if not self._returned:
            self._returned = True
            self._pool.putconn(self._conn)

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
        # Roll back anything uncommitted, then hand the connection back to
        # the pool (mirrors sqlite3, where close() discards open work).
        import psycopg

        if self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            self._conn.rollback()
        self._return_to_pool()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Same contract callers relied on: commit on success, rollback on
        # error. The connection then returns to the pool instead of closing.
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._return_to_pool()
        return False


_pg_pool = None
_pg_pool_lock = threading.Lock()


def _configure_pg(conn) -> None:
    """Run once per pooled connection: return SQLite-shaped Python types
    (see module docstring)."""
    from psycopg.types.numeric import FloatLoader
    from psycopg.types.string import TextLoader

    for typename in ("timestamptz", "timestamp", "date", "uuid", "jsonb"):
        conn.adapters.register_loader(typename, TextLoader)
    conn.adapters.register_loader("numeric", FloatLoader)


def _get_pool():
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool

                _pg_pool = ConnectionPool(
                    _env("DATABASE_URL"),
                    min_size=0,
                    max_size=4,
                    # Close pooled connections idle beyond this before the
                    # Supabase pooler / NAT kills them under us.
                    max_idle=300,
                    timeout=15,
                    kwargs={
                        "row_factory": dict_row,
                        "connect_timeout": 15,
                        # TCP keepalives so long-lived pooled connections
                        # survive NAT/proxy idle timeouts.
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                    },
                    configure=_configure_pg,
                )
    return _pg_pool


def _pg_connect():
    return _PgConnection(_get_pool())


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
