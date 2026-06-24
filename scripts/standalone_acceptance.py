import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import load_project_env


load_project_env()


@dataclass
class Step:
    name: str
    command: list[str]


@dataclass
class StepResult:
    name: str
    returncode: int


def python_cmd(script: str, *args: str) -> list[str]:
    return [sys.executable, str(SCRIPT_DIR / script), *args]


def build_steps(args: argparse.Namespace) -> list[Step]:
    base_args = ["--base-url", args.base_url]
    live_args = [*base_args, "--manual", args.manual, "--alarm-code", args.alarm_code, "--timeout", str(args.timeout)]
    smoke_args = [*live_args]
    if args.pdf:
        smoke_args.extend(["--pdf", args.pdf, "--pdf-max-mb", str(args.pdf_max_mb)])

    steps = [
        Step("preflight", python_cmd("preflight_check.py", "--require-model-cache")),
        Step("model-cache", python_cmd("model_cache.py", "check")),
        Step("smoke", python_cmd("smoke_test.py", *smoke_args)),
        Step("regression", python_cmd("regression_checks.py", *live_args)),
        Step("role-console", python_cmd("role_console_smoke.py", *base_args, "--timeout", str(args.timeout))),
        Step("n8n-workflow", python_cmd("n8n_workflow_check.py")),
        Step("backup:list", python_cmd("data_maintenance.py", "list-backups", "--limit", "5")),
    ]
    if args.create_backup:
        steps.append(Step("backup:create", python_cmd(
            "data_maintenance.py",
            "backup-runtime",
            "--include-mock-data",
            "--retention-days",
            str(args.retention_days),
        )))
        steps.append(Step("backup:verify-latest", python_cmd("data_maintenance.py", "verify-runtime-backup")))
        steps.append(Step("backup:health", python_cmd("data_maintenance.py", "backup-health", "--verify")))
        if args.restore_smoke:
            steps.append(Step("backup:restore-smoke", python_cmd("data_maintenance.py", "restore-smoke", "--cleanup")))
    else:
        steps.append(Step("backup:dry-run", python_cmd(
            "data_maintenance.py",
            "--dry-run",
            "backup-runtime",
            "--include-mock-data",
            "--retention-days",
            str(args.retention_days),
        )))
    return steps


def run_step(step: Step) -> StepResult:
    print(f"\n=== {step.name} ===", flush=True)
    print(" ".join(step.command), flush=True)
    completed = subprocess.run(step.command, cwd=ROOT, check=False)
    return StepResult(step.name, completed.returncode)


def print_summary(results: list[StepResult]) -> None:
    print("\nAlarm RAG Standalone Acceptance")
    print("-" * 72)
    for result in results:
        status = "PASS" if result.returncode == 0 else "FAIL"
        print(f"[{status:<4}] {result.name:<22} exit={result.returncode}")
    print("-" * 72)
    print(
        "PASS={pass_count} FAIL={fail_count}".format(
            pass_count=sum(result.returncode == 0 for result in results),
            fail_count=sum(result.returncode != 0 for result in results),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Alarm RAG standalone deployment acceptance checks")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pdf", default="", help="optional PDF for smoke upload; guarded by --pdf-max-mb")
    parser.add_argument("--pdf-max-mb", type=float, default=1.0)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--create-backup", action="store_true", help="create and verify a real backup instead of dry-run")
    parser.add_argument("--restore-smoke", action="store_true", help="opt in to restore-smoke staging extraction after backup creation")
    args = parser.parse_args()
    if args.restore_smoke and not args.create_backup:
        parser.error("--restore-smoke requires --create-backup")

    results = [run_step(step) for step in build_steps(args)]
    print_summary(results)
    return 1 if any(result.returncode != 0 for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
