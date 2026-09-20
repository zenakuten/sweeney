#!/usr/bin/env bash
# Nothing in this repo may cite the engine's C++ source.
#
# Reading a local copy is allowed (see reference/agent-instructions.md) -- writing a
# path, file name or line number from it is not. It is private to one machine, so the
# citation is useless to every other reader. Keep the finding, state it as engine
# behaviour, drop the reference.
#
# This check is the enforcement arm of that rule. Exits non-zero on a hit.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Paths and citation shapes that give the private source away.
PATTERN='engine C++ source|[A-Za-z0-9_]+/Src/[A-Za-z0-9_]+\.(cpp|h)\b|\b[A-Za-z0-9_]+\.cpp\b|\bUn[A-Z][A-Za-z0-9_]*\.cpp'

hits=$(grep -rnIE "$PATTERN" "$ROOT" \
  --exclude-dir=.git \
  --exclude-dir=__pycache__ \
  --exclude=check-scrub.sh \
  --exclude=Proto.md \
  2>/dev/null)

if [ -n "$hits" ]; then
  echo "C++ source references found -- remove them before committing:" >&2
  echo >&2
  printf '%s\n' "$hits" >&2
  echo >&2
  echo "Keep the finding, drop the citation: state it as engine behaviour." >&2
  exit 1
fi

echo "scrub check clean: no C++ source references"
