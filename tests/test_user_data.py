"""Tests for user_data.py — GDPR export and account deletion.

The risks worth pinning: export leaking another user's rows, deletion
missing a table (data survives "erasure"), and the PER_USER_TABLES list
drifting as new tables are added.
"""

from datetime import date
import json

import pytest

import db
import goals_db
import income_db
import user_data

USER_A = "user-a"
USER_B = "user-b"


@pytest.fixture()
def db_file(tmp_path):
    """A portfolio DB containing every per-user table, with one row each for
    two users in the tables we exercise."""
    path = tmp_path / "portfolio.db"
    income_db.ensure_schema(path)
    goals_db.ensure_schema(path)
    with db.connect(path) as conn:
        # Minimal shapes for the remaining tables — user_id is what matters here.
        for table in user_data.PER_USER_TABLES:
            if table in ("income_events", "goals"):
                continue
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT, user_id TEXT, note TEXT)")
        for user in (USER_A, USER_B):
            for table in user_data.PER_USER_TABLES:
                if table in ("income_events", "goals"):
                    continue
                conn.execute(f"INSERT INTO {table} VALUES (?, ?, 'x')", (f"{table}-{user}", user))
    income_db.add_income(USER_A, "Dividend", "VWCE", 10.0, date(2026, 6, 1), db_file=path)
    goals_db.add_goal(USER_A, "House", 1000.0, date(2027, 1, 1), db_file=path)
    return path


def test_export_contains_all_tables_and_only_own_rows(db_file):
    export = user_data.export_user_data(USER_A, db_file=db_file)
    assert set(export["tables"]) == set(user_data.PER_USER_TABLES)
    for table, rows in export["tables"].items():
        assert all(row["user_id"] == USER_A for row in rows), table
    assert export["row_counts"]["income_events"] == 1
    assert export["row_counts"]["goals"] == 1
    assert export["row_counts"]["holdings"] == 1
    # The export must be JSON-serializable as-is (it becomes the download).
    json.dumps(export)


def test_delete_erases_every_table_but_spares_others(db_file):
    deleted = user_data.delete_account(USER_A, db_file=db_file)
    assert set(deleted) == set(user_data.PER_USER_TABLES)
    assert deleted["holdings"] == 1 and deleted["goals"] == 1

    with db.connect(db_file) as conn:
        for table in user_data.PER_USER_TABLES:
            remaining = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = ?", (USER_A,)
            ).fetchall()[0]["n"]
            assert remaining == 0, f"{table} still has rows after deletion"
        # user B untouched
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM holdings WHERE user_id = ?", (USER_B,)
        ).fetchall()[0]["n"] == 1


def test_export_after_delete_is_empty(db_file):
    user_data.delete_account(USER_A, db_file=db_file)
    export = user_data.export_user_data(USER_A, db_file=db_file)
    assert all(count == 0 for count in export["row_counts"].values())


def test_per_user_tables_covers_known_schema():
    """Guard against drift: every table DATA_MODEL.md documents as per-user
    must be in PER_USER_TABLES (a new table missing here would survive
    'account deletion' — the worst kind of bug for a GDPR feature)."""
    documented = {
        "holdings", "imports", "snapshots", "refresh_runs", "budget_settings",
        "budget_expenses", "manual_assets", "broker_imports",
        "broker_transactions", "income_events", "goals",
    }
    assert documented == set(user_data.PER_USER_TABLES)
