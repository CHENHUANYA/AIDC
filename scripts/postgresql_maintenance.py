from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from config_values import env_int
from repositories.postgres_auth import PostgresLoginThrottleRepository


DEFAULT_RETENTION_SECONDS = max(
    env_int("LOGIN_FAILURE_WINDOW_SECONDS", 300, minimum=1, maximum=86_400),
    env_int("LOGIN_LOCKOUT_SECONDS", 300, minimum=1, maximum=86_400),
)


def prune_login_throttles(
    repository: PostgresLoginThrottleRepository,
    *,
    retention_seconds: int,
    batch_size: int,
    max_batches: int,
    apply: bool,
    now: datetime | None = None,
) -> dict:
    if retention_seconds <= 0:
        raise ValueError("retention_seconds must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_batches <= 0:
        raise ValueError("max_batches must be positive")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    eligible_before = repository.count_expired(retention_seconds, now=current)
    removed = 0
    batches = 0
    if apply:
        while batches < max_batches:
            batch_removed = repository.cleanup_expired(
                retention_seconds,
                batch_size=batch_size,
                now=current,
            )
            removed += batch_removed
            batches += int(batch_removed > 0)
            if batch_removed < batch_size:
                break
    remaining = repository.count_expired(retention_seconds, now=current)
    completed = remaining == 0
    return {
        "status": "ok" if not apply or completed else "partial",
        "mode": "apply" if apply else "dry-run",
        "generated_at": current.isoformat(),
        "retention_seconds": retention_seconds,
        "batch_size": batch_size,
        "max_batches": max_batches,
        "eligible_before": eligible_before,
        "removed": removed,
        "remaining_eligible": remaining,
        "batches": batches,
        "completed": completed if apply else False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run bounded PostgreSQL maintenance without exposing connection details"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    cleanup = subparsers.add_parser(
        "cleanup-login-throttles",
        help="preview or delete expired shared login throttle rows",
    )
    cleanup.add_argument(
        "--retention-seconds",
        type=int,
        default=env_int(
            "LOGIN_THROTTLE_RETENTION_SECONDS",
            DEFAULT_RETENTION_SECONDS,
            minimum=1,
            maximum=2_592_000,
        ),
    )
    cleanup.add_argument(
        "--batch-size",
        type=int,
        default=env_int("LOGIN_THROTTLE_CLEANUP_BATCH_SIZE", 1000, minimum=1, maximum=100_000),
    )
    cleanup.add_argument(
        "--max-batches",
        type=int,
        default=env_int("LOGIN_THROTTLE_CLEANUP_MAX_BATCHES", 20, minimum=1, maximum=10_000),
    )
    cleanup.add_argument(
        "--apply",
        action="store_true",
        help="delete eligible rows; without this flag the command is read-only",
    )
    args = parser.parse_args()

    if args.command != "cleanup-login-throttles":
        parser.error(f"Unsupported command: {args.command}")
    if not os.getenv("DATABASE_URL", "").strip() and os.getenv("POSTGRES_ENABLED", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        parser.error("PostgreSQL is not configured")

    report = prune_login_throttles(
        PostgresLoginThrottleRepository(),
        retention_seconds=args.retention_seconds,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
