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
# One machine often has several installs with different jobs: a Windows one to
# build with, a native one to play on, an old retail one kept around. They are
# told apart by what is in System/.
say "UT2004 installs"

is_install() { [ -f "$1/System/Engine.u" ]; }

describe() {                       # -> "<version>|<ucc kind>|<bits>|<date>|<commit>"
  local root="$1" label version ucc bits built commit
  label=$(sed -n 's/^Label=//p' "$root/System/Build.ini" 2>/dev/null | head -1)
  case "$label" in
    *_v[0-9][0-9][0-9][0-9]_*) version=$(printf '%s' "$label" | sed -n 's/.*_v\([0-9]\{4\}\)_.*/\1/p') ;;
    UT2004_Build_*)            version="retail" ;;
    *)                         version="unknown" ;;
  esac
  # e.g. UT2004_v3374_[2026-07-18_18.30]_d1b145e1
  built=$(printf '%s' "$label" | sed -n 's/.*\[\([0-9-]*\)_\([0-9.]*\)\].*/\1 \2/p')
  commit=$(printf '%s' "$label" | sed -n 's/.*\]_\(.*\)$/\1/p')
  [ -n "$built" ] || built="0000-00-00 00.00"
  if [ -f "$root/System/UCC.exe" ]; then
    case "$(file -b "$root/System/UCC.exe" 2>/dev/null)" in
      *x86-64*|*PE32+*) ucc="UCC.exe"; bits=64 ;;
      *PE32*)           ucc="UCC.exe"; bits=32 ;;
      *)                ucc="UCC.exe"; bits="?" ;;
    esac
  elif [ -f "$root/System/ucc-bin" ] || [ -f "$root/System/UCC" ]; then
    ucc="native"; bits=$(case "$(file -bL "$root/System/ucc-bin" 2>/dev/null)" in
                           *64-bit*) echo 64 ;; *32-bit*) echo 32 ;; *) echo "?" ;; esac)
  else
    ucc="none"; bits="-"
  fi
  printf '%s|%s|%s|%s|%s' "$version" "$ucc" "$bits" "$built" "$commit"
}

# Gather every candidate rather than taking the first hit.
FOUND=""
add_install() {
  case "$FOUND" in *"|$1|"*) return ;; esac
  is_install "$1" && FOUND="$FOUND|$1|"$'\n'
}
d="$PWD"
while [ "$d" != "/" ]; do add_install "$d"; d="$(dirname "$d")"; done
for c in "$HOME"/UT2004* "$HOME"/ut2004* /data/dev/UT2004* \
         "$HOME"/.steam/steam/steamapps/common/"Unreal Tournament 2004"; do
  [ -d "$c" ] && add_install "$c"
done

INSTALL_ROOT=""; CLIENT_ROOT=""; UCC_BITS=""; UCC_ID=""; UCC_KIND=""; BUILD_VERSION=""
BEST=-1; BESTKEY=""; BUILD_DATE=""; BUILD_COMMIT=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  root=$(printf '%s' "$line" | sed 's/^|//; s/|$//')
  IFS='|' read -r ver ucc bits built commit <<EOF2
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
    [ "$ucc" = "UCC.exe" ] && UCC_ID="$(stat -c '%s' "$root/System/UCC.exe" 2>/dev/null) bytes, mtime $(stat -c '%y' "$root/System/UCC.exe" 2>/dev/null | cut -d. -f1)"
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
UCC_ID="$UCC_ID" UCC_KIND="$UCC_KIND" BUILD_VERSION="$BUILD_VERSION" BUILD_DATE="$BUILD_DATE" BUILD_COMMIT="$BUILD_COMMIT" CLIENT_ROOT="$CLIENT_ROOT" \
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
