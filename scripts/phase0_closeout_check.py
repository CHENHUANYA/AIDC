from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "tests_tmp" / "phase0" / "phase0_closeout_report.json"
DEFAULT_BASETEMP = ROOT / "tests_tmp" / "phase0" / "pytest"


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    elapsed_seconds: float
    output_tail: str


def build_steps(*, basetemp: Path, skip_pytest: bool = False) -> list[Step]:
    steps = [
        Step("git-diff-check", ["git", "diff", "--check"]),
        Step("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        Step("mypy", [sys.executable, "-m", "mypy"]),
    ]
    if not skip_pytest:
        steps.append(
            Step("pytest", [sys.executable, "-m", "pytest", "-q", "--basetemp", str(basetemp)])
        )
    return steps


def console_safe(value: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return value.encode(encoding, errors="backslashreplace").decode(encoding)


def run_step(step: Step, *, verbose: bool) -> StepResult:
    print(f"[RUN ] {step.name}", flush=True)
    started = time.monotonic()
    completed = subprocess.run(
        step.command,
        cwd=ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    elapsed = time.monotonic() - started
    output = completed.stdout or ""
    status = "PASS" if completed.returncode == 0 else "FAIL"
    print(f"[{status:<4}] {step.name} exit={completed.returncode} elapsed={elapsed:.1f}s", flush=True)
    if verbose or completed.returncode != 0:
        print(console_safe(output[-6000:].rstrip()), flush=True)
    return StepResult(step.name, step.command, completed.returncode, round(elapsed, 3), output[-6000:])


def git_status() -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def write_report(path: Path, results: list[StepResult], status_lines: list[str], *, skipped_pytest: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(result.returncode == 0 for result in results) else "fail",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "working_tree_clean": not status_lines,
        "working_tree_entries": status_lines,
        "pytest_skipped": skipped_pytest,
        "steps": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 0 source-quality and test closeout gate")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--basetemp", type=Path, default=DEFAULT_BASETEMP)
    parser.add_argument("--skip-pytest", action="store_true", help="run only fast static closeout checks")
    parser.add_argument("--require-clean", action="store_true", help="fail when the Git working tree is not clean")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    args.basetemp.parent.mkdir(parents=True, exist_ok=True)
    status_lines = git_status()
    results = [run_step(step, verbose=args.verbose) for step in build_steps(basetemp=args.basetemp, skip_pytest=args.skip_pytest)]
    write_report(args.report, results, status_lines, skipped_pytest=args.skip_pytest)

    failed = [result.name for result in results if result.returncode != 0]
    if args.require_clean and status_lines:
        failed.append("working-tree-clean")
    print("\nAlarm RAG Phase 0 Closeout")
    print(f"report={args.report}")
    print(f"working_tree={'clean' if not status_lines else f'dirty ({len(status_lines)} entries)'}")
    print(f"RESULT={'FAIL' if failed else 'PASS'} failed={','.join(failed) if failed else '-'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
