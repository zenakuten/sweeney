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
    defaults: dict | None = None    # what UCC would store for the class's defaults
    compiled: bool = False          # every stage modelled and clean: UCC would compile it
    unknown: str | None = None      # why it isn't "compiled" when there's no error


def analyze(name: str, src: bytes, package: str | None = None, context=None,
            path=None, visible=None) -> Analysis:
    """Run the stages in UCC's order -- importer, lexer, declarations, the checks
    that need other classes -- and keep the first error UCC would hit. `visible`
    limits other classes to the packages this build loads (None: everything known)."""
    from .context import default_context
    ctx = context or default_context()
    with ctx.only(visible):
        return _analyze(name, src, package, ctx, path)


def _analyze(name, src, package, context, path) -> Analysis:
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
    if any(d.strip() == "" for d in im.dependson):
        # dependson() makes an empty name, and UCC crashes on it.
        return Analysis(error=Diag(name, 0, "General protection fault!"))

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
    resolved = resolve_class(view, package, ctx, text)
    resolved["_rep_conditions"] = view.get("_rep_conditions", [])
    resolved["_rep_statements"] = view.get("_rep_statements", [])

    # defaultproperties: imported after everything compiles. Errors there carry no
    # line (0), and UCC's output shows the last line logged.
    # Object literals in code: compiled in pass 2, before defaults are imported.
    # UCC only finds objects already loaded ("Can't find Sound 'Pkg.Name'").
    lit = _object_literal_error(tokens, ctx, package, name)

    # Function bodies and state code (pass 2).
    from .body import compile_bodies, Unsupported
    bodies_ok = True
    unknown = None
    try:
        err = compile_bodies(tokens, _body_spans(resolved), resolved, ctx, package, eof_line)
    except Unsupported as e:
        err, bodies_ok, unknown = None, False, f"unsupported: {e}"
    except RecursionError:
        err, bodies_ok, unknown = None, False, "recursion"
    except Exception as e:
        err, bodies_ok, unknown = None, False, f"crash: {type(e).__name__}: {e}"
    if err is not None:
        return Analysis(error=Diag(name, err.line, err.message), view=resolved)
    if lit is not None:
        # The bodies stopped short of it, but a literal that finds no object is an
        # error wherever it is; which one UCC meets first is a guess.
        return Analysis(error=lit, view=resolved)

    from .defaults import predict_defaults
    stored, logged, failed, defaults_checked = predict_defaults(
        _with_inner(resolved), [t for _, t in im.defaults], package, ctx, _stock_packages(ctx),
        has_exec=bool(_EXEC_LINE.search(text)))
    if failed and logged:
        return Analysis(error=Diag(name, 0, logged[-1]), view=resolved)
    # A 64-character identifier gets through the lexer (65 doesn't) but can't be made
    # a name: "Unhashed name '<63 characters>'", once everything else is done.
    for t in tokens:
        if t.kind == "ident" and len(t.text) >= 64:
            return Analysis(error=Diag(name, 0, f"Unhashed name '{t.text[:63]}'"), view=resolved)
    if not defaults_checked and unknown is None:
        from . import defaults as _d
        unknown = f"defaults: {getattr(_d, 'last_unknown_reason', None)}"
    return Analysis(view=resolved, defaults=stored, compiled=bodies_ok and defaults_checked,
                    unknown=unknown)


import re as _re
_EXEC_LINE = _re.compile(r"^[ \t]*#[ \t]*exec\b", _re.I | _re.M)


def _body_spans(view: dict) -> list:
    """Every body, in the order UCC's second pass compiles them: the replication
    block first, then the class's functions and states newest-first (the order
    UCC's child list is stored in); inside a state, its functions newest-first,
    then its code."""
    spans = []
    for st in view.get("_rep_statements", []):
        spans.append((None, None, st["cond"][0], st["cond"][1], "replication", st))
    for f in reversed(view["fields"]):
        if f["kind"] == "Function" and f.get("_body"):
            spans.append((f, None, f["_body"][0], f["_body"][1], "function", None))
        elif f["kind"] == "State":
            for g in reversed(f.get("fields", [])):
                if g["kind"] == "Function" and g.get("_body"):
                    spans.append((g, f, g["_body"][0], g["_body"][1], "function", None))
            if f.get("_code"):
                spans.append((None, f, f["_code"][0], f["_code"][1], "state", None))
    return spans


def _object_literal_error(tokens, ctx, package: str, name: str) -> "Diag | None":
    """The first `Type'Pkg.Name'` literal in the script whose object isn't loaded.
    Only claimed when sure: a class type that isn't an Actor (else it isn't an object
    literal), and a package we can read. Anything doubtful stops the check."""
    from .lexer import OBJECT, IDENT, NAME
    if ctx.visible is None:
        return None
    for i, t in enumerate(tokens):
        if t.kind not in (OBJECT, NAME) or i == 0 or tokens[i - 1].kind != IDENT:
            continue
        if t.kind == NAME and (not t.text or " " in t.text):
            continue
        tname = tokens[i - 1].text
        if t.kind == NAME and ctx.info(tname) is None:
            continue                      # `case 'Foo'`, `return 'Foo'`: a name, not an object
        tinfo = ctx.info(tname)
        if tinfo is None:
            return None
        if any(a.name.lower() == "actor" for a in ctx.ancestry(tinfo.name)):
            return None
        problem = literal_problem(ctx, package, name.rsplit(".", 1)[0], tinfo.name, t.text)
        if problem == "unknown":
            return None
        if problem is not None:
            return Diag(name, t.line, problem)
    return None


def literal_problem(ctx, package: str, own: str, type_name: str, path: str) -> str | None:
    """Whether the object literal type_name'path' finds its object: None when it does,
    the message when it doesn't, "unknown" when we can't tell."""
    if ctx.visible is None:
        return "unknown"
    pkg = path.split(".")[0].lower()
    if pkg in (package.lower(), own.lower()):
        return None
    if type_name.lower() == "class":
        if ctx.info(path) is not None:
            return None
        if "." in path and pkg in ctx.visible:
            return "unknown"              # loaded, but we may just not know the class
    else:
        hit = ctx.find_loaded(path)
        if hit == "ambiguous":
            return "unknown"
        if hit is not None:
            return None
    return f"Can't find {type_name} '{path}'"


def _with_inner(view: dict) -> dict:
    """The resolved view, with array element types under "inner_field"."""
    def fix(f):
        g = dict(f)
        if "_inner_field" in g:
            g["inner_field"] = g["_inner_field"]
        if "fields" in g:
            g["fields"] = [fix(x) for x in g["fields"]]
        return g
    return {**view, "fields": [fix(f) for f in view["fields"]]}


def _stock_packages(ctx) -> list[str]:
    """EditPackages as shipped (Default.ini): the packages loaded while compiling."""
    from pathlib import Path
    import json
    try:
        cfg = json.load(open(Path.home() / ".sweeney" / "config.json"))
        root = Path(ctx.compiled_root or cfg.get("install_root"))
        text = (root / "System" / "Default.ini").read_text(encoding="latin-1")
    except Exception:
        return []
    return [l.split("=", 1)[1].strip() for l in text.splitlines()
            if l.strip().lower().startswith("editpackages=")]


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
    from .context import default_context
    first_error = None
    defaults = None
    all_ok = True
    ctx = default_context(prefer_compiled=True)
    visible = _stock_packages(ctx) + list(deps or []) + list(packages)
    for pkg, files in packages.items():
        for name, src in files.items():
            a = analyze(name, src, package=pkg, context=ctx, visible=visible)
            if a.hang_line is not None:
                return Prediction("hang", [Diag(name, a.hang_line, HANG_MESSAGE)])
            if a.error is not None and first_error is None:
                first_error = a.error
            if len(files) == 1:
                defaults = a.defaults
            all_ok = all_ok and a.compiled
    if first_error:
        return Prediction("error", [first_error])
    return Prediction("ok" if all_ok else "unknown", defaults=defaults)


def check_file(name: str, src: bytes, path=None) -> list[Diag] | None:
    """Syntax-level diagnostics for one file. [] means nothing found at the stages
    implemented so far (importer, lexer, declarations); None means don't know."""
    a = analyze(name, src, path=path)
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
