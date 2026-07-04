# Security Contract

> The negotiated definition of "secure enough" for this loop. The planner owns
> this file; the generator writes fixes to satisfy it; `verify.py` grades
> against it. The contract is the boundary that gets graded (LOOPS.md §III).
>
> A human edits this file only when an **assertion** is wrong — not when a build
> fails to meet it.

Each assertion is testable. `verify.py` mechanically checks the ones marked
`[auto]`; `[manual]` ones are audited by a human/agent and recorded in `log.md`.

## Assertions

- **SEC-001 `[auto]` — Workflow condition expressions cannot escape to RCE.**
  A workflow condition such as
  `().__class__.__bases__[0].__subclasses__()` must evaluate to `false`, never
  reach `object.__subclasses__()`, and never execute arbitrary code.
  Legitimate conditions (`outputs['x'] == 'y'`, `'e' in outputs['x']`,
  boolean combinations) must still evaluate correctly.
  → Fixed: `workflow/engine.py` now uses the AST-allowlist `safe_eval_expr`.

- **SEC-002 `[auto]` — The workflow test suite stays green.**
  `tests/workflow/` must pass with the hardened evaluator (no behavioural
  regression in condition/loop nodes).

- **SEC-003 `[manual]` — Every `eval`/`exec` outside a sandbox is justified.**
  All `eval`/`exec` call sites must either (a) use `safe_eval_expr`, or
  (b) be a deliberate code-execution sandbox (benchmark scorers in
  `evals/scorers/*`, `code_interpreter`, `repl`) whose purpose *is* running
  untrusted code, or (c) be a vulnerable-by-design test fixture
  (`evals/datasets/security_scanner.py`). No unaccounted-for site may exist.
  → Audited 2026-07-04: all current sites fall under (a)/(b)/(c). Holds.

- **SEC-004 `[manual]` — No hardcoded secrets in source.**
  No API keys, tokens, or credentials committed to `src/`. Secrets come from
  config/env/keyring only.

- **SEC-005 `[manual]` — External-boundary inputs are validated.**
  User input and external-API responses crossing into tools/engines pass
  through the existing security stack (scanners, boundary guard, taint,
  capability policy) — nothing bypasses `ToolExecutor`.

## Definition of done for this contract

`verify.py` reports `score == 1.0` (all `[auto]` assertions pass) **and** every
`[manual]` assertion has a dated, holding entry in `log.md`.
