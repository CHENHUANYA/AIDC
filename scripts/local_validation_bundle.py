import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent


@dataclass
class Step:
    name: str
    command: list[str]


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    elapsed_seconds: float
    output: str


def python_cmd(script: str, *args: str) -> list[str]:
    return [sys.executable, str(SCRIPT_DIR / script), *args]


def build_steps(args: argparse.Namespace) -> list[Step]:
    preflight_args = ["--require-model-cache"] if args.require_model_cache else []
    acceptance_args = [
        "--base-url",
        args.base_url,
        "--manual",
        args.manual,
        "--alarm-code",
        args.alarm_code,
        "--timeout",
        str(args.timeout),
        "--retention-days",
        str(args.retention_days),
    ]
    if args.pdf:
        acceptance_args.extend(["--pdf", args.pdf, "--pdf-max-mb", str(args.pdf_max_mb)])
    if args.create_backup:
        acceptance_args.append("--create-backup")

    steps = [
        Step("preflight", python_cmd("preflight_check.py", *preflight_args)),
        Step("n8n-workflow", python_cmd("n8n_workflow_check.py")),
        Step("standalone-acceptance", python_cmd("standalone_acceptance.py", *acceptance_args)),
        Step("ui-evidence", python_cmd("ui_evidence_check.py", "--report", args.ui_report, "--screenshots", args.ui_screenshots)),
        Step(
            "backup-health",
            python_cmd(
                "data_maintenance.py",
                "backup-health",
                "--max-age-hours",
                str(args.backup_max_age_hours),
                "--require-components",
                args.backup_components,
                "--verify",
            ),
        ),
    ]
    if args.restore_smoke:
        steps.append(Step("restore-smoke", python_cmd("data_maintenance.py", "restore-smoke", "--cleanup")))
    return steps


def run_step(step: Step, verbose: bool) -> StepResult:
    print(f"[RUN ] {step.name}", flush=True)
    started = time.monotonic()
    completed = subprocess.run(
        step.command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    elapsed = time.monotonic() - started
    output = completed.stdout or ""
    status = "PASS" if completed.returncode == 0 else "FAIL"
    print(f"[{status:<4}] {step.name} exit={completed.returncode} elapsed={elapsed:.1f}s", flush=True)
    if verbose or completed.returncode != 0:
        text = output.strip()
        if text:
            print("-" * 72)
            print(text[-4000:])
            print("-" * 72)
    return StepResult(step.name, step.command, completed.returncode, elapsed, output)


def command_text(command: list[str]) -> str:
    return " ".join(command)


def print_summary(results: list[StepResult], restore_smoke: bool) -> None:
    failed = [result for result in results if result.returncode != 0]
    print("\nAlarm RAG Local Validation Bundle")
    print("-" * 72)
    print(f"timestamp={datetime.now().isoformat(timespec='seconds')}")
    print(f"root={ROOT}")
    for result in results:
        status = "PASS" if result.returncode == 0 else "FAIL"
        print(f"[{status:<4}] {result.name:<24} exit={result.returncode:<3} elapsed={result.elapsed_seconds:.1f}s")
    if not restore_smoke:
        print("[SKIP] restore-smoke             opt-in only; touches backup staging")
    print("-" * 72)
    print(
        "RESULT={result} PASS={pass_count} FAIL={fail_count} SKIP={skip_count}".format(
            result="FAIL" if failed else "PASS",
            pass_count=sum(result.returncode == 0 for result in results),
            fail_count=len(failed),
            skip_count=0 if restore_smoke else 1,
        )
    )
    if failed:
        print("failed_steps=" + ",".join(result.name for result in failed))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local reliability validation checks for handoff screenshots")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pdf", default="", help="optional PDF forwarded to standalone acceptance")
    parser.add_argument("--pdf-max-mb", type=float, default=1.0)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--create-backup", action="store_true", help="let standalone acceptance create and verify a real backup")
    parser.add_argument("--restore-smoke", action="store_true", help="opt in to restore-smoke staging extraction")
    parser.add_argument("--no-require-model-cache", dest="require_model_cache", action="store_false")
    parser.set_defaults(require_model_cache=True)
    parser.add_argument("--ui-report", default=str(ROOT / "tests_tmp" / "browser_e2e" / "browser_e2e_report.json"))
    parser.add_argument("--ui-screenshots", default=str(ROOT / "tests_tmp" / "browser_e2e" / "screenshots"))
    parser.add_argument("--backup-max-age-hours", type=float, default=72.0)
    parser.add_argument("--backup-components", default="alarm_db,data,n8n_data,qdrant_data")
    parser.add_argument("--verbose", action="store_true", help="print full child command output")
    args = parser.parse_args()

    results = [run_step(step, args.verbose) for step in build_steps(args)]
    print_summary(results, restore_smoke=args.restore_smoke)
    return 1 if any(result.returncode != 0 for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
