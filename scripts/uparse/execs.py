"""#exec lines: the objects a package's #exec directives put in memory before any
function body compiles.

UCC runs a class's #exec lines as it reaches them in the first pass (`ucc make`
bootstraps, so plain #exec runs), and every class of a package is parsed before any
is compiled. So an object literal anywhere in the package -- or in a package built
after it -- finds whatever any #exec of the package made. Measured against the C++
(Editor/Src/UnScrCom.cpp CompileDirective, UnEdSrv.cpp, UMakeCommandlet.cpp):

- While a class compiles the current directory is ../<Package>, so FILE= paths are
  relative to the package directory. OBJ LOAD also searches the install's content
  directories (appFindPackageFile, through the ini Paths).
- TEXTURE IMPORT makes Texture <Package>[.<Group>].<Name>; Package defaults to the
  package being built, Name to the file's base name. A texture that isn't a power of
  two in each direction is not made, and UCC first shows a modal dialog (appMsgf)
  and waits for a click -- unattended, the build looks hung. Reported, not modelled.
- AUDIO IMPORT makes Sound <Package>[.<Group>].<Name> the same way.
- STATICMESH IMPORT makes StaticMesh <Package>[.<Group>].<Name> from a .lwo file
  (the extension is matched case-sensitively); other formats aren't modelled.
- SOUND is no command at all (it appears nowhere in the editor or engine source):
  `#exec SOUND IMPORT` makes nothing, and the build goes on (probed).
- OBJ LOAD FILE=X loads every export of X. With PACKAGE=P they are loaded into P
  (X.Group.Name becomes P.Group.Name); without it, X is a package loaded whole.
- KEY=value is found anywhere in the line, ignoring case (appStrfind); a value ends
  at a blank or a comma, or runs between double quotes.
- An #exec inside a function or state body runs in the second pass instead, after
  literals before it have been compiled. Not modelled.

Anything not modelled -- another command, a file that can't be found or read --
makes the package "incomplete": a literal that finds nothing stays "don't know".
"""

from __future__ import annotations

import dataclasses
import struct
from pathlib import Path

EXEC_DIRECTIVES = {"exec", "alwaysexec", "forceexec"}
CONTENT_DIRS = ("System", "Textures", "Sounds", "Music", "StaticMeshes", "Animations", "Maps")


@dataclasses.dataclass
class ExecResult:
    # {lower package: {lower path: [(path, class name)]}}: objects the #exec lines made
    objects: dict = dataclasses.field(default_factory=dict)
    # Packages loaded whole by OBJ LOAD: {lower package: its file}
    loaded: dict = dataclasses.field(default_factory=dict)
    # Files OBJ LOAD loaded into another package (PACKAGE=): their imports load too.
    into_files: list = dataclasses.field(default_factory=list)
    # What wasn't modelled, as (file, why); empty means every #exec line was.
    incomplete: list = dataclasses.field(default_factory=list)

    def add(self, path: str, cls: str) -> None:
        pkg = path.split(".")[0].lower()
        self.objects.setdefault(pkg, {}).setdefault(path.lower(), [])
        if (path, cls) not in self.objects[pkg][path.lower()]:
            self.objects[pkg][path.lower()].append((path, cls))


def exec_lines(text: str) -> list[tuple[int, str, bool]]:
    """(line, command, top level?) for each #exec in the script text."""
    from .lexer import tokenize_partial, SYMBOL, IDENT, RAW
    tokens, _ = tokenize_partial(text)
    out, depth = [], 0
    for i, t in enumerate(tokens):
        if t.kind == SYMBOL and t.text == "{":
            depth += 1
        elif t.kind == SYMBOL and t.text == "}":
            depth = max(0, depth - 1)
        elif (t.kind == IDENT and t.text.lower() in EXEC_DIRECTIVES and i > 0
              and tokens[i - 1].kind == SYMBOL and tokens[i - 1].text == "#"):
            raw = tokens[i + 1] if i + 1 < len(tokens) and tokens[i + 1].kind == RAW else None
            out.append((t.line, raw.text if raw else "", depth == 0))
    return out


def parse_value(line: str, key: str) -> str | None:
    """Core's Parse(Stream, "KEY=", Value): first match anywhere, ignoring case."""
    at = line.lower().find(key.lower())
    if at < 0:
        return None
    rest = line[at + len(key):]
    if rest.startswith('"'):
        out, j = [], 1
        while j < len(rest) and rest[j] != '"':
            if rest[j] == "\\" and j + 1 < len(rest) and rest[j + 1] in '\\"':
                j += 1
            out.append(rest[j])
            j += 1
        return "".join(out)
    for stop in (" ", "\r", "\n", "\t", ","):
        rest = rest.split(stop, 1)[0]
    return rest


def parse_command(line: str, word: str) -> str | None:
    """Core's ParseCommand: the rest of the line after `word`, or None."""
    s = line.lstrip(" \t")
    if s[:len(word)].lower() != word.lower():
        return None
    rest = s[len(word):]
    if rest[:1].isalnum():
        return None
    return rest.lstrip(" \t")


def find_file(rel: str, package_dir: Path | None, install: Path | None,
              search_content: bool = False) -> Path | None:
    """A FILE= path as UCC opens it: relative to the package directory (ignoring
    case, as Windows does); OBJ LOAD then tries the install's content dirs."""
    rel = rel.replace("\\", "/")
    bases = [package_dir] if package_dir else []
    if search_content and install:
        bases += [install / d for d in CONTENT_DIRS]
    for base in bases:
        p = _icase(base, rel)
        if p is not None:
            return p
    return None


def _icase(base: Path, rel: str) -> Path | None:
    p = base
    for part in [x for x in rel.split("/") if x not in ("", ".")]:
        if part == "..":
            p = p.parent
            continue
        q = p / part
        if not q.exists():
            try:
                q = next((c for c in p.iterdir() if c.name.lower() == part.lower()), None)
            except OSError:
                return None
            if q is None:
                return None
        p = q
    return p if p.is_file() else None


def image_size(path: Path) -> tuple[int, int] | None:
    """(width, height) of an image the texture importer reads, or None."""
    try:
        head = path.read_bytes()[:128]
    except OSError:
        return None
    ext = path.suffix.lower()
    try:
        if ext == ".tga" and len(head) >= 18:
            return struct.unpack_from("<HH", head, 12)
        if ext == ".bmp" and head[:2] == b"BM":
            w, h = struct.unpack_from("<ii", head, 18)
            return w, abs(h)
        if ext == ".pcx" and len(head) >= 12:
            x0, y0, x1, y1 = struct.unpack_from("<HHHH", head, 4)
            return x1 - x0 + 1, y1 - y0 + 1
        if ext == ".dds" and head[:4] == b"DDS ":
            h, w = struct.unpack_from("<II", head, 12)
            return w, h
    except struct.error:
        return None
    return None


def _pow2(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def _base_name(file: str) -> str:
    """The object name UnrealEd deduces from a file name: no directory, no extension."""
    name = file.replace("\\", "/").rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


def run_package(package: str, sources: list[tuple[str, str]], package_dir: Path | None,
                install: Path | None, exports_of) -> ExecResult:
    """What the #exec lines of a package's classes make. `sources` is [(file name,
    script text)]; `exports_of(path)` lists a package file's exports as
    [(path without the package, class name)], or None if it can't be read."""
    res = ExecResult()
    for name, text in sources:
        for line, cmd, top in exec_lines(text):
            why = _run(cmd, top, package, package_dir, install, exports_of, res)
            if why:
                res.incomplete.append((name, f"#exec line {line}: {why}"))
    return res


def _run(cmd, top, package, package_dir, install, exports_of, res) -> str | None:
    """Apply one #exec command to `res`; the reason it isn't modelled, if it isn't."""
    if not top:
        return "inside a body (runs in the second pass)"
    if package_dir is None:
        return "no package directory to find files in"
    rest = parse_command(cmd, "OBJ")
    if rest is not None:
        rest = parse_command(rest, "LOAD")
        return _obj_load(rest, package_dir, install, exports_of, res) if rest is not None \
            else "OBJ command"
    rest = parse_command(cmd, "TEXTURE")
    if rest is not None and parse_command(rest, "IMPORT") is not None:
        return _import(parse_command(rest, "IMPORT"), package, package_dir, "Texture", res)
    rest = parse_command(cmd, "AUDIO")
    if rest is not None and parse_command(rest, "IMPORT") is not None:
        return _import(parse_command(rest, "IMPORT"), package, package_dir, "Sound", res)
    rest = parse_command(cmd, "STATICMESH")
    if rest is not None and parse_command(rest, "IMPORT") is not None:
        return _import(parse_command(rest, "IMPORT"), package, package_dir, "StaticMesh", res)
    if parse_command(cmd, "SOUND") is not None:
        return None                       # not a command: nothing is made
    return f"command {cmd.split(' ', 1)[0] or '(none)'}"


def _import(args, package, package_dir, cls, res) -> str | None:
    file = parse_value(args, "FILE=")
    if not file:
        return "no FILE="
    pkg = parse_value(args, "PACKAGE=") or package
    name = parse_value(args, "NAME=") or _base_name(file)
    group = parse_value(args, "GROUP=")
    if "." in pkg or not name or (group is not None and group.lower() in ("", "none")):
        return "PACKAGE/GROUP/NAME form"
    found = find_file(file, package_dir, None)
    if found is None:
        return f"file {file} not found"
    if cls == "StaticMesh":
        if ".lwo" not in file:
            return f"static mesh from {file}: only .lwo is modelled"
        head = found.read_bytes()[:12]
        if head[:4] != b"FORM" or head[8:12] not in (b"LWO2", b"LWOB"):
            return f"{file} isn't a LightWave object"
    if cls == "Texture":
        size = image_size(found)
        if size is None:
            return f"can't read the size of {file}"
        if not (_pow2(size[0]) and _pow2(size[1])):
            # appMsgf: a modal dialog that UCC waits on until someone clicks it.
            return f"{file} isn't a power of two: UCC pops a modal dialog and waits"
    res.add(".".join(x for x in (pkg, group, name) if x), cls)
    return None


def _obj_load(args, package_dir, install, exports_of, res) -> str | None:
    file = parse_value(args, "FILE=")
    if not file:
        return "OBJ LOAD without FILE="
    into = parse_value(args, "PACKAGE=")
    if into is not None and ("." in into or not into):
        return "OBJ LOAD PACKAGE= form"
    found = find_file(file, package_dir, install, search_content=True)
    if found is None:
        return f"package file {file} not found"
    exports = exports_of(found)
    if exports is None:
        return f"can't read {file}"
    if into:
        for path, cls in exports:
            res.add(f"{into}.{path}", cls)
        res.into_files.append(found)      # its imports load too, but not its name
    else:
        res.loaded[found.stem.lower()] = found
    return None
