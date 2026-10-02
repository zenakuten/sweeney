"""The class importer: what UCC does with a .uc before the compiler sees it.

UCC reads a class file in two stages. First a line-based importer splits it into
script text, defaultproperties text and cpptext, and finds the class and parent
names. Only then does the compiler tokenize the script text. This module is the
first stage. Its quirks are UCC's, each pinned by a probe in tests/uparse:

- Lines end at CR, LF or CRLF (one of each, at most). Epic's source cuts a line at
  4095 characters, but the 3374 UCC does not (probe lex-line-over-4095).
- The script text is every line outside defaultproperties, rebuilt with CRLF
  endings; cpptext lines become `// (cpptext)` placeholders, but defaultproperties
  lines are dropped. So compiler line numbers after a defaultproperties block are
  off by the block's length.
- To strip `//` comments safely, each script line is scanned for its first string.
  That scan treats `\\"` as an escaped quote, and it never advances when a line's
  last quote is an escaped one with nothing after it. UCC then spins forever on
  "Analyzing...": the comment hang. It applies to code lines as much as comments,
  but not inside defaultproperties or cpptext, which are scanned differently.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

MAX_LINE = None     # Epic's 4095-character cut; measured absent on the 3374 UCC


def decode(src: bytes) -> str:
    """Bytes to text the way UCC loads a .uc: UTF-16 if it has a BOM, else Latin-1
    byte for byte. A UTF-8 BOM is dropped (the 3374 UCC accepts it; the retail
    compiler does not -- see the encoding-utf8-bom probe)."""
    if src[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return src.decode("utf-16")
    if src[:3] == b"\xef\xbb\xbf":
        src = src[3:]
    return src.decode("latin-1")


def split_lines(text: str) -> list[str]:
    """ParseLine in exact mode: CR, LF or CRLF ends a line; long lines are cut."""
    out, i, n = [], 0, len(text)
    while i < n:
        j = i
        while j < n and text[j] not in "\r\n" and (MAX_LINE is None or j - i < MAX_LINE):
            j += 1
        got = j > i
        line = text[i:j]
        if j < n and text[j] == "\r":
            j += 1
        if j < n and text[j] == "\n":
            j += 1
        # ParseLine reports a line if it read anything or more input follows, so an
        # empty line right at the end of the file is not a line.
        if got or j < n:
            out.append(line)
        i = j
    return out


def _parse_command(line: str, word: str) -> bool:
    """ParseCommand: leading blanks, the word (any case), then a non-alnum."""
    s = line.lstrip(" \t")
    if s[:len(word)].lower() != word:
        return False
    rest = s[len(word):]
    return not (rest[:1].isalnum())


def _string_end_hangs(line: str) -> bool:
    """Whether the importer's search for the end of the line's first string spins.

    The search starts at the first quote and steps to each next quote while the one
    it is on is escaped (a backslash before it, not itself escaped). When there is
    no next quote it stays put; if it is sitting on an escaped quote, it loops
    forever.
    """
    begin = line.find('"')
    if begin < 0:
        return False

    def escaped(i: int) -> bool:
        return i >= 1 and line[i - 1] == "\\" and not (i >= 2 and line[i - 2] == "\\")

    end = begin
    while True:
        nxt = line.find('"', end + 1)
        if nxt < 0:
            return escaped(end)
        end = nxt
        if not escaped(end):
            return False


@dataclasses.dataclass
class Imported:
    script: list[tuple[int, str]]       # (source line, text) of each script-text line
    defaults: list[tuple[int, str]]     # defaultproperties text lines, comments stubbed
    cpptext: list[tuple[int, str]]
    class_name: str
    base_name: str
    dependson: list[str]
    hang_line: int | None               # source line where the importer would spin

    def script_text(self) -> str:
        """The text the compiler tokenizes: script lines, each ending in CRLF."""
        return "".join(t + "\r\n" for _, t in self.script)


def import_class(src: bytes | str) -> Imported:
    text = decode(src) if isinstance(src, bytes) else src
    lines = split_lines(text)
    script, defaults, cpptext, deps = [], [], [], []
    class_name = base_name = ""
    comment_dim = 0
    i = 0

    def take():
        nonlocal i
        if i >= len(lines):
            return None
        i += 1
        return i, lines[i - 1]          # 1-based source line number

    while True:
        got = take()
        if got is None:
            break
        ln, line = got
        process = comment_dim <= 0

        if _parse_command(line, "cpptext") and process:
            script.append((ln, "// (cpptext)"))
            script.append((ln, "// (cpptext)"))
            take()                      # the line holding '{'
            while True:
                got = take()
                if got is None:
                    break
                cl, cline = got
                script.append((cl, "// (cpptext)"))
                if cline[:1] == "}":
                    break
                cpptext.append((cl, cline))
            continue

        if _parse_command(line, "defaultproperties") and process:
            while True:
                got = take()
                if got is None:
                    break
                dl, dline = got
                process = comment_dim <= 0
                s_begin = dline.find('"')
                s_end = dline.find('"', s_begin + 1) if s_begin >= 0 else -1
                outside = lambda p: s_begin < 0 or p < s_begin or p > s_end  # noqa: E731
                pos = dline.find("//")
                if pos >= 0:
                    if outside(pos):
                        dline = dline[:pos]
                    if dline == "":
                        continue
                pos, end = dline.find("/*"), dline.find("*/")
                if pos >= 0:
                    if outside(pos):
                        if end >= 0 and (end < s_begin or end > s_end):
                            dline = dline[:pos] + dline[end + 2:]
                            end = -1
                        else:
                            dline = dline[:pos]
                            comment_dim += 1
                    process = comment_dim <= 1
                if end >= 0:
                    if outside(end):
                        dline = dline[end + 2:]
                        comment_dim -= 1
                    process = comment_dim <= 0
                if dline.lstrip(" \t")[:1] == "}" and process:
                    break
                if not process or dline == "":
                    continue
                defaults.append((dl, dline))
            continue

        # Script text: kept verbatim. The scan below only decides what to look in.
        script.append((ln, line))
        if _string_end_hangs(line):
            return Imported(script, defaults, cpptext, class_name, base_name, deps, ln)
        s_begin = line.find('"')
        s_end = -1
        if s_begin >= 0:
            s_end = s_begin
            while True:
                nxt = line.find('"', s_end + 1)
                if nxt < 0:
                    break
                s_end = nxt
                if not (line[s_end - 1] == "\\" and (s_end < 2 or line[s_end - 2] != "\\")):
                    break
        outside = lambda p: s_begin < 0 or p < s_begin or p > s_end  # noqa: E731
        stripped = line
        pos = stripped.find("//")
        if pos >= 0:
            if outside(pos):
                stripped = stripped[:pos]
            if stripped == "":
                continue
        pos, end = stripped.find("/*"), stripped.find("*/")
        if pos >= 0:
            if outside(pos):
                if end >= 0 and (end < s_begin or end > s_end):
                    stripped = stripped[:pos] + stripped[end + 2:]
                    end = -1
                else:
                    stripped = stripped[:pos]
                    comment_dim += 1
            process = comment_dim <= 1
        if end >= 0:
            if outside(end):
                stripped = stripped[end + 2:]
                comment_dim -= 1
            process = comment_dim <= 0
        if not process or stripped == "":
            continue

        if not class_name:
            k = _strfind(stripped, "class")
            if k >= 0:
                class_name = _token_after(stripped, k + 6)
        if not base_name:
            k = _strfind(stripped, "extends")
            if k >= 0:
                base_name = _token_after(stripped, k + 7).rstrip(";")
        k = 0
        while True:
            k = _strfind(stripped, "dependson(", k)
            if k < 0:
                break
            k += 10
            e = stripped.find(")", k)
            deps.append(stripped[k:e if e >= 0 else len(stripped)])

    return Imported(script, defaults, cpptext, class_name, base_name, deps, None)


def expand_includes(script_text: str, package_dir: Path | None, depth: int = 0) -> str:
    """Splice `#include <file>` lines the way the compiler does: the line is replaced
    by the file's text up to `defaultproperties` (case-sensitive, as UCC searches).
    The path is relative to the package directory, where UCC compiles from. The
    included text is inserted after import, so the importer's scan never sees it."""
    if package_dir is None or depth > 8 or "#include" not in script_text.lower():
        return script_text
    out = []
    for line in script_text.split("\r\n"):
        s = line.strip()
        if s[:1] == "#" and s[1:].lstrip().lower().startswith("include"):
            rel = s[1:].lstrip()[len("include"):].strip().split("//")[0].strip()
            target = package_dir / rel.replace("\\", "/")
            try:
                text = decode(target.read_bytes())
            except OSError:
                out.append(line)              # UCC: "include file ... not found"
                continue
            k = text.find("defaultproperties")
            if k >= 0:
                text = text[:k]
            text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
            out.append(expand_includes(text, package_dir, depth + 1).rstrip("\r\n"))
            continue
        out.append(line)
    return "\r\n".join(out)


def _strfind(s: str, find: str, start: int = 0) -> int:
    """appStrfind: case-insensitive, and only where the match does not follow a
    letter or digit (an underscore does not count, so `my_class` matches)."""
    find = find.upper()
    alnum = start > 0 and (s[start - 1].upper().isascii() and s[start - 1].upper().isalnum())
    for i in range(start, len(s)):
        if not alnum and s[i:i + len(find)].upper() == find:
            return i
        c = s[i].upper()
        alnum = ("A" <= c <= "Z") or ("0" <= c <= "9")
    return -1


def _token_after(s: str, k: int) -> str:
    """ParseToken (not quoted): skip blanks, then up to the next blank."""
    s = s[k:].lstrip(" \t")
    out = []
    for c in s:
        if c in " \t":
            break
        out.append(c)
    return "".join(out)
