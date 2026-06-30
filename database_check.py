"""Convenience entry point for the PostgreSQL schema health check."""

from scripts.database_check import main


if __name__ == "__main__":
    raise SystemExit(main())
