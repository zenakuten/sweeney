#!/usr/bin/env python3
"""Copy an UnrealScript package's source tree under a new package name.

A mod released more than once needs a new package name each release: clients cache
packages by name and GUID, so two different builds sharing a name conflict online. The
package name is the source folder's name, but the source also spells it out wherever it
refers to its own objects by path, and those must follow:

    class'MyMod.MyPawn'                      object literals
    Texture'MyMod.Textures.Icon'
    "MyMod.MyPickup"                         class paths in strings, DynamicLoadObject,
    ScoreBoardType=MyMod.MyScoreboard        game options, defaultproperties
    #exec OBJ LOAD FILE=... PACKAGE=MyMod    imported content

What must NOT follow is everything that uses the name as plain text: config(MyMod)
names the .ini file (rename it and every player's settings reset), and display
strings, URLs and key-bind labels are just words.

The rule that separates them: rewrite the name only where it is a package qualifier --
the whole word followed by a dot and an object name (or the end of a string, for code
that builds paths as "MyMod." $ Name) -- or the value of PACKAGE=. Every other use is
left alone, so there is no list of files or lines to maintain.

    uscript_rename.py MyMod MyMod MyMod_V30
    uscript_rename.py MyAddon MyAddon MyAddon_V30 --also MyMod=MyMod_V30
    uscript_rename.py MyMod MyMod MyMod_V30 --dry-run

Rewritten: .uc and .uci (comments are left as they are), and .ini/.int, where
EditPackages=/ServerPackages= values are renamed too. Every other file is copied
unchanged. The copy goes beside the source, named after the new package, and is
refused if that folder exists.

Afterwards it lists every use of each old name it left alone, so the result can be
read over rather than trusted.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uccheck import CODE, STRING, NAME_LITERAL, classify, line_of  # noqa: E402

SCRIPT_EXT = {".uc", ".uci"}
TEXT_EXT = {".ini", ".int"}

# A dot after the name that starts a file extension is a file name, not a path:
# FILE=Textures\MyMod.utx, or a message mentioning MyMod.ini.
FILE_EXT = ("u|ucl|int|ini|log|utx|uax|usx|ukx|upx|uz2|umod|ut2|dds|tga|bmp|pcx|png"
            "|wav|ogg|mp3|txt|htm|html|md")


def qualifier(old: str) -> re.Pattern:
    return re.compile(
        rf"(?<![\w.]){re.escape(old)}(?=\.(?:(?!(?:{FILE_EXT})\b)[A-Za-z_]|\"))",
        re.IGNORECASE)


def package_value(old: str) -> re.Pattern:
    return re.compile(rf"(\bPACKAGE\s*=\s*\"?){re.escape(old)}\b", re.IGNORECASE)


def packages_line(old: str) -> re.Pattern:
    return re.compile(rf"(?m)^(\s*\w*Packages\s*=\s*\"?){re.escape(old)}(?=\"?\s*$)",
                      re.IGNORECASE)


def bare(old: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.]){re.escape(old)}(?!\w)", re.IGNORECASE)


class Change:
    def __init__(self, path, line, before, after):
        self.path, self.line, self.before, self.after = path, line, before, after


def code_spans(regions: list[int]):
    """Contiguous spans that are code, strings or name/object literals (not comments)."""
    keep = (CODE, STRING, NAME_LITERAL)
    spans, i, n = [], 0, len(regions)
    while i < n:
        if regions[i] in keep:
            j = i
            while j < n and regions[j] in keep:
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def rewrite_spans(text: str, spans, renames):
    """Apply the renames inside [start, end) spans of text. Returns the new text."""
    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        seg = text[start:end]
        for old, new in renames:
            seg = qualifier(old).sub(new, seg)
            seg = package_value(old).sub(lambda m, new=new: m.group(1) + new, seg)
        out.append(seg)
        last = end
    out.append(text[last:])
    return "".join(out)


def rewrite(path: Path, text: str, renames):
    if path.suffix.lower() in SCRIPT_EXT:
        spans = code_spans(classify(text))
    else:
        spans = [(0, len(text))]
    new = rewrite_spans(text, spans, renames)
    if path.suffix.lower() in TEXT_EXT:
        for old, name in renames:
            new = packages_line(old).sub(lambda m, name=name: m.group(1) + name, new)
    return new


def line_changes(rel: str, before: str, after: str) -> list[Change]:
    a, b = before.split("\n"), after.split("\n")
    # Renaming never adds or removes a line, so lines pair up one to one.
    return [Change(rel, i + 1, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]


def left_alone(rel: str, text: str, path: Path, renames):
    """Every remaining use of an old name, for the report."""
    if path.suffix.lower() in SCRIPT_EXT:
        regions = classify(text)
        in_code = lambda pos: regions[pos] in (CODE, STRING, NAME_LITERAL)
    else:
        in_code = lambda pos: True
    found = []
    for old, _ in renames:
        for m in bare(old).finditer(text):
            if not in_code(m.start()):
                continue
            ln = line_of(text, m.start())
            line = text.split("\n")[ln - 1].strip()
            found.append((rel, ln, line))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Copy an UnrealScript source tree under a new package name.")
    ap.add_argument("src", type=Path, help="the package's source folder")
    ap.add_argument("old", help="current package name")
    ap.add_argument("new", help="new package name")
    ap.add_argument("--also", action="append", default=[], metavar="OLD=NEW",
                    help="also rename references to another package (a dependency "
                         "renamed in the same release); repeatable")
    ap.add_argument("--dest", type=Path,
                    help="output folder (default: beside src, named NEW)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change; write nothing")
    ap.add_argument("--quiet", action="store_true", help="summary only")
    args = ap.parse_args()

    src = args.src.resolve()
    if not src.is_dir():
        sys.exit(f"not a folder: {src}")
    renames = [(args.old, args.new)]
    for pair in args.also:
        if "=" not in pair:
            sys.exit(f"--also wants OLD=NEW, got {pair!r}")
        o, n = pair.split("=", 1)
        renames.append((o.strip(), n.strip()))
    olds = [o.lower() for o, _ in renames]
    if len(set(olds)) != len(olds):
        sys.exit("an old name is given twice")

    dest = (args.dest or src.parent / args.new).resolve()
    if not args.dry_run:
        if dest.exists():
            sys.exit(f"destination exists: {dest}")
        shutil.copytree(src, dest)
    root = src if args.dry_run else dest

    changes: list[Change] = []
    kept = []
    files_changed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        if path.suffix.lower() not in SCRIPT_EXT | TEXT_EXT:
            continue
        rel = str(path.relative_to(root))
        raw = path.read_bytes()
        text = raw.decode("latin-1")        # .uc is Latin-1; this round-trips any byte
        new = rewrite(path, text, renames)
        if new != text:
            files_changed += 1
            changes += line_changes(rel, text, new)
            if not args.dry_run:
                path.write_bytes(new.encode("latin-1"))
        kept += left_alone(rel, new, path, renames)

    verb = "would rewrite" if args.dry_run else "rewrote"
    if not args.quiet:
        for c in changes:
            print(f"{c.path}:{c.line}")
            print(f"  - {c.before.strip()[:200]}")
            print(f"  + {c.after.strip()[:200]}")
        print()
    print(f"{verb} {len(changes)} lines in {files_changed} files"
          + ("" if args.dry_run else f" -> {dest}"))
    if kept:
        print(f"\nleft alone ({len(kept)}) -- not package paths; check none should move:")
        for rel, ln, line in kept:
            print(f"  {rel}:{ln}: {line[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
