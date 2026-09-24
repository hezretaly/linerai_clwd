"""Migrations for a dealer group's database. The runner is `app/migrate.py`."""

from app.migrate import run_env

run_env("store")
