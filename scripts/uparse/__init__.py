"""uparse: an UnrealScript front end that predicts what UCC will do.

See SweeneyParser.md. This is the interface the scoreboard
(tools/uparse_oracle/score.py) measures. Every function may answer "don't know"
(outcome "unknown", or None), which never counts as agreement -- so a stub scores
zero rather than looking right by accident.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class Diag:
    file: str       # basename, e.g. Probe.uc
    line: int       # 0 when UCC gives no line (defaultproperties import, fatal errors)
    message: str    # UCC's text, e.g. "Missing ';' before 'function'"


@dataclasses.dataclass
class Prediction:
    outcome: str                # ok | error | hang | unknown
    errors: list[Diag] = dataclasses.field(default_factory=list)
    # The class defaults UCC would store, in reflect.decode_defaults' shape:
    # {property: {"array index": value}}, names as ["name", "Foo"], strings as
    # ["str", "Foo"]. Only values that differ from the parent's are stored, and a
    # value UCC silently discards is simply absent. None means don't know.
    defaults: dict | None = None


HANG_MESSAGE = "UCC hangs on 'Analyzing...': this line's first string ends in an escaped quote"


@dataclasses.dataclass
class Analysis:
    """Everything the front end learned about one class file."""
    hang_line: int | None = None
    error: Diag | None = None       # the first error UCC would report, if any
    view: dict | None = None        # the resolved declarations (when it parses)


def analyze(name: str, src: bytes, package: str | None = None, context=None,
            path=None) -> Analysis:
    """Run the stages in UCC's order -- importer, lexer, declarations, the checks
    that need other classes -- and keep the first error UCC would hit."""
    from pathlib import Path
    from .importer import import_class, expand_includes
    from .lexer import tokenize_partial
    from .decl import parse_declarations
    from .context import default_context
    from .resolve import resolve_class, deferred_errors

    stem = Path(name).stem
    im = import_class(src)
    if im.hang_line is not None:
        return Analysis(hang_line=im.hang_line)

    # The importer's own checks, before anything is compiled.
    if not im.class_name or (not im.base_name and im.class_name.lower() != "object"):
        n = len(_decoded_len(src))
        return Analysis(error=Diag(name, 0, f"Bad class definition '{im.class_name}'/"
                                            f"'{im.base_name}'/{n}/{n}"))
    if im.class_name.lower() != stem.lower():
        return Analysis(error=Diag(name, 0, f"Script vs. class name mismatch ({stem}/{im.class_name})"))

    text = im.script_text()
    if path is not None:
        p = Path(path)
        pkg_dir = p.parent.parent if p.parent.name.lower() == "classes" else p.parent
        text = expand_includes(text, pkg_dir)
    tokens, lex_error = tokenize_partial(text)
    eof_line = text.count("\r\n") + 1
    view = parse_declarations(tokens, stem, eof_line)
    syntax = view.pop("_error")

    ctx = context or default_context()
    if package is None:
        info = ctx.info(view.get("name") or stem)
        package = info.package if info else "Unknown"

    # Candidates, by token position: the syntax error, the lexer error (after every
    # token read), and the deferred checks on fields declared before the syntax error.
    candidates = []
    if syntax is not None:
        spos = syntax.pos if syntax.pos is not None else len(tokens)
        if not (lex_error is not None and spos >= len(tokens)):
            candidates.append((spos, syntax.line, syntax.message))
    if lex_error is not None:
        candidates.append((len(tokens), lex_error.line, lex_error.message))
    limit = min((c[0] for c in candidates), default=1 << 30)
    for pos, line, msg in deferred_errors(view, package, ctx):
        if pos < limit:
            candidates.append((pos, line, msg))
    if candidates:
        pos, line, msg = min(candidates, key=lambda c: c[0])
        return Analysis(error=Diag(name, line, msg))
    return Analysis(view=resolve_class(view, package, ctx, text))


def _decoded_len(src: bytes) -> str:
    from .importer import decode
    return decode(src)


def predict(packages: dict[str, dict[str, bytes]], deps: list[str] | None = None) -> Prediction:
    """What `UCC make` does with these packages, built in order after the stock ones
    and `deps`. packages: {PackageName: {"Foo.uc": source bytes}}.

    So far: the importer's hang, then the first error from the importer, lexer and
    declaration checks. The importer runs over every class before anything compiles,
    so a hang anywhere wins. Errors in function bodies aren't checked yet, so a class
    that gets through is "unknown", not "ok".
    """
    first_error = None
    for pkg, files in packages.items():
        for name, src in files.items():
            a = analyze(name, src, package=pkg)
            if a.hang_line is not None:
                return Prediction("hang", [Diag(name, a.hang_line, HANG_MESSAGE)])
            if a.error is not None and first_error is None:
                first_error = a.error
    if first_error:
        return Prediction("error", [first_error])
    return Prediction("unknown")


def check_file(name: str, src: bytes) -> list[Diag] | None:
    """Syntax-level diagnostics for one file. [] means nothing found at the stages
    implemented so far (importer, lexer, declarations); None means don't know."""
    a = analyze(name, src)
    if a.hang_line is not None:
        return [Diag(name, a.hang_line, HANG_MESSAGE)]
    return [a.error] if a.error else []


def declarations(name: str, src: bytes, package: str | None = None, context=None,
                 path=None) -> dict | None:
    """The class's declarations, in the shape reflect.py's class_view gives for the
    compiled class: {"name", "super", "fields": [{"name", "kind", "flags", ...}]}.
    None means don't know: the file doesn't get through the front end.

    `package` is the package the class is built into; by default it's looked up in
    the context (the engine checkout and the local corpus). Types are resolved
    against `context`, by default that same index. `path`, the file's location, lets
    `#include` lines be resolved (relative to the package directory).
    """
    return analyze(name, src, package, context, path).view
