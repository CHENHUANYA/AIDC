from pathlib import Path

from scripts import phase0_closeout_check


def test_phase0_closeout_runs_source_and_test_gates():
    steps = phase0_closeout_check.build_steps(basetemp=Path("tests_tmp/phase0/pytest"))

    assert [step.name for step in steps] == ["git-diff-check", "ruff", "mypy", "pytest"]
    assert steps[0].command == ["git", "diff", "--check"]
    assert steps[-1].command[-2] == "--basetemp"
    assert Path(steps[-1].command[-1]) == Path("tests_tmp/phase0/pytest")


def test_phase0_closeout_can_skip_only_the_slow_test_gate():
    steps = phase0_closeout_check.build_steps(basetemp=Path("tests_tmp/phase0/pytest"), skip_pytest=True)

    assert [step.name for step in steps] == ["git-diff-check", "ruff", "mypy"]
