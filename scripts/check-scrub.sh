#!/usr/bin/env bash
# Sweeney must never reference the UT2004 C++ source, which is not publicly available.
# Findings from it are welcome -- stated as engine behaviour, without the citation.
#
# Exits non-zero if a reference is found. Run before committing.

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
