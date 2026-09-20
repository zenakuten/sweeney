#!/usr/bin/env bash
# Emit both agent files from the single shared body in reference/agent-instructions.md,
# so the Claude and Copilot versions cannot drift apart.
#
# Run after editing reference/agent-instructions.md. check-agents.sh verifies they match.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BODY="$ROOT/reference/agent-instructions.md"

DESC="UT2004 UnrealScript and modding expert. Use for any work on UT2004 mods, mutators, gametypes or content: writing and debugging .uc, the UCC compile loop, replication and netcode, GUI and HUD, packages and textures, .t3d and UnrealEd map work, and testing against a running server."

mkdir -p "$ROOT/agents" "$ROOT/.github/agents"

{
  echo "---"
  echo "name: sweeney"
  echo "description: $DESC"
  echo "tools: Bash, Read, Write, Edit, Grep, Glob, Skill, WebFetch, WebSearch"
  echo "---"
  echo
  echo "<!-- Generated from reference/agent-instructions.md by scripts/build-agents.sh. Edit that, not this. -->"
  echo
  cat "$BODY"
} > "$ROOT/agents/sweeney.md"

{
  echo "---"
  echo "name: sweeney"
  echo "description: $DESC"
  echo "---"
  echo
  echo "<!-- Generated from reference/agent-instructions.md by scripts/build-agents.sh. Edit that, not this. -->"
  echo
  cat "$BODY"
} > "$ROOT/.github/agents/sweeney.agent.md"

echo "wrote agents/sweeney.md and .github/agents/sweeney.agent.md"
