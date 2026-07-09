"""Error monitoring: Sentry, enabled only when SENTRY_DSN is configured.

Both processes call init_monitoring() at startup. Without a DSN this is a
no-op, so local dev and tests never send anything. Set SENTRY_DSN in .env
locally, in Streamlit Cloud secrets for the hosted app, and as a GitHub
Actions secret if the scheduled jobs should report too.

send_default_pii stays False: this app holds financial data — stack traces
and breadcrumbs go to Sentry, user identities and request bodies do not.
"""

from __future__ import annotations

import db


def init_monitoring(component: str) -> bool:
    """Initialize Sentry for one process ("streamlit", "fastapi", "jobs").
    Returns True when monitoring is active."""
    dsn = db._env("SENTRY_DSN")
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=db._env("VERMO_ENV") or "production",
        send_default_pii=False,
        traces_sample_rate=0.0,  # errors only; performance tracing is paid-tier noise for now
    )
    sentry_sdk.set_tag("component", component)
    return True
