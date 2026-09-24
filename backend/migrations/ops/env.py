"""Migrations for Liner's own database. The runner is `app/migrate.py`."""

from app.migrate import run_env

run_env("ops")
