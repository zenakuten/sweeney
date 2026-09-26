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

INSTALL_COPILOT=0; INSTALL_VSCODE=0; INSTALL_UMODEL=0
for arg in "$@"; do
  case "$arg" in
    --install) ;;
    copilot|--install=copilot) INSTALL_COPILOT=1 ;;
    vscode|--install=vscode)   INSTALL_VSCODE=1 ;;
    --install-umodel)          INSTALL_UMODEL=1 ;;
    -h|--help)
      sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
      echo
      echo "Usage: setup.sh [--install copilot|vscode] [--install-umodel]"
      exit 0 ;;
  esac
done

PY=""
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "need Python 3.8+ on PATH (tried python3, python, py)" >&2; exit 1; }

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
# One machine often has several installs with different jobs: a Windows one to
# build with, a native one to play on, an old retail one kept around. They are
# told apart by what is in System/.
say "UT2004 installs"

is_install() { [ -f "$1/System/Engine.u" ]; }

describe() {   # -> "<version>|<ucc>|<bits>|<built>|<commit>|<size>|<mtime>"
  "$PY" "$PLUGIN_ROOT/scripts/inspect_install.py" "$1" 2>/dev/null
}

# Gather every candidate rather than taking the first hit.
FOUND=""
add_install() {
  case "$FOUND" in *"|$1|"*) return ;; esac
  is_install "$1" && FOUND="$FOUND|$1|"$'\n'
}
d="$PWD"
while [ "$d" != "/" ]; do add_install "$d"; d="$(dirname "$d")"; done
for c in "$HOME"/UT2004* "$HOME"/ut2004* \
         "$HOME"/.steam/steam/steamapps/common/"Unreal Tournament 2004" \
         "$HOME"/.local/share/Steam/steamapps/common/"Unreal Tournament 2004"; do
  [ -d "$c" ] && add_install "$c"
done

INSTALL_ROOT=""; CLIENT_ROOT=""; UCC_BITS=""; UCC_ID=""; UCC_KIND=""; BUILD_VERSION=""
BEST=-1; BESTKEY=""; BUILD_DATE=""; BUILD_COMMIT=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  root=$(printf '%s' "$line" | sed 's/^|//; s/|$//')
  IFS='|' read -r ver ucc bits built commit uccsize uccmtime <<EOF2
$(describe "$root")
EOF2
  printf '  found  %s\n         v%s, %s%s, built %s%s\n' "$root" "$ver" "$ucc" \
    "$([ "$bits" != "-" ] && printf ' (%s-bit)' "$bits")" "$built" \
    "$([ -n "$commit" ] && printf ' %s' "$commit")"

  # Build install: a 64-bit UCC.exe is the best thing to compile with.
  score=0
  [ "$ucc" = "UCC.exe" ] && [ "$bits" = 64 ] && score=3
  [ "$ucc" = "UCC.exe" ] && [ "$bits" = 32 ] && score=2
  [ "$ucc" = "native" ] && score=1
  # capability, then build date -- "3374" alone does not say which patch it is
  key="$score $built"
  if [ "$score" -gt "$BEST" ] || { [ "$score" -eq "$BEST" ] && [ "$key" \> "$BESTKEY" ]; }; then
    BEST=$score; BESTKEY="$key"
    INSTALL_ROOT="$root"; UCC_BITS="$bits"; UCC_KIND="$ucc"; BUILD_VERSION="$ver"
    BUILD_DATE="$built"; BUILD_COMMIT="$commit"
    [ "$ucc" = "UCC.exe" ] && UCC_ID="$uccsize bytes, mtime $uccmtime"
  fi
  # A play install is one with no UCC.exe -- keep the first as the client.
  [ "$ucc" != "UCC.exe" ] && [ -z "$CLIENT_ROOT" ] && CLIENT_ROOT="$root"
done <<EOF2
$FOUND
EOF2

say
if [ -n "$INSTALL_ROOT" ]; then
  ok "build install: $INSTALL_ROOT"
  ok "  v$BUILD_VERSION, $UCC_KIND, built $BUILD_DATE $BUILD_COMMIT"
  case "$UCC_KIND:$UCC_BITS" in
    UCC.exe:64) : ;;
    UCC.exe:32) warn "32-bit UCC.exe hangs building large packages -- a 3374 install has a 64-bit one" ;;
    native:*)   warn "no UCC.exe here. On Linux, building with the Windows UCC.exe under Wine is"
                warn "preferred over the native binary -- that needs a WINDOWS UT2004 install (3374)" ;;
    none:*)     warn "no compiler in this install at all" ;;
  esac
  [ "$UCC_BITS" = 64 ] && [ "$UCC_KIND" = "UCC.exe" ] && ok "UCC.exe: $UCC_ID"
else
  warn "no UT2004 install found"
  warn "run this from inside one, or set install_root in $CONFIG by hand"
fi
[ -n "$CLIENT_ROOT" ] && [ "$CLIENT_ROOT" != "$INSTALL_ROOT" ] && ok "play install: $CLIENT_ROOT"
say

# --------------------------------------------------------------- python toolchains
say "Python toolchains"
UT3CONV=""
if [ -n "$INSTALL_ROOT" ]; then
  [ -f "$INSTALL_ROOT/ut3converter/ut3conv.py" ] && UT3CONV="$INSTALL_ROOT/ut3converter"
fi
[ -n "$UT3CONV" ] && ok "ut3converter at $UT3CONV" || warn "ut3converter not found (optional)"

# The texture-rebuild toolchain ships with Sweeney rather than being found.
UTTEXTURE=""
[ -f "$PLUGIN_ROOT/tools/uttexture/uttexture.py" ] && UTTEXTURE="$PLUGIN_ROOT/tools/uttexture"
[ -n "$UTTEXTURE" ] && ok "texture-rebuild toolchain at $UTTEXTURE" \
                     || warn "texture-rebuild toolchain missing from the plugin"

# umodel exports meshes for the rebuild. On PATH is enough; note it if not.
UMODEL=""
for c in "$SWEENEY_HOME/umodel/umodel_64.exe" "$SWEENEY_HOME/umodel/umodel.exe" \
         "$SWEENEY_HOME/umodel/umodel" "$HOME/umodel/umodel_64.exe" \
         "$HOME/umodel/umodel" "$HOME/.local/bin/umodel"; do
  [ -f "$c" ] && { UMODEL="$c"; break; }
done
[ -z "$UMODEL" ] && command -v umodel >/dev/null 2>&1 && UMODEL="$(command -v umodel)"
# Not shipped with Sweeney and not redistributable, so it is fetched on request.
if [ -z "$UMODEL" ] && [ "$INSTALL_UMODEL" -eq 1 ]; then
  say "  fetching umodel (gildor.org)"
  if SWEENEY_HOME="$SWEENEY_HOME" bash "$PLUGIN_ROOT/scripts/install-umodel.sh"; then
    [ -f "$SWEENEY_HOME/umodel/.path" ] && UMODEL="$(cat "$SWEENEY_HOME/umodel/.path")"
  fi
fi
if [ -n "$UMODEL" ]; then
  ok "umodel at $UMODEL"
else
  warn "umodel not found -- the texture-rebuild toolchain cannot export without it."
  warn "  get it with: bash scripts/setup.sh --install-umodel"
fi

# Per-map rebuild data runs to gigabytes, so it lives in the install, never in
# the plugin checkout.
TEXWORK=""
[ -n "$INSTALL_ROOT" ] && TEXWORK="$INSTALL_ROOT/texture-rebuild"
say

# ------------------------------------------------------------------------ config
mkdir -p "$SWEENEY_HOME"
ENGINE_DIR="$ENGINE_DIR" INSTALL_ROOT="$INSTALL_ROOT" UCC_BITS="$UCC_BITS" \
UCC_ID="$UCC_ID" UCC_KIND="$UCC_KIND" BUILD_VERSION="$BUILD_VERSION" BUILD_DATE="$BUILD_DATE" BUILD_COMMIT="$BUILD_COMMIT" CLIENT_ROOT="$CLIENT_ROOT" \
UT3CONV="$UT3CONV" UTTEXTURE="$UTTEXTURE" UMODEL="$UMODEL" TEXWORK="$TEXWORK" PLUGIN_ROOT="$PLUGIN_ROOT" \
CONFIG="$CONFIG" "$PY" - <<'PY'
import json, os, datetime

def val(k):
    v = os.environ.get(k, "")
    return v if v else None

cfg = {
    "_comment": "Written by sweeney scripts/setup.sh. Safe to edit by hand.",
    "generated": datetime.datetime.now().isoformat(timespec="seconds"),
    "engine_source": val("ENGINE_DIR"),
    "install_root": val("INSTALL_ROOT"),
    "client_install_root": val("CLIENT_ROOT"),
    "ucc_bits": int(os.environ["UCC_BITS"]) if os.environ.get("UCC_BITS") else None,
    "ucc_id": val("UCC_ID"),
    "ucc_kind": val("UCC_KIND"),
    "build_version": val("BUILD_VERSION"),
    "build_date": val("BUILD_DATE"),
    "build_commit": val("BUILD_COMMIT"),
    "engine_source_version": "v3369 script dump (github.com/deaod/ut2004)",
    "mcp_url": "http://localhost:6900/mcp",
    "plugin_root": val("PLUGIN_ROOT"),
    "tools": {
        "ut3converter": val("UT3CONV"),
        "uttexture": val("UTTEXTURE"),
        "umodel": val("UMODEL"),
    },
    "paths": {
        "texture_work": val("TEXWORK"),
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

# ------------------------------------------------- copilot / vscode install
# VS Code reads personal skills and agents from ~/.copilot/ too, so both front
# ends share one install there. Only their MCP config differs.
if [ "$INSTALL_COPILOT" -eq 1 ] || [ "$INSTALL_VSCODE" -eq 1 ]; then
  say "Copilot CLI / VS Code install"
  mkdir -p "$HOME/.copilot/skills" "$HOME/.copilot/agents"
  n=0
  for s in "$PLUGIN_ROOT"/skills/*/; do
    [ -d "$s" ] || continue
    name="$(basename "$s")"
    rm -rf "$HOME/.copilot/skills/$name"
    if ln -s "${s%/}" "$HOME/.copilot/skills/$name" 2>/dev/null; then
      n=$((n+1))
    elif cp -r "${s%/}" "$HOME/.copilot/skills/$name" 2>/dev/null; then
      n=$((n+1)); COPIED=1
    fi
  done
  ok "installed $n skills into ~/.copilot/skills/"
  [ "${COPIED:-0}" = 1 ] && warn "copied rather than linked (no symlink support) -- re-run after updating Sweeney"
  if [ -f "$PLUGIN_ROOT/.github/agents/sweeney.agent.md" ]; then
    rm -f "$HOME/.copilot/agents/sweeney.agent.md"
    ln -s "$PLUGIN_ROOT/.github/agents/sweeney.agent.md" "$HOME/.copilot/agents/sweeney.agent.md" 2>/dev/null \
      || { cp "$PLUGIN_ROOT/.github/agents/sweeney.agent.md" "$HOME/.copilot/agents/sweeney.agent.md"; COPIED=1; }
    ok "installed agent into ~/.copilot/agents/"
  fi
  say
fi
if [ "$INSTALL_COPILOT" -eq 1 ]; then
  say "  Merge this into ~/.copilot/mcp-config.json for live in-game testing:"
  say
  sed 's/^/    /' "$PLUGIN_ROOT/agents/copilot-mcp-config.json"
  say
fi
if [ "$INSTALL_VSCODE" -eq 1 ]; then
  case "$(uname -s)" in
    Darwin)               base="$HOME/Library/Application Support" ;;
    MINGW*|MSYS*|CYGWIN*) base="${APPDATA:-$HOME/AppData/Roaming}" ;;
    *)                    base="${XDG_CONFIG_HOME:-$HOME/.config}" ;;
  esac
  # Distro builds keep their config under a different name ("Code - OSS" on Arch).
  VSCODE_USER="$base/Code/User"
  for c in "Code" "Code - OSS" "Code - Insiders" "VSCodium"; do
    [ -d "$base/$c/User" ] && { VSCODE_USER="$base/$c/User"; break; }
  done
  say "  Merge this into $VSCODE_USER/mcp.json for live in-game testing"
  say "  (or run \"MCP: Open User Configuration\" in VS Code):"
  say
  sed 's/^/    /' "$PLUGIN_ROOT/agents/vscode-mcp-config.json"
  say
  say "  Then pick \"sweeney\" from the agent dropdown in the Chat view."
  say
fi

say "Done."
[ "${ENGINE_OK:-0}" -eq 1 ] || { say; err "engine source is not usable -- Sweeney cannot answer engine questions"; exit 1; }
