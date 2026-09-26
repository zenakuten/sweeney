#!/usr/bin/env bash
# Fetch umodel (Gildor's UE Viewer) into ~/.sweeney/umodel.
#
# umodel exports the textures and meshes the texture-rebuild toolchain works
# from, so nothing can be rebuilt without it. It is not redistributable here,
# so it is downloaded from the author's site on request.
#
#   source  https://github.com/gildor2/UEViewer
#   builds  https://www.gildor.org/en/projects/umodel
#
# Two things the download needs. The release directory is versioned (.../47/...)
# and changes with each build, so the link is read off the page rather than
# hardcoded; and the site rejects requests without a Referer, returning an HTML
# 404 that looks like a successful download until you try to open it.
set -u

PAGE="https://www.gildor.org/en/projects/umodel"
DEST="${SWEENEY_HOME:-$HOME/.sweeney}/umodel"
UA="Mozilla/5.0"

say()  { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || fail "curl is needed to download umodel"

# Which build? The native Linux one is a 32-bit binary against libpng12 and will
# not start on a current distro, so everything except Windows takes the Windows
# build and runs it through wine -- which is what the toolchain expects anyway.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) WANT="umodel_win32.zip"; NATIVE=1 ;;
  *)                    WANT="umodel_win32.zip"; NATIVE=0 ;;
esac

say "reading $PAGE"
HREF="$(curl -sSL --max-time 30 -A "$UA" "$PAGE" \
        | grep -oE "href=\"[^\"]*${WANT}\"" | head -1 \
        | sed -E 's/^href="//; s/"$//')"
[ -n "$HREF" ] || fail "could not find $WANT on $PAGE (has the page changed?)"
case "$HREF" in
  http*) URL="$HREF" ;;
  /*)    URL="https://www.gildor.org$HREF" ;;
  *)     URL="https://www.gildor.org/$HREF" ;;
esac
say "downloading $URL"

mkdir -p "$DEST" || fail "cannot create $DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -sSL --max-time 300 -e "$PAGE" -A "$UA" -o "$TMP/$WANT" "$URL" \
  || fail "download failed"

# A hotlink rejection arrives as HTML with a 200, so check before unpacking.
case "$(file -b "$TMP/$WANT" 2>/dev/null)" in
  *Zip*|*zip*) : ;;
  *) fail "downloaded file is not a zip (the site likely refused the request)" ;;
esac

command -v unzip >/dev/null 2>&1 || fail "unzip is needed to unpack umodel"
unzip -oq "$TMP/$WANT" -d "$DEST" || fail "unpack failed"

BIN=""
for c in "$DEST/umodel_64.exe" "$DEST/umodel.exe" "$DEST/umodel"; do
  [ -f "$c" ] && { BIN="$c"; break; }
done
[ -n "$BIN" ] || fail "no umodel binary in $DEST after unpacking"
chmod +x "$BIN" 2>/dev/null || true

say "installed $BIN"
if [ "$NATIVE" -eq 0 ]; then
  command -v wine >/dev/null 2>&1 \
    || say "note: this is the Windows build and wine was not found -- install wine to use it"
fi
printf '%s\n' "$BIN" > "$DEST/.path"
