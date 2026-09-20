#!/usr/bin/env bash
# Sweeney setup: fetch the UT2004 script source, locate a UT2004 install,
# and write ~/.sweeney/config.json.
#
# Assumes a clean machine. The engine source is cloned, never discovered:
# the only directory reused is one a previous run of this script created.
#
# Safe to re-run. Never writes into a UT2004 install.

set -uo pipefail

ENGINE_REPO="https://github.com/deaod/ut2004"
SWEENEY_HOME="${SWEENEY_HOME:-$HOME/.sweeney}"
ENGINE_DIR="${SWEENEY_ENGINE:-$SWEENEY_HOME/engine/ut2004}"
CONFIG="$SWEENEY_HOME/config.json"
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INSTALL_COPILOT=0
for arg in "$@"; do
  case "$arg" in
    --install) ;;
    copilot|--install=copilot) INSTALL_COPILOT=1 ;;
    -h|--help)
      sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
      echo
      echo "Usage: setup.sh [--install copilot]"
      exit 0 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
warn() { printf '  warn  %s\n' "$*"; }
err()  { printf '  ERROR %s\n' "$*" >&2; }

say "Sweeney setup"
say

# ---------------------------------------------------------------- engine source
say "Engine script source"
if [ -d "$ENGINE_DIR/.git" ]; then
  say "  updating $ENGINE_DIR"
  if git -C "$ENGINE_DIR" pull --quiet --ff-only 2>/dev/null; then
    ok "up to date"
  else
    warn "pull failed; keeping the existing checkout"
  fi
elif [ -d "$ENGINE_DIR" ] && [ -n "$(ls -A "$ENGINE_DIR" 2>/dev/null)" ]; then
  warn "$ENGINE_DIR exists but is not a git checkout; leaving it alone"
else
  command -v git >/dev/null 2>&1 || { err "git is not installed"; exit 1; }
  say "  cloning $ENGINE_REPO"
  mkdir -p "$(dirname "$ENGINE_DIR")"
  if git clone --depth 1 --quiet "$ENGINE_REPO" "$ENGINE_DIR"; then
    ok "cloned to $ENGINE_DIR"
  else
    err "clone failed -- check network access to github.com"
    exit 1
  fi
fi

# The repo is flat: <Package>/<Class>.uc, with no Classes/ subdirectory.
if [ -f "$ENGINE_DIR/Engine/Actor.uc" ]; then
  n_uc=$(find "$ENGINE_DIR" -name '*.uc' 2>/dev/null | wc -l)
  ok "verified: Engine/Actor.uc present, $n_uc .uc files"
  ENGINE_OK=1
else
  err "$ENGINE_DIR does not look like the UT2004 script source"
  err "expected Engine/Actor.uc"
  ENGINE_OK=0
fi
say

# -------------------------------------------------------------- UT2004 install
# An install is identified by System/UCC.exe next to System/Engine.u.
say "UT2004 install"
INSTALL_ROOT=""
is_install() { [ -f "$1/System/UCC.exe" ] && [ -f "$1/System/Engine.u" ]; }

d="$PWD"
while [ "$d" != "/" ]; do
  if is_install "$d"; then INSTALL_ROOT="$d"; break; fi
  d="$(dirname "$d")"
done

if [ -z "$INSTALL_ROOT" ]; then
  for c in "$HOME"/UT2004* "$HOME"/ut2004* "$HOME"/.steam/steam/steamapps/common/"Unreal Tournament 2004"; do
    [ -d "$c" ] || continue
    if is_install "$c"; then INSTALL_ROOT="$c"; break; fi
  done
fi

UCC_BITS=""; UCC_ID=""
if [ -n "$INSTALL_ROOT" ]; then
  ok "found $INSTALL_ROOT"
  # A 32-bit UCC.exe hangs building large packages. Worth knowing up front.
  if command -v file >/dev/null 2>&1; then
    case "$(file -b "$INSTALL_ROOT/System/UCC.exe" 2>/dev/null)" in
      *x86-64*|*PE32+*) UCC_BITS=64; ok "UCC.exe is 64-bit" ;;
      *PE32*)           UCC_BITS=32; warn "UCC.exe is 32-bit -- it hangs building large packages" ;;
      *)                warn "could not determine UCC.exe architecture" ;;
    esac
  fi
  # UCC is community-patched and differs between builds, so record which one
  # this is. Claims about compiler behaviour are only ever about one binary.
  UCC_ID="$(stat -c '%s' "$INSTALL_ROOT/System/UCC.exe" 2>/dev/null) bytes, mtime $(stat -c '%y' "$INSTALL_ROOT/System/UCC.exe" 2>/dev/null | cut -d. -f1)"
  [ -n "$UCC_ID" ] && ok "UCC.exe: $UCC_ID"
else
  warn "no UT2004 install found"
  warn "run this from inside one, or set install_root in $CONFIG by hand"
fi
say

# --------------------------------------------------------------- python toolchains
say "Python toolchains"
UT3CONV=""; UTUPSCALER=""
if [ -n "$INSTALL_ROOT" ]; then
  [ -f "$INSTALL_ROOT/ut3converter/ut3conv.py" ] && UT3CONV="$INSTALL_ROOT/ut3converter"
  [ -f "$INSTALL_ROOT/utupscaler/utup.py" ]      && UTUPSCALER="$INSTALL_ROOT/utupscaler"
fi
[ -n "$UT3CONV" ]    && ok "ut3converter at $UT3CONV"       || warn "ut3converter not found (optional)"
[ -n "$UTUPSCALER" ] && ok "utupscaler at $UTUPSCALER"      || warn "utupscaler not found (optional)"
say

# ------------------------------------------------------------------------ config
mkdir -p "$SWEENEY_HOME"
ENGINE_DIR="$ENGINE_DIR" INSTALL_ROOT="$INSTALL_ROOT" UCC_BITS="$UCC_BITS" \
UCC_ID="$UCC_ID" \
UT3CONV="$UT3CONV" UTUPSCALER="$UTUPSCALER" PLUGIN_ROOT="$PLUGIN_ROOT" \
CONFIG="$CONFIG" python3 - <<'PY'
import json, os, datetime

def val(k):
    v = os.environ.get(k, "")
    return v if v else None

cfg = {
    "_comment": "Written by sweeney scripts/setup.sh. Safe to edit by hand.",
    "generated": datetime.datetime.now().isoformat(timespec="seconds"),
    "engine_source": val("ENGINE_DIR"),
    "install_root": val("INSTALL_ROOT"),
    "client_install_root": None,
    "ucc_bits": int(os.environ["UCC_BITS"]) if os.environ.get("UCC_BITS") else None,
    "ucc_id": val("UCC_ID"),
    "engine_source_version": "v3369 script dump (github.com/deaod/ut2004)",
    "mcp_url": "http://localhost:6900/mcp",
    "plugin_root": val("PLUGIN_ROOT"),
    "tools": {
        "ut3converter": val("UT3CONV"),
        "utupscaler": val("UTUPSCALER"),
    },
}

path = os.environ["CONFIG"]
# Preserve anything the user set by hand that we did not detect this run.
if os.path.exists(path):
    try:
        with open(path) as f:
            old = json.load(f)
        for k in ("client_install_root", "install_root", "mcp_url"):
            if cfg.get(k) in (None, "") and old.get(k):
                cfg[k] = old[k]
        for k, v in (old.get("tools") or {}).items():
            if not cfg["tools"].get(k) and v:
                cfg["tools"][k] = v
    except Exception:
        pass

with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"  ok    wrote {path}")
PY
say

# ------------------------------------------------------------- copilot install
if [ "$INSTALL_COPILOT" -eq 1 ]; then
  say "Copilot CLI install"
  mkdir -p "$HOME/.copilot/skills" "$HOME/.copilot/agents"
  n=0
  for s in "$PLUGIN_ROOT"/skills/*/; do
    [ -d "$s" ] || continue
    name="$(basename "$s")"
    ln -sfn "${s%/}" "$HOME/.copilot/skills/$name" && n=$((n+1))
  done
  ok "linked $n skills into ~/.copilot/skills/"
  if [ -f "$PLUGIN_ROOT/.github/agents/sweeney.agent.md" ]; then
    ln -sfn "$PLUGIN_ROOT/.github/agents/sweeney.agent.md" "$HOME/.copilot/agents/sweeney.agent.md"
    ok "linked agent into ~/.copilot/agents/"
  fi
  say
  say "  Merge this into ~/.copilot/mcp-config.json for live in-game testing:"
  say
  sed 's/^/    /' "$PLUGIN_ROOT/agents/copilot-mcp-config.json"
  say
fi

say "Done."
[ "${ENGINE_OK:-0}" -eq 1 ] || { say; err "engine source is not usable -- Sweeney cannot answer engine questions"; exit 1; }
