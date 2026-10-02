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


def _front_end(name: str, src: bytes) -> Diag | Prediction | None:
    """Run the importer and lexer. A hang, a lexer error, or None if both pass."""
    from .importer import import_class
    from .lexer import tokenize, LexError
    im = import_class(src)
    if im.hang_line is not None:
        return Prediction("hang", [Diag(name, im.hang_line, HANG_MESSAGE)])
    try:
        tokenize(im.script_text())
    except LexError as e:
        return Diag(name, e.line, e.message)
    return None


def predict(packages: dict[str, dict[str, bytes]], deps: list[str] | None = None) -> Prediction:
    """What `UCC make` does with these packages, built in order after the stock ones
    and `deps`. packages: {PackageName: {"Foo.uc": source bytes}}.

    So far: the importer's hang, and the first lexer error. The importer runs over
    every class before anything compiles, so a hang anywhere wins; a lexer error is
    reported where UCC would hit it, unless an earlier parse error stops it first,
    which this can't see yet. Anything else is "unknown".
    """
    first_error = None
    for files in packages.values():
        for name, src in files.items():
            r = _front_end(name, src)
            if isinstance(r, Prediction):
                return r
            if r is not None and first_error is None:
                first_error = r
    if first_error:
        return Prediction("error", [first_error])
    return Prediction("unknown")


def check_file(name: str, src: bytes) -> list[Diag] | None:
    """Syntax-level diagnostics for one file, without resolving other classes.
    [] means nothing found at the stages implemented so far (importer, lexer);
    None means don't know."""
    r = _front_end(name, src)
    if r is None:
        return []
    return r.errors if isinstance(r, Prediction) else [r]


def declarations(name: str, src: bytes) -> dict | None:
    """The class's declarations, in the shape reflect.py's class_view gives for the
    compiled class: {"name", "super", "fields": [{"name", "kind", "flags", ...}]}.
    None means don't know."""
    return None
