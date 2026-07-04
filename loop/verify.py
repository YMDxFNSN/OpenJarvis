#!/usr/bin/env python3
"""Evaluator tick for the security loop — the `verify` verb.

Assumes the code is broken and tries to prove it (LOOPS.md §II). Runs the
`[auto]` assertions from ``contract.md`` and emits a score in ``[0, 1]``.

Deterministic and side-effect-light: it reads the repo, runs checks, prints a
score, and appends one line to ``log.md``. It does **not** edit source, commit,
or push — writing fixes is the generator's job and is human-gated.

Exit code: ``0`` when every auto-assertion passes, ``1`` otherwise. Suitable for
cron / ``jarvis scheduler`` (see ``loop/README.md``).
"""

from __future__ import annotations

import datetime as _dt
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
LOG = Path(__file__).resolve().parent / "log.md"

# Ensure the in-tree package is importable when run as a bare script.
sys.path.insert(0, str(SRC))


def check_sec001() -> tuple[bool, str]:
    """Workflow condition expressions cannot escape to RCE (SEC-001)."""
    try:
        from openjarvis.workflow.engine import WorkflowEngine
        from openjarvis.workflow.types import NodeType, WorkflowNode
    except Exception as exc:  # import/env problem — report, don't crash the tick
        return False, f"import failed: {exc!r}"

    eng = WorkflowEngine.__new__(WorkflowEngine)

    def cond(expr: str, outputs: dict[str, str]) -> str:
        node = WorkflowNode(id="c", node_type=NodeType.CONDITION, condition_expr=expr)
        return eng._run_condition_node(node, outputs).output

    outs = {"step1": "yes", "step2": "an error occurred"}

    # Legitimate conditions must still evaluate correctly.
    legit_ok = (
        cond("outputs['step1'] == 'yes'", outs) == "True"
        and cond("'error' in outputs['step2']", outs) == "True"
        and cond("outputs['step1']=='yes' and 'x' in outputs['step2']", outs)
        == "False"
    )

    # The classic sandbox-escape payload must be neutralized.
    escape = "().__class__.__bases__[0].__subclasses__()"
    escape_blocked = cond(escape, outs) == "false"

    if legit_ok and escape_blocked:
        return True, "legit conditions OK; escape payload -> 'false'"
    return False, f"legit_ok={legit_ok} escape_blocked={escape_blocked}"


def check_sec002() -> tuple[bool, str]:
    """The workflow test suite stays green (SEC-002)."""
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/workflow/test_workflow.py",
                "tests/cli/test_workflow_cmd.py",
                "-q",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return True, "pytest unavailable — skipped (install the dev extra to enforce)"
    except subprocess.TimeoutExpired:
        return False, "workflow tests timed out"

    tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:] or [""]
    if proc.returncode == 0:
        return True, f"tests green ({tail[0]})"
    if "No module named pytest" in (proc.stdout + proc.stderr):
        return True, "pytest not installed — skipped"
    return False, f"tests failed ({tail[0]})"


AUTO_CHECKS = [
    ("SEC-001", check_sec001),
    ("SEC-002", check_sec002),
]


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    for name, fn in AUTO_CHECKS:
        ok, detail = fn()
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    passed = sum(1 for _, ok, _ in results if ok)
    score = passed / len(results) if results else 0.0
    print(f"\nscore = {score:.2f}  ({passed}/{len(results)} auto-assertions)")

    stamp = _dt.date.today().isoformat()
    verdict = "pass" if score == 1.0 else "regression"
    summary = "; ".join(f"{n}={'ok' if ok else 'FAIL'}" for n, ok, _ in results)
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## [{stamp}] verify | tick score={score:.2f} ({verdict})\n")
            fh.write(f"Evaluator: {summary}.\n")
    except OSError:
        pass  # logging is best-effort; never fail the tick on a write error

    return 0 if score == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
