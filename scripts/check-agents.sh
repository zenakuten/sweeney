#!/usr/bin/env bash
# Verify the two agent files still match reference/agent-instructions.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp -r "$ROOT/agents/sweeney.md" "$tmp/claude.before"
cp -r "$ROOT/.github/agents/sweeney.agent.md" "$tmp/copilot.before"
"$ROOT/scripts/build-agents.sh" >/dev/null
if diff -q "$tmp/claude.before" "$ROOT/agents/sweeney.md" >/dev/null \
&& diff -q "$tmp/copilot.before" "$ROOT/.github/agents/sweeney.agent.md" >/dev/null; then
  echo "agent files are in sync with reference/agent-instructions.md"
else
  echo "agent files were stale; regenerated from reference/agent-instructions.md" >&2
  exit 1
fi
