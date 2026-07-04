# Log

_Append-only. One entry per operation. Format: `## [YYYY-MM-DD] op | title`._

## [2026-07-04] init | Loop harness created
Planner: turned "fix all security issues + run on a loop" into a testable
`contract.md` (SEC-001..SEC-005). State files seeded. Roles separated
(planner/generator/evaluator). Harness is deletable scaffolding.

## [2026-07-04] act | SEC-001 fix workflow condition RCE
Generator: `workflow/engine.py` `_run_condition_node` no longer calls
`eval(expr, {"__builtins__": {}}, ...)` (escapable via attribute walks to
`object.__subclasses__()`). Now delegates to the AST-allowlist `safe_eval_expr`
from `tools/templates/loader.py`. Catches `ValueError/SyntaxError/KeyError/TypeError`
→ `"false"`.

## [2026-07-04] verify | SEC-001 + SEC-002 pass
Evaluator: legitimate conditions evaluate correctly (`==`, `in`, boolean).
Escape payload `().__class__.__bases__[0].__subclasses__()` → `'false'`.
Confirmed the OLD eval reached 716 subclasses (the RCE gateway). Test suite:
`tests/workflow` + `tests/cli/test_workflow_cmd` → 19 passed. Score = 1.0.

## [2026-07-04] audit | SEC-003 eval/exec sites justified
Evaluator: every `eval`/`exec` in `src/` is a benchmark code-execution sandbox
(`evals/scorers/*`, `code_interpreter`, `repl`), a vulnerable-by-design dataset
fixture (`evals/datasets/security_scanner.py`), or now uses `safe_eval_expr`
(`templates/loader.py`, `workflow/engine.py`). No unaccounted site. Holds.

## [2026-07-04] verify | tick score=1.00 (pass)
Evaluator: SEC-001=ok; SEC-002=ok.
