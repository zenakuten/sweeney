#!/usr/bin/env python3
"""Pre-compile checks for UnrealScript sources.

UCC fails badly: instead of reporting an error it often hangs on "Analyzing...",
and several mistakes compile cleanly and then do nothing at runtime. This catches
the ones that cost the most time.

    uccheck.py MyMod/Classes/*.uc
    uccheck.py MyMod/                 # walks for .uc

Checks
  encoding    UTF-8 multi-byte sequences and BOMs. .uc must be Latin-1
              (ISO-8859-1); UCC is not Unicode-aware and these can hang it.
  comment-string
              A comment that leaves a string literal open. UCC tracks strings
              inside comments, so an unbalanced quote opens one that never
              closes, and analysis runs off the end of the file.
  ternary     `a ? b : c` is not supported by UCC and hangs analysis.
  enum-default
              `Prop=2` in defaultproperties where Prop is an enum. Silently
              discarded -- the property keeps its inherited default. Use the
              enum name. The property's type is resolved through the class's
              own ancestry, using the engine source when it is available, so
              a name that is an enum on one class and an int on another does
              not produce a false report.

Errors are things that will break or silently misbehave. Warnings are worth a
look but appear throughout code that compiles. Exit status is 1 if there were
errors, or any finding under --strict, so it can gate a build.

Calibration: 0 errors across the 2432-file engine source and 472 mod files that
all compile.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- engine source


def engine_source() -> Path | None:
    """Path to the UT2004 script checkout, from ~/.sweeney/config.json."""
    env = os.environ.get("SWEENEY_ENGINE")
    if env and Path(env).is_dir():
        return Path(env)
    cfg = Path.home() / ".sweeney" / "config.json"
    try:
        with cfg.open() as f:
            src = json.load(f).get("engine_source")
        if src and Path(src).is_dir():
            return Path(src)
    except Exception:
        pass
    return None


# ------------------------------------------------------------------- tokenizing

# Spans of a .uc file, so checks can ask "is this inside a comment or a string?".
CODE, LINE_COMMENT, BLOCK_COMMENT, STRING, NAME_LITERAL = range(5)


def classify(text: str) -> list[int]:
    """Return a per-character region id for `text`.

    Deliberately simple, and deliberately *not* UCC's tokenizer: it reads a
    comment as a comment even where UCC would get lost, which is the point --
    the whole bug being detected is that UCC does not.
    """
    n = len(text)
    out = [CODE] * n
    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = LINE_COMMENT
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = BLOCK_COMMENT
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                if text[j : j + 1] == "\n":
                    break
                j += 1
            j = min(j + 1, n)
            for k in range(i, j):
                out[k] = STRING
            i = j
        elif c == "'":
            # Object/name literal: Texture'Pkg.Group.Name'
            j = text.find("'", i + 1)
            j = n if j < 0 else j + 1
            if "\n" in text[i:j]:
                out[i] = CODE
                i += 1
                continue
            for k in range(i, j):
                out[k] = NAME_LITERAL
            i = j
        else:
            i += 1
    return out


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


ERROR, WARN = "error", "warn"


class Finding:
    def __init__(self, path, line, check, message, detail="", level=ERROR):
        self.path, self.line, self.check = path, line, check
        self.message, self.detail, self.level = message, detail, level


# ---------------------------------------------------------------------- checks


def check_encoding(path: Path, raw: bytes) -> list[Finding]:
    out = []
    if raw.startswith(b"\xef\xbb\xbf"):
        out.append(Finding(path, 1, "encoding", "UTF-8 BOM", "UCC is not Unicode-aware; save as Latin-1 with no BOM"))
        return out
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        out.append(Finding(path, 1, "encoding", "UTF-16 BOM", "save as Latin-1 (ASCII is a safe subset)"))
        return out
    try:
        raw.decode("ascii")
        return out
    except UnicodeDecodeError:
        pass
    # Non-ASCII, no BOM. Decodable as UTF-8 means genuine multi-byte UTF-8,
    # the riskier case; bare Latin-1 high bytes are legal. One finding per line.
    try:
        raw.decode("utf-8")
        is_utf8 = True
    except UnicodeDecodeError:
        is_utf8 = False

    text = raw.decode("utf-8") if is_utf8 else raw.decode("latin-1")
    pattern = r"[^\x00-\x7f]+"
    seen = set()
    for m in re.finditer(pattern, text):
        ln = line_of(text, m.start())
        if ln in seen:
            continue
        seen.add(ln)
        if is_utf8:
            out.append(
                Finding(path, ln, "encoding",
                        f"multi-byte UTF-8 ({m.group()[:12]!r})",
                        "UCC is not Unicode-aware; .uc should be Latin-1. Such files "
                        "often do compile, but this is a known cause of UCC hanging "
                        "on 'Analyzing...'. Re-save as Latin-1",
                        level=WARN)
            )
        else:
            out.append(
                Finding(path, ln, "encoding",
                        f"Latin-1 high byte 0x{ord(m.group()[0]):02x}",
                        "legal -- .uc is Latin-1 -- but ASCII is safer if it was "
                        "not intended",
                        level=WARN)
            )
    return out[:8]


def comment_spans(text: str, regions: list[int]):
    """Yield (start, end) for each comment region."""
    i, n = 0, len(text)
    while i < n:
        if regions[i] in (LINE_COMMENT, BLOCK_COMMENT):
            kind = regions[i]
            j = i
            while j < n and regions[j] == kind:
                j += 1
            yield i, j
            i = j
        else:
            i += 1


def check_comment_string(path: Path, text: str, regions: list[int]) -> list[Finding]:
    """A comment that leaves a string literal open.

    UCC's tokenizer tracks string literals *inside* comments, which it should
    not. A `"` in a comment therefore opens a string; if nothing closes it,
    the parser runs past the comment and off the end of the file, and UCC hangs
    on "Analyzing..." instead of reporting an error.

    Two conditions, both needed. Each was checked against the whole engine
    source and ~470 mod files, all of which compile:

        // "                                  ditto marks, no backslash -- fine
        // the "fire held through a           prose split over two comment
        // weapon switch" glitch               lines, no backslash -- fine
        // ex: PlayStream("D:\\\\a.mp3",0)         backslash, but the string opens
                                              and closes -- fine
        // \\r\\n is stripped from the line       backslash, no quote -- fine
        // #exec ... FILE="Textures\\foo.utx"  opens and closes -- fine
        // ends with \\" and more              backslash, and the " opens a
                                              string that never closes -- HANGS

    So an unbalanced quote alone is tolerated; it is an unbalanced quote
    reached through UCC's escape handling that runs the parser off the end.
    """
    out = []
    for start, end in comment_spans(text, regions):
        body = text[start:end]
        if '"' not in body or "\\" not in body:
            continue
        in_string = False
        opened_at = 0
        i = 0
        while i < len(body):
            c = body[i]
            if in_string and c == "\\":
                i += 2
                continue
            if c == '"':
                if in_string:
                    in_string = False
                else:
                    in_string, opened_at = True, i
            i += 1
        if in_string:
            out.append(
                Finding(path, line_of(text, start + opened_at), "comment-string",
                        'unclosed \'"\' in a comment',
                        "UCC tracks string literals inside comments, so this opens a "
                        "string that never closes; the parser runs off the end of the "
                        "file and hangs on 'Analyzing...'. Balance the quotes, or drop "
                        "the backslash if it was meant to escape one")
            )
    return out


def check_ternary(path: Path, text: str, regions: list[int]) -> list[Finding]:
    # `?` has no valid use in UnrealScript outside strings and name literals.
    out = []
    for i, ch in enumerate(text):
        if ch == "?" and regions[i] == CODE:
            out.append(
                Finding(path, line_of(text, i), "ternary",
                        "'?' in code -- the ternary operator",
                        "UCC does not support 'a ? b : c' and hangs on it. Use if/else "
                        "into a local")
            )
    return out


ENUM_DECL = re.compile(r"\benum\s+(\w+)", re.IGNORECASE)
VAR_DECL = re.compile(
    r"^\s*var\b(?:\s*\([^)]*\))?\s*((?:\w+\s+)*?)(\w+)\s+([\w,\s]+);",
    re.IGNORECASE | re.MULTILINE,
)
VAR_MODIFIERS = {
    "config", "globalconfig", "localized", "const", "private", "protected",
    "native", "transient", "editconst", "export", "noexport", "travel",
    "input", "cache", "automated", "editinline", "editinlineuse", "deprecated",
    "edfindable", "noimport", "duplicatetransient", "repnotify", "databinding",
    "nontransactional", "init", "editoronly", "notforconsole", "serializetext",
    "array", "class",
}


CLASS_DECL = re.compile(r"^\s*class\s+(\w+)\s+extends\s+([\w.]+)", re.IGNORECASE | re.MULTILINE)


class ClassIndex:
    """Class hierarchy and per-class property types.

    Property names collide across unrelated classes -- `RenderStyle` is an
    `ERenderStyle` on Actor but a plain `byte` on DrawOpBase, and `Height` is
    an int on SheetBuilder while another class has an enum of that name. So a
    property is resolved by walking the class's own ancestry, never by name
    alone.
    """

    def __init__(self):
        self.enums: set[str] = set()
        self.parent: dict[str, str] = {}
        self.props: dict[str, dict[str, str]] = {}

    def add_file(self, path: Path) -> str | None:
        try:
            text = path.read_bytes().decode("latin-1")
        except Exception:
            return None
        for m in ENUM_DECL.finditer(text):
            self.enums.add(m.group(1).lower())

        cm = CLASS_DECL.search(text)
        if not cm:
            return None
        cls = cm.group(1).lower()
        self.parent[cls] = cm.group(2).split(".")[-1].lower()
        table = self.props.setdefault(cls, {})
        for m in VAR_DECL.finditer(text):
            typ, names = m.group(2), m.group(3)
            if typ.lower() in VAR_MODIFIERS:
                continue
            for nm in names.split(","):
                nm = nm.strip().split("[")[0].strip()
                if nm:
                    table.setdefault(nm.lower(), typ)
        return cls

    def resolve(self, cls: str | None, prop: str) -> str | None:
        """The declared type of `prop` on `cls` or an ancestor, if known."""
        seen = set()
        while cls and cls not in seen:
            seen.add(cls)
            typ = self.props.get(cls, {}).get(prop.lower())
            if typ:
                return typ
            cls = self.parent.get(cls)
        return None

    def is_enum(self, typ: str | None) -> bool:
        return bool(typ) and typ.lower() in self.enums


DEFAULTS_BLOCK = re.compile(r"^\s*defaultproperties\s*$", re.IGNORECASE | re.MULTILINE)
ASSIGN_INT = re.compile(r"^\s*(\w+)\s*=\s*(-?\d+)\s*$")


def check_enum_defaults(path: Path, text: str, index: ClassIndex) -> list[Finding]:
    m = DEFAULTS_BLOCK.search(text)
    if not m:
        return []
    cm = CLASS_DECL.search(text)
    cls = cm.group(1).lower() if cm else None
    out = []
    start_line = line_of(text, m.start())
    for offset, raw_line in enumerate(text[m.end():].splitlines(), start=1):
        line = raw_line.split("//")[0]
        am = ASSIGN_INT.match(line)
        if not am:
            continue
        prop, value = am.group(1), am.group(2)
        typ = index.resolve(cls, prop)
        if index.is_enum(typ):
            out.append(
                Finding(path, start_line + offset, "enum-default",
                        f"{prop}={value} -- {prop} is the enum {typ}",
                        "UCC silently discards an int given to an enum property; it "
                        "keeps its inherited default. Write the enum name instead")
            )
    return out


# ------------------------------------------------------------------------ main


def gather(args) -> list[Path]:
    paths = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.uc")))
        elif p.suffix.lower() == ".uc":
            paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help=".uc files or directories")
    ap.add_argument("--quiet", "-q", action="store_true", help="only print findings")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero for warnings too, not just errors")
    ap.add_argument("--no-engine", action="store_true",
                    help="skip learning enum properties from the engine source")
    args = ap.parse_args()

    files = gather(args.paths)
    if not files:
        print("no .uc files found", file=sys.stderr)
        return 2

    index = ClassIndex()
    if not args.no_engine:
        src = engine_source()
        if src:
            for p in sorted(src.rglob("*.uc")):
                index.add_file(p)
        elif not args.quiet:
            print("note: engine source not found (run setup.sh) -- enum-default check "
                  "can only resolve properties declared in the files given\n",
                  file=sys.stderr)
    # The files under test declare their own classes, enums and properties, and
    # sit below the engine classes in the hierarchy.
    for p in files:
        index.add_file(p)

    findings: list[Finding] = []
    for path in files:
        try:
            raw = path.read_bytes()
        except OSError as e:
            print(f"{path}: cannot read: {e}", file=sys.stderr)
            continue
        enc = check_encoding(path, raw)
        findings.extend(enc)
        text = raw.decode("latin-1")
        regions = classify(text)
        findings.extend(check_comment_string(path, text, regions))
        findings.extend(check_ternary(path, text, regions))
        findings.extend(check_enum_defaults(path, text, index))

    for f in sorted(findings, key=lambda f: (f.level != ERROR, str(f.path), f.line)):
        print(f"{f.path}:{f.line}: {f.level}: {f.check}: {f.message}")
        if f.detail:
            print(f"    {f.detail}")

    errors = sum(1 for f in findings if f.level == ERROR)
    warns = len(findings) - errors
    if not args.quiet:
        n = len(files)
        if findings:
            print(f"\n{errors} error(s), {warns} warning(s) in {n} file(s)")
        else:
            print(f"{n} file(s) checked, nothing found")
    return 1 if (errors or (args.strict and warns)) else 0


if __name__ == "__main__":
    sys.exit(main())
