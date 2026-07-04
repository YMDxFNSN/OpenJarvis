# Progress

_Resume point. Read this + `contract.md` + `feature_list.json` to reconstruct
state after any crash or new session (LOOPS.md §IV)._

**Updated:** 2026-07-04
**Loop:** openjarvis-security
**Automated score (last tick):** 1.0 — all `[auto]` assertions pass.

## Done
- **SEC-001** — Replaced the escapable `eval` in `workflow/engine.py`
  `_run_condition_node` with the AST-allowlist `safe_eval_expr`. Verified:
  legitimate conditions still evaluate; the classic
  `().__class__.__bases__[0].__subclasses__()` escape now returns `false`.
- **SEC-002** — `tests/workflow/test_workflow.py` + `tests/cli/test_workflow_cmd.py`:
  19 passed, no regression.
- **SEC-003** — Audited all `eval`/`exec` sites; each is a benchmark sandbox, a
  vulnerable-by-design dataset fixture, or now uses `safe_eval_expr`. No
  unaccounted site.

## Next bottleneck (LOOPS.md §IX — the bottleneck always moves)
Coding for SEC-001 is closed; verification is automated. The next thing to make
visible is the **manual** surface:
- **SEC-004** — secret scan across `src/` (run `/security-review`).
- **SEC-005** — confirm no tool path bypasses `ToolExecutor` / boundary guard.

These two are `[manual]`: they need the *generator* role (a model editing code)
if any finding turns up. That step is human-gated in this harness — it does not
auto-push. Hand it the contract and let it run once a finding is confirmed.
