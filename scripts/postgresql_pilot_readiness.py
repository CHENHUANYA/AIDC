from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.postgresql_backup import load_manifest, manifest_integrity, resolve_backup


ROOT = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
CLOCK_SKEW_HOURS = 5 / 60
PLACEHOLDER_PARTS = ("change-me", "replace-with", "placeholder", "example", "changeme")
SECRET_RULES = {
    "ADMIN_INITIAL_PASSWORD": 16,
    "ALARM_RAG_TRIGGER_TOKEN": 32,
    "N8N_ENCRYPTION_KEY": 32,
    "POSTGRES_PASSWORD": 20,
}
FORMAL_ENVIRONMENTS = {"pilot", "production"}


@dataclass(frozen=True)
class Check:
    category: str
    name: str
    status: str
    detail: str


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return not value or any(part in lowered for part in PLACEHOLDER_PARTS)


def tracked_paths() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def relative_name(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def secret_checks(env_path: Path, postgres_env_path: Path) -> list[Check]:
    tracked = tracked_paths()
    checks: list[Check] = []
    combined: dict[str, str] = {}
    for path in (env_path, postgres_env_path):
        name = relative_name(path)
        exists = path.is_file()
        checks.append(Check("secrets", f"env-file:{name}", "PASS" if exists else "FAIL", "present" if exists else "missing"))
        if exists:
            checks.append(
                Check(
                    "secrets",
                    f"env-untracked:{name}",
                    "FAIL" if name in tracked else "PASS",
                    "tracked by Git" if name in tracked else "not tracked by Git",
                )
            )
            combined.update(parse_env_file(path))

    secret_values: list[str] = []
    for key, minimum in SECRET_RULES.items():
        value = combined.get(key, "")
        ok = not is_placeholder(value) and len(value) >= minimum
        detail = f"configured length={len(value)}, required>={minimum}" if value else "missing"
        checks.append(Check("secrets", key, "PASS" if ok else "FAIL", detail))
        if ok:
            secret_values.append(value)

    unique = len(secret_values) == len(set(secret_values))
    checks.append(Check("secrets", "secret-uniqueness", "PASS" if unique else "FAIL", "all distinct" if unique else "duplicate secret values detected"))

    production = combined.get("ALARM_RAG_ENV", "").lower() == "production"
    checks.append(Check("deployment", "production-mode", "PASS" if production else "FAIL", combined.get("ALARM_RAG_ENV", "missing")))
    bind = combined.get("POSTGRES_BIND_ADDRESS", "")
    private_bind = bind in {"127.0.0.1", "localhost", "::1"}
    checks.append(Check("deployment", "postgres-private-bind", "PASS" if private_bind else "FAIL", bind or "missing"))
    return checks


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_hours(value: Any, now: datetime | None = None) -> float | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return (current - parsed).total_seconds() / 3600


def backup_checks(backup_path: str, max_age_hours: float) -> list[Check]:
    try:
        backup_dir = resolve_backup(backup_path)
        manifest = load_manifest(backup_dir)
        integrity = manifest_integrity(backup_dir, manifest)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return [Check("backup", "local-backup", "FAIL", str(exc))]

    integrity_ok = all(integrity[key] for key in ("dump_exists", "checksum", "size"))
    age = age_hours(manifest.get("created_at"))
    age_ok = age is not None and -CLOCK_SKEW_HOURS <= age <= max_age_hours
    return [
        Check("backup", "local-backup-integrity", "PASS" if integrity_ok else "FAIL", relative_name(backup_dir)),
        Check(
            "backup",
            "local-backup-age",
            "PASS" if age_ok else "FAIL",
            f"age_hours={age:.2f}, required<={max_age_hours:g}" if age is not None else "invalid created_at",
        ),
    ]


def load_json_report(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not path.is_file():
        return None, f"missing: {relative_name(path)}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "report root must be an object"
    return payload, ""


def boolean_check(category: str, name: str, payload: dict[str, Any], field: str) -> Check:
    ok = payload.get(field) is True
    return Check(category, name, "PASS" if ok else "FAIL", f"{field}={payload.get(field)!r}")


def evidence_age_check(category: str, payload: dict[str, Any], max_age_days: float) -> Check:
    age = age_hours(payload.get("completed_at"))
    limit = max_age_days * 24
    ok = age is not None and -CLOCK_SKEW_HOURS <= age <= limit
    detail = f"age_hours={age:.2f}, required<={limit:g}" if age is not None else "invalid completed_at"
    return Check(category, "evidence-age", "PASS" if ok else "FAIL", detail)


def formal_environment_check(category: str, payload: dict[str, Any]) -> Check:
    environment = payload.get("environment")
    ok = environment in FORMAL_ENVIRONMENTS
    return Check(
        category,
        "formal-environment",
        "PASS" if ok else "FAIL",
        f"environment={environment!r}, required=pilot|production",
    )


def soak_checks(path: Path, min_hours: float, max_age_days: float) -> list[Check]:
    payload, error = load_json_report(path)
    if payload is None:
        return [Check("soak", "report", "FAIL", error)]
    elapsed = payload.get("elapsed_seconds")
    elapsed_ok = isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed >= min_hours * 3600
    checks_payload = payload.get("checks")
    checks_ok = isinstance(checks_payload, dict) and bool(checks_payload) and all(value is True for value in checks_payload.values())
    failures = payload.get("failures")
    return [
        Check("soak", "status", "PASS" if payload.get("status") == "ok" else "FAIL", f"status={payload.get('status')!r}"),
        formal_environment_check("soak", payload),
        Check("soak", "actual-duration", "PASS" if elapsed_ok else "FAIL", f"elapsed_seconds={elapsed!r}, required>={min_hours * 3600:g}"),
        Check("soak", "runtime-checks", "PASS" if checks_ok else "FAIL", "all passed" if checks_ok else "missing or failed checks"),
        Check("soak", "zero-failures", "PASS" if failures == [] else "FAIL", f"failure_count={len(failures) if isinstance(failures, list) else 'invalid'}"),
        evidence_age_check("soak", payload, max_age_days),
    ]


def offsite_checks(path: Path, max_age_days: float) -> list[Check]:
    payload, error = load_json_report(path)
    if payload is None:
        return [Check("offsite-backup", "report", "FAIL", error)]
    checksum = payload.get("artifact_sha256")
    valid_checksum = isinstance(checksum, str) and bool(SHA256.fullmatch(checksum))
    return [
        Check("offsite-backup", "status", "PASS" if payload.get("status") == "ok" else "FAIL", f"status={payload.get('status')!r}"),
        formal_environment_check("offsite-backup", payload),
        boolean_check("offsite-backup", "encrypted", payload, "encrypted"),
        boolean_check("offsite-backup", "remote", payload, "remote"),
        boolean_check("offsite-backup", "immutable", payload, "immutable"),
        boolean_check("offsite-backup", "restore-verified", payload, "restore_verified"),
        boolean_check("offsite-backup", "database-restore-verified", payload, "database_restore_verified"),
        boolean_check("offsite-backup", "external-key-management", payload, "key_managed_externally"),
        boolean_check("offsite-backup", "retention-lock", payload, "retention_lock_verified"),
        boolean_check("offsite-backup", "separate-failure-domain", payload, "separate_failure_domain"),
        Check("offsite-backup", "artifact-checksum", "PASS" if valid_checksum else "FAIL", "valid SHA-256" if valid_checksum else "missing or invalid SHA-256"),
        evidence_age_check("offsite-backup", payload, max_age_days),
    ]


def metric_check(category: str, name: str, value: Any, maximum: float) -> Check:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= maximum
    return Check(category, name, "PASS" if ok else "FAIL", f"{name}_seconds={value!r}, required<={maximum:g}")


def pitr_checks(path: Path, max_rpo_seconds: float, max_rto_seconds: float, max_age_days: float) -> list[Check]:
    payload, error = load_json_report(path)
    if payload is None:
        return [Check("pitr", "report", "FAIL", error)]
    valid_target = parse_time(payload.get("recovery_target_time")) is not None
    return [
        Check("pitr", "status", "PASS" if payload.get("status") == "ok" else "FAIL", f"status={payload.get('status')!r}"),
        formal_environment_check("pitr", payload),
        Check("pitr", "recovery-target", "PASS" if valid_target else "FAIL", "valid" if valid_target else "missing or invalid"),
        boolean_check("pitr", "data-checks", payload, "data_checks_passed"),
        metric_check("pitr", "rpo", payload.get("rpo_seconds"), max_rpo_seconds),
        metric_check("pitr", "rto", payload.get("rto_seconds"), max_rto_seconds),
        evidence_age_check("pitr", payload, max_age_days),
    ]


def ha_checks(path: Path, max_rto_seconds: float, max_age_days: float) -> list[Check]:
    payload, error = load_json_report(path)
    if payload is None:
        return [Check("ha", "report", "FAIL", error)]
    return [
        Check("ha", "status", "PASS" if payload.get("status") == "ok" else "FAIL", f"status={payload.get('status')!r}"),
        formal_environment_check("ha", payload),
        boolean_check("ha", "failover-performed", payload, "failover_performed"),
        boolean_check("ha", "post-failover-writes", payload, "writes_verified_after_failover"),
        boolean_check("ha", "data-consistency", payload, "data_consistency_passed"),
        boolean_check("ha", "split-brain-prevention", payload, "split_brain_prevention_verified"),
        boolean_check("ha", "quorum", payload, "quorum_verified"),
        boolean_check("ha", "fencing", payload, "fencing_verified"),
        boolean_check("ha", "client-reconnect", payload, "client_reconnect_verified"),
        metric_check("ha", "rto", payload.get("rto_seconds"), max_rto_seconds),
        evidence_age_check("ha", payload, max_age_days),
    ]


def build_report(checks: list[Check]) -> dict[str, Any]:
    failures = sum(check.status == "FAIL" for check in checks)
    return {
        "status": "ready" if failures == 0 else "not_ready",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "pass": sum(check.status == "PASS" for check in checks),
            "fail": failures,
            "total": len(checks),
        },
        "checks": [asdict(check) for check in checks],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict PostgreSQL Pilot production-readiness gate")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--postgres-env-file", default=str(ROOT / ".env.postgresql"))
    parser.add_argument("--backup", default="")
    parser.add_argument("--backup-max-age-hours", type=float, default=24)
    parser.add_argument("--soak-report", default=str(ROOT / "exports" / "postgresql_pilot_soak.json"))
    parser.add_argument("--min-soak-hours", type=float, default=4)
    parser.add_argument("--offsite-report", default=str(ROOT / "exports" / "postgresql_offsite_backup.json"))
    parser.add_argument("--pitr-report", default=str(ROOT / "exports" / "postgresql_pitr_drill.json"))
    parser.add_argument("--ha-report", default=str(ROOT / "exports" / "postgresql_ha_drill.json"))
    parser.add_argument("--max-evidence-age-days", type=float, default=30)
    parser.add_argument("--max-rpo-seconds", type=float, default=300)
    parser.add_argument("--max-pitr-rto-seconds", type=float, default=3600)
    parser.add_argument("--max-ha-rto-seconds", type=float, default=300)
    parser.add_argument("--report", default=str(ROOT / "exports" / "postgresql_pilot_readiness.json"))
    args = parser.parse_args()

    checks: list[Check] = []
    checks.extend(secret_checks(Path(args.env_file), Path(args.postgres_env_file)))
    checks.extend(backup_checks(args.backup, args.backup_max_age_hours))
    checks.extend(soak_checks(Path(args.soak_report), args.min_soak_hours, args.max_evidence_age_days))
    checks.extend(offsite_checks(Path(args.offsite_report), args.max_evidence_age_days))
    checks.extend(pitr_checks(Path(args.pitr_report), args.max_rpo_seconds, args.max_pitr_rto_seconds, args.max_evidence_age_days))
    checks.extend(ha_checks(Path(args.ha_report), args.max_ha_rto_seconds, args.max_evidence_age_days))
    report = build_report(checks)
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
