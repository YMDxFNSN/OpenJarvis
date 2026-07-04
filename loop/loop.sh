#!/usr/bin/env bash
# One full loop tick: gather -> reason -> act -> verify -> repeat (LOOPS.md §I).
#
# This script runs the DETERMINISTIC half of the loop — gather state and run the
# evaluator. The `act` (generator) step, when a manual assertion turns up a
# finding, is intentionally NOT automated here: writing + pushing code fixes on
# a schedule needs an explicit human go-ahead (LOOPS.md §V — the human owns the
# contract, not the build). Schedule this via cron or `jarvis scheduler`.
#
# Usage:   loop/loop.sh
# Exit:    0 when the security contract holds, non-zero otherwise.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

echo "== gather =="
echo "repo:    $REPO"
echo "branch:  $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
echo "head:    $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "dirty:   $(test -n "$(git status --porcelain 2>/dev/null)" && echo yes || echo no)"

echo
echo "== verify =="
# Prefer the project's environment if uv is available; fall back to python3.
if command -v uv >/dev/null 2>&1; then
  uv run --no-sync python loop/verify.py
else
  python3 loop/verify.py
fi
rc=$?

echo
echo "== report =="
if [ "$rc" -eq 0 ]; then
  echo "contract holds (score 1.0). next bottleneck: see loop/progress.md"
else
  echo "REGRESSION — a security assertion failed. read loop/log.md and fix before shipping."
fi
exit "$rc"
