"""Force the SQLite backend for the whole test session.

db.py only talks to Postgres when VERMO_BACKEND=postgres — but a developer's
.env may set exactly that once the app runs on Supabase. Tests must stay
hermetic (tmp SQLite files), so pin the backend before any module under test
is imported. Environment variables take precedence over .env in db._env().
"""

import os

os.environ["VERMO_BACKEND"] = "sqlite"
