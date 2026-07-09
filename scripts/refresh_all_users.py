"""Scheduled price refresh: refresh FX and quotes for every user with holdings.

Run by .github/workflows/price-refresh.yml on a cron (and manually via
workflow_dispatch). Replaces the sidebar button as the only way prices
update — the app now updates itself.

Requires VERMO_BACKEND=postgres and DATABASE_URL in the environment.
Exits non-zero if any user's refresh failed more symbols than it refreshed,
so a broken data provider turns the workflow red instead of rotting silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import portfolio_core
from monitoring import init_monitoring

init_monitoring("jobs")


def main() -> int:
    if not db.is_postgres():
        sys.exit("Refusing to run: VERMO_BACKEND=postgres and DATABASE_URL are required.")

    with db.connect() as connection:
        user_ids = [row["user_id"] for row in connection.execute(
            "SELECT DISTINCT user_id FROM holdings"
        ).fetchall()]

    if not user_ids:
        print("No users with holdings — nothing to refresh.")
        return 0

    worst_failure = 0
    for user_id in user_ids:
        run = portfolio_core.refresh_prices(user_id, connect_fn=db.connect)
        print(f"user {user_id[:8]}…  refreshed={run['refreshed']}  "
              f"fx-only={run['skipped']}  failed={run['failed']}  "
              f"EUR/INR={run['fx_rate_inr']}")
        if run["failed"] > run["refreshed"]:
            worst_failure = 1
    return worst_failure


if __name__ == "__main__":
    sys.exit(main())
