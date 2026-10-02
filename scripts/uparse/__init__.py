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


def predict(packages: dict[str, dict[str, bytes]], deps: list[str] | None = None) -> Prediction:
    """What `UCC make` does with these packages, built in order after the stock ones
    and `deps`. packages: {PackageName: {"Foo.uc": source bytes}}."""
    return Prediction("unknown")


def check_file(name: str, src: bytes) -> list[Diag] | None:
    """Syntax-level diagnostics for one file, without resolving other classes.
    [] means it parses clean; None means don't know."""
    return None


def declarations(name: str, src: bytes) -> dict | None:
    """The class's declarations, in the shape reflect.py's class_view gives for the
    compiled class: {"name", "super", "fields": [{"name", "kind", "flags", ...}]}.
    None means don't know."""
    return None
