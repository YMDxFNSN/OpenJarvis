# `loop/` — A Loop-Engineering Harness for OpenJarvis

> Deletable scaffolding. This directory is a **harness**, not product code. Per
> LOOPS.md §VIII and the Loop-Engineering playbook: re-read it against each new
> model release and delete anything the model now does for free. If it grows
> monotonically, you've stopped reading it.

## What this is

A concrete implementation of the four-layer stack from the field notes, applied
to *keeping OpenJarvis secure over many iterations*:

| Layer | Question it answers | Where it lives here |
|---|---|---|
| Prompt eng | What do I tell the model? | (the task you type) |
| Context eng | What goes in the window now? | `contract.md` + `progress.md` (read at each tick) |
| **Harness eng** | Which tools, which checks, what counts as done? | `verify.py`, `feature_list.json` |
| **Loop eng** | How to make it run itself, over and over? | `loop.sh` + a scheduler entry |

The loop is five verbs: **gather → reason → act → verify → repeat.** Everything
else is a footnote on those.

## The roles are separated (LOOPS.md §II)

- **Planner** — turns "make it secure" into `contract.md` (testable assertions).
  Never writes fixes.
- **Generator** — writes the fix for one unchecked assertion. Forbidden from
  grading its own work.
- **Evaluator** — `verify.py`. Assumes the code is broken and tries to prove it:
  runs the security assertions and the test suite, emits a score in `[0,1]`.

A human is inserted **only when the contract itself is wrong** (§V), never to
babysit an individual build.

## State lives on disk, not in context (LOOPS.md §IV)

The loop can crash, lose its session, and resume by reading three files:

- **`contract.md`** — the negotiated definition of "secure enough." The boundary.
- **`feature_list.json`** — machine-readable work items + status (the checklist).
- **`progress.md`** — current position; what's done, what's next.
- **`log.md`** — append-only history: `## [YYYY-MM-DD] op | title`.

If you cannot describe the state in these files, the state is too complicated.

## Running one tick (the `verify` verb)

```bash
python loop/verify.py            # runs the contract's automated assertions
```

It exits `0` when the contract holds, prints a `score` in `[0,1]`, and appends a
line to `log.md`. This is the honest, deterministic half of the loop.

## Running the loop (`loop eng` = scheduling on the harness)

```bash
loop/loop.sh                     # one full tick: gather → verify → report
```

Schedule it so it "runs while you sleep" — the loop is a thing that runs on a
timer, not a prompt you type once:

```bash
# cron: verify the security contract every 6 hours
0 */6 * * * cd /path/to/OpenJarvis && loop/loop.sh >> loop/cron.out 2>&1
```

Or drive it through OpenJarvis's own scheduler once you want the *generator*
(model) in the loop too:

```bash
jarvis scheduler add --name security-loop --cron "0 */6 * * *" --agent operative
```

## The generator step is intentionally human/agent-gated

`verify.py` runs on its own. Writing a *new* fix (the generator role) puts a
model in the loop that edits code — this harness does **not** auto-commit or
auto-push those edits. That's the one place the playbook says to keep a human on
the contract. Wire an autonomous generator+push only with an explicit, written
go-ahead.
