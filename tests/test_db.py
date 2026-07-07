"""Tests for db.py — backend selection and the sqlite→psycopg SQL translation.

The translation is pure string work, testable without a Postgres server. The
live-Postgres behavior (type loaders, commit semantics) was verified against
Supabase during Stage 2 and is exercised by the app itself.
"""

import sqlite3

import db
from db import _PgConnection


def test_qmark_becomes_format_style():
    sql = "SELECT * FROM t WHERE a = ? AND b = ?"
    assert _PgConnection._translate(sql, (1, 2)) == "SELECT * FROM t WHERE a = %s AND b = %s"


def test_named_style_becomes_pyformat():
    sql = "INSERT INTO t (a, b) VALUES (:a, :b)"
    assert _PgConnection._translate(sql, {"a": 1, "b": 2}) == "INSERT INTO t (a, b) VALUES (%(a)s, %(b)s)"


def test_postgres_casts_survive_named_translation():
    sql = "SELECT CAST(? AS NUMERIC), x::text FROM t WHERE y = :y"
    out = _PgConnection._translate(sql, {"y": 1})
    assert "::text" in out and "%(y)s" in out


def test_literal_percent_is_escaped_when_params_present():
    sql = "SELECT * FROM settings WHERE key LIKE 'fx_%' AND k = ?"
    assert _PgConnection._translate(sql, ("v",)) == "SELECT * FROM settings WHERE key LIKE 'fx_%%' AND k = %s"


def test_explicit_sqlite_file_wins_over_postgres_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VERMO_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    assert db.is_postgres()
    conn = db.connect(tmp_path / "x.db")  # must NOT try to reach Postgres
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_backend_defaults_to_sqlite(monkeypatch):
    monkeypatch.setenv("VERMO_BACKEND", "sqlite")
    assert not db.is_postgres()
    monkeypatch.setenv("VERMO_BACKEND", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    # DATABASE_URL alone is not enough — the flag must be explicit.
    monkeypatch.delenv("VERMO_BACKEND")
    monkeypatch.setattr(db, "_ENV_FILE", db.BASE_DIR / "nonexistent.env")
    assert not db.is_postgres()
