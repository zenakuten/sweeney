#!/usr/bin/env bash
# Measure which constructs actually hang or break YOUR UCC.
#
# The traps Sweeney documents are folklore until measured, and UCC differs
# between builds: the 32-bit and 64-bit community patches are separate
# binaries, and the engine script source is a v3369 dump compiled by Epic's
# own compiler years earlier. Nothing about one build proves anything about
# another. This settles it for the install you actually use.
#
# It builds a throwaway package one variant at a time and records whether UCC
# succeeded, errored, or hung.
#
# It WRITES INTO THE INSTALL, which is why it needs --yes:
#   - creates   <install>/SweeneyProbe/
#   - appends   EditPackages=SweeneyProbe to System/UT2004.ini
# Both are undone at the end, and UT2004.ini is backed up first.
#
#   ./ucc-probe.sh --install ~/UT2004 --yes

set -uo pipefail

INSTALL=""; YES=0; TIMEOUT=90
while [ $# -gt 0 ]; do
  case "$1" in
    --install) INSTALL="${2:-}"; shift 2 ;;
    --yes) YES=1; shift ;;
    --timeout) TIMEOUT="${2:-90}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$INSTALL" ]; then
  INSTALL=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.sweeney/config.json'))).get('install_root') or '')" 2>/dev/null)
fi
[ -n "$INSTALL" ] && [ -f "$INSTALL/System/UCC.exe" ] || {
  echo "need a UT2004 install: --install <path> (no System/UCC.exe found)" >&2; exit 1; }

INI="$INSTALL/System/UT2004.ini"
PKG="SweeneyProbe"
PKGDIR="$INSTALL/$PKG"

if [ "$YES" -ne 1 ]; then
  cat <<EOF
This will write into the install at:
  $INSTALL

  create   $PKGDIR/
  append   EditPackages=$PKG   to System/UT2004.ini (backed up)

Both are reverted when it finishes. Re-run with --yes to proceed.
EOF
  exit 1
fi

[ -e "$PKGDIR" ] && { echo "$PKGDIR already exists; remove it first" >&2; exit 1; }

UCC_DESC=$(file -b "$INSTALL/System/UCC.exe" 2>/dev/null | cut -c1-40)
echo "install:  $INSTALL"
echo "UCC.exe:  $UCC_DESC"
echo "          $(stat -c '%s bytes, mtime %y' "$INSTALL/System/UCC.exe" | cut -d. -f1)"
echo

BACKUP="$INI.sweeney-probe-backup"
cp "$INI" "$BACKUP" || exit 1
cleanup() {
  [ -f "$BACKUP" ] && mv -f "$BACKUP" "$INI"
  rm -rf "$PKGDIR"
  rm -f "$INSTALL/System/$PKG.u"
}
trap cleanup EXIT INT TERM

mkdir -p "$PKGDIR/Classes"
# EditPackages lines live under [Editor.EditorEngine]; append after the last one.
python3 - "$INI" "$PKG" <<'PY'
import sys
ini, pkg = sys.argv[1], sys.argv[2]
with open(ini, 'r', encoding='latin-1', newline='') as f:
    lines = f.readlines()
last = max((i for i, l in enumerate(lines) if l.strip().lower().startswith('editpackages=')), default=None)
if last is None:
    sys.exit("no EditPackages= line found in UT2004.ini")
nl = '\r\n' if lines[last].endswith('\r\n') else '\n'
lines.insert(last + 1, f'EditPackages={pkg}{nl}')
with open(ini, 'w', encoding='latin-1', newline='') as f:
    f.writelines(lines)
PY
[ $? -eq 0 ] || exit 1

# ---------------------------------------------------------------- the variants
# Each is a whole Probe.uc. Written as a function so odd characters survive.
emit() {
  case "$1" in
    baseline) cat <<'V'
class Probe extends Info;
// an ordinary comment
defaultproperties
{
}
V
;;
    comment-escaped-quote) cat <<'V'
class Probe extends Info;
// this comment has an escaped quote \" and nothing closes it
var int Marker;
defaultproperties
{
}
V
;;
    comment-balanced-quotes) cat <<'V'
class Probe extends Info;
// this comment has a balanced "pair" of quotes
var int Marker;
defaultproperties
{
}
V
;;
    comment-odd-quote) cat <<'V'
class Probe extends Info;
// this comment has one " quote and no backslash
var int Marker;
defaultproperties
{
}
V
;;
    comment-windows-path) cat <<'V'
class Probe extends Info;
// #exec OBJ LOAD FILE="Textures\something.utx" PACKAGE=Probe
var int Marker;
defaultproperties
{
}
V
;;
    comment-backslash-n) cat <<'V'
class Probe extends Info;
// \r\n is stripped from the line
var int Marker;
defaultproperties
{
}
V
;;
    ternary) cat <<'V'
class Probe extends Info;
function int Pick(bool b)
{
    return b ? 1 : 0;
}
defaultproperties
{
}
V
;;
    utf8) printf 'class Probe extends Info;\n// caf\xc3\xa9 -- multi-byte UTF-8\nvar int Marker;\ndefaultproperties\n{\n}\n' ;;
    latin1) printf 'class Probe extends Info;\n// caf\xe9 -- Latin-1 high byte\nvar int Marker;\ndefaultproperties\n{\n}\n' ;;
    utf8-bom) printf '\xef\xbb\xbfclass Probe extends Info;\nvar int Marker;\ndefaultproperties\n{\n}\n' ;;
  esac
}

VARIANTS="baseline
comment-escaped-quote
comment-balanced-quotes
comment-odd-quote
comment-windows-path
comment-backslash-n
ternary
utf8
latin1
utf8-bom"

printf '%-40s %s\n' "VARIANT" "RESULT"
printf '%-40s %s\n' "----------------------------------------" "------"

RESULTS=""
for v in $VARIANTS; do
  emit "$v" > "$PKGDIR/Classes/Probe.uc"
  rm -f "$INSTALL/System/$PKG.u"
  out=$(cd "$INSTALL/System" && timeout "$TIMEOUT" ./UCC.exe make 2>&1)
  rc=$?
  if [ $rc -eq 124 ]; then
    r="HANG (timed out after ${TIMEOUT}s)"
  elif [ -f "$INSTALL/System/$PKG.u" ]; then
    r="ok"
  elif printf '%s' "$out" | grep -qiE '\berror\b'; then
    r="error: $(printf '%s' "$out" | grep -iE '\berror\b' | head -1 | cut -c1-60)"
  else
    r="failed (no .u, no error line)"
  fi
  printf '%-40s %s\n' "$v" "$r"
  RESULTS="$RESULTS$v=$r"$'\n'
done

echo
echo "Done. The install has been restored."
echo
echo "'baseline' must read ok -- if it does not, the harness is at fault and"
echo "nothing else in this table means anything."
