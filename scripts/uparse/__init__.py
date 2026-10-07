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
    # Why the answer is "unknown": (file, what wasn't modelled), one per class.
    unknown: list[tuple[str, str]] = dataclasses.field(default_factory=list)


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
            path=None, visible=None, includes=None, package_exec: bool = True) -> Analysis:
    """Run the stages in UCC's order -- importer, lexer, declarations, the checks
    that need other classes -- and keep the first error UCC would hit. `visible`
    limits other classes to the packages this build loads (None: everything known).
    `includes` reads #include paths (rel path -> text or None) when there's no `path`
    to find them from; with neither, a class that includes a file is "don't know".
    `package_exec` False says no class of the package has #exec lines, so the
    package holds only its classes and their subobjects."""
    from .context import default_context
    ctx = context or default_context()
    with ctx.only(visible):
        return _analyze(name, src, package, ctx, path, includes, package_exec)


def _analyze(name, src, package, context, path, includes=None, package_exec=True) -> Analysis:
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
    ctx0 = context
    if ctx0 is not None and ctx0.visible is not None and any(
            ctx0.info(d.strip()) is None and d.strip().lower() != stem.lower()
            for d in im.dependson):
        # So does a dependson class that isn't loaded (not in this package or one
        # loaded before it).
        return Analysis(error=Diag(name, 0, "General protection fault!"))

    text = im.script_text()
    missing: set = set()
    reader = includes
    if path is not None:
        p = Path(path)
        reader = p.parent.parent if p.parent.name.lower() == "classes" else p.parent
    includes_unknown = reader is None and _INCLUDE_LINE.search(text) is not None
    text = expand_includes(text, reader, missing=missing)
    tokens, lex_error = tokenize_partial(text)
    eof_line = text.count("\r\n") + 1
    view = parse_declarations(tokens, stem, eof_line, missing)
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
    order_errors, order_unknown = _parse_order_refs(view, stem, im.dependson, package, ctx)
    for pos, line, msg in order_errors:
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
        has_exec=bool(_EXEC_LINE.search(text)) and package_exec, package_exec=package_exec)
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
    if includes_unknown and unknown is None:
        unknown = "include file: nowhere to look"
    if order_unknown and unknown is None:
        unknown = order_unknown
    return Analysis(view=resolved, defaults=stored,
                    compiled=bodies_ok and defaults_checked and not includes_unknown
                    and not order_unknown,
                    unknown=unknown)


def _parse_order_refs(view: dict, stem: str, dependson, package: str, ctx):
    """`Other.Type` declarations naming a class of the package being built.

    UCC's first pass resolves that type when it parses this class, so Other must
    have been parsed already, or it's "Unrecognized type 'Type' within 'Other'"
    (tests/uparse/suites/parseorder.jsonl). Parents are parsed before their
    subclasses, and a dependson() class (this class's, or an ancestor's) before the
    class naming it. So an ancestor or a dependson class is fine and a subclass is
    an error. Anything else depends on the order of the engine's class branches
    (an Info subclass is parsed before a Controller one). That order is measured,
    not derived: data/classorder.json (tools/uparse_oracle/classorder.py) holds the
    turn UCC gives a new subclass of each stock class. A class whose parent is
    stock class P is parsed at P's turn; one under a dependency's class, just
    before its nearest stock ancestor's turn (dependencies load after the stock
    packages, so their subtrees come after the stock ones but before the package's
    own direct subclasses). Two classes at the same turn (same parent) follow the
    order the package's classes were created in, which isn't modelled: "don't
    know". So is any pair a dependson() elsewhere in the package could reorder.
    Returns ([(token pos, line, message)], unknown reason or None)."""
    if package is None or package.lower() not in ctx.building:
        return [], None
    pkg = package.lower()
    me = (view.get("name") or stem).lower()

    def own(cls):
        info = ctx.info(cls, package)
        return info if info is not None and info.package.lower() == pkg else None

    def chain(cls):
        """cls and its ancestors inside the package, lower case."""
        out, info = [], own(cls)
        while info is not None and info.name.lower() not in out:
            out.append(info.name.lower())
            info = own(info.super) if info.super else None
        return out

    ancestors = chain(me)[1:] or [(view.get("super") or "").split(".")[-1].lower()]
    # Parsed before this class: dependson classes (and their ancestors and their own
    # dependson classes) of this class and of its ancestors in the package.
    before, todo = set(), [d.strip() for d in dependson if d.strip()]
    for a in ancestors:
        info = own(a)
        if info is not None:
            todo.extend(info.dependson)
    while todo:
        d = todo.pop()
        for c in chain(d) or [d.lower()]:
            if c not in before:
                before.add(c)
                info = own(c)
                if info is not None:
                    todo.extend(info.dependson)

    # Classes some dependson() parses early: the target, and when its parent in the
    # package isn't parsed yet, that parent's whole subtree.
    pulled = set()
    for info in ctx.by_package.values():
        if info.package.lower() != pkg:
            continue
        for d in info.dependson:
            c = chain(d)
            pulled.update(c[1:] if len(c) > 1 else c)
    def subtree_of(low):
        return {c for (p, c), i in ctx.by_package.items() if p == pkg and low in chain(c)}
    pulled_all = set()
    for c in pulled:
        pulled_all |= subtree_of(c)

    errors, unknown = [], None
    for t in _qualified_types(view.get("fields", [])):
        parts = t["type"].split(".")
        if len(parts) != 2:
            continue
        other = own(parts[0])
        if other is None or other.name.lower() == me:
            continue
        low = other.name.lower()
        if low in ancestors or low in before:
            continue
        if me in chain(low):
            errors.append((t.get("_tpos", 0), t.get("_line", 0),
                           f"Unrecognized type '{parts[1]}' within '{parts[0]}'"))
            continue
        first = None if me in pulled_all else _parsed_first(me, low, chain, ctx)
        if first == "other":
            continue
        if first == "me" and low not in pulled_all:
            errors.append((t.get("_tpos", 0), t.get("_line", 0),
                           f"Unrecognized type '{parts[1]}' within '{parts[0]}'"))
        elif unknown is None:
            unknown = (f"parse order: {t['type']} needs {other.name} parsed first, and the "
                       f"order of different class branches isn't modelled "
                       f"(dependson({other.name}) makes it certain)")
    return errors, unknown


_CLASS_ORDER = None


def _class_order(ctx) -> dict | None:
    """{lower 'pkg.class': turn} from data/classorder.json, when it was measured
    with this install's UCC; None otherwise."""
    global _CLASS_ORDER
    if _CLASS_ORDER is None:
        import json
        from pathlib import Path
        from .context import _config
        _CLASS_ORDER = {}
        try:
            d = json.loads((Path(__file__).parent / "data" / "classorder.json").read_text())
            if d.get("ucc_id") and d["ucc_id"] == _config().get("ucc_id"):
                _CLASS_ORDER = {c.lower(): i for i, c in enumerate(d["order"])}
        except (OSError, ValueError):
            pass
    return _CLASS_ORDER or None


def _parsed_first(me: str, other: str, chain, ctx) -> str | None:
    """Which of two package classes in different branches UCC parses first:
    "me", "other", or None when it can't be told."""
    order = _class_order(ctx)
    if order is None:
        return None
    stock = set(order)

    def key(low):
        top = chain(low)[-1]
        parent = (ctx.info(top).super or "") if ctx.info(top) else ""
        info, dep = ctx.info(parent), False
        while info is not None and info.path().lower() not in stock:
            dep = True                   # a dependency's class: before the stock turn
            info = ctx.info(info.super) if info.super else None
        if info is None:
            return None
        return (order[info.path().lower()], 0 if dep else 1, parent.lower() if dep else "")
    a, b = key(me), key(other)
    if a is None or b is None or a == b:
        return None
    if a[:2] == b[:2]:
        return None                      # two dependency branches: their load order
    return "me" if a < b else "other"


def _qualified_types(fields):
    """Every declared type written `Class.Type`: variables, struct members,
    function parameters, return values and locals, in states too."""
    for f in fields:
        for t in (f.get("_type"), (f.get("_ret") or {}).get("_type") if isinstance(f.get("_ret"), dict) else None):
            while t:
                if t.get("kind") == "UnresolvedProperty" and "." in (t.get("type") or ""):
                    yield t
                t = t.get("inner_type") if t.get("kind") == "ArrayProperty" else None
        if f.get("kind") in ("Struct", "Function", "State"):
            yield from _qualified_types(f.get("fields") or [])


import re as _re
_EXEC_LINE = _re.compile(r"^[ \t]*#[ \t]*exec\b", _re.I | _re.M)
_INCLUDE_LINE = _re.compile(r"^[ \t]*#[ \t]*include\b", _re.I | _re.M)


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
        return _own_package_problem(ctx, package, type_name, path)
    if type_name.lower() == "class":
        if ctx.info(path) is not None:
            return None
        if "." in path and pkg in ctx.visible:
            return "unknown"              # loaded, but we may just not know the class
    else:
        hit = ctx.find_loaded(path, type_name)
        if hit == "ambiguous":
            return "unknown"
        if hit is not None:
            return None
        if "." not in path and ctx.exec_packages:
            return "unknown"              # maybe imported by an #exec of the build
    return f"Can't find {type_name} '{path}'"


# Classes no object of a package being built can be in the first pass but one an
# #exec made: its classes and their fields are none of these, and defaultproperties
# subobjects come later, when defaults are imported.
_EXEC_ONLY_TYPES = ("material", "sound", "mesh", "staticmesh", "font", "meshanimation")


def _own_package_problem(ctx, package: str, type_name: str, path: str) -> str | None:
    """A literal into the package being built (Probe.Group.Name): there is nothing
    in it yet but its classes and what its #exec lines made. Only claimed for an
    object type a class can't be, and when every #exec line was modelled."""
    if path.split(".")[0].lower() != package.lower() or package.lower() not in ctx.building \
            or package.lower() in ctx.exec_packages:
        return None
    if not any(ctx._is_a(type_name, t) is True for t in _EXEC_ONLY_TYPES):
        return None
    hits = [h for h in ctx.export_all(package, path) if ctx._is_a(h[1], type_name) is not False]
    if hits:
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


def predict(packages: dict[str, dict[str, bytes]], deps: list[str] | None = None,
            dirs: dict | None = None) -> Prediction:
    """What `UCC make` does with these packages, built in order after the stock ones
    and `deps`. packages: {PackageName: {"Foo.uc": source bytes}}.

    The importer runs over every class before anything compiles, so a hang anywhere
    wins; otherwise the first error -- importer, lexer, declarations, function bodies,
    defaultproperties -- with a package's classes taken parents first, as UCC does.
    "ok" only when every stage of every class was modelled; anything that wasn't
    makes it "unknown". A package's classes see each other, and a key that isn't a
    .uc is an include file, at its path relative to the package directory.
    `defaults` are the first class's stored defaults.

    `dirs` gives a package's directory ({PackageName: path}), where its #exec lines
    find their files (execs.py). Without one, what #exec makes is "don't know".
    """
    from .context import default_context
    from pathlib import Path
    import contextlib
    first_error = None
    defaults = None
    unknown: list = []
    first_name = next((k for f in packages.values() for k in f if k.lower().endswith(".uc")), None)
    all_ok = True
    ctx = default_context(prefer_compiled=True)
    built: list[str] = []
    saved = {k: getattr(ctx, k) for k in
             ("exec_packages", "exec_objects", "building", "package_paths", "closure_roots",
              "exec_incomplete")}
    ctx.exec_packages, ctx.exec_objects, ctx.building = set(), {}, set()
    ctx.exec_incomplete = {}
    ctx.package_paths, ctx.closure_roots = dict(saved["package_paths"]), set(saved["closure_roots"])
    exec_loaded: list[str] = []           # packages #exec lines loaded: they stay loaded

    def restore():
        for k in set(ctx.package_paths) - set(saved["package_paths"]):
            ctx._exports.pop(k, None)     # read from the mod's own directory
            ctx._imports_cache.pop(k, None)
        for k, v in saved.items():
            setattr(ctx, k, v)
    overlays_cm = contextlib.ExitStack()
    overlays_cm.callback(restore)
    with overlays_cm as overlays:
        for pkg, files in packages.items():
            if first_error:
                break                     # UCC stops at the package that failed
            built.append(pkg)
            pdir = next((Path(v) for k, v in (dirs or {}).items() if k.lower() == pkg.lower()), None)
            _run_execs(ctx, pkg, files, pdir, exec_loaded)
            visible = _stock_packages(ctx) + list(deps or []) + built + exec_loaded
            r = _predict_package(ctx, pkg, files, visible, overlays, first_name)
            if r["hang"] is not None:
                return r["hang"]
            first_error = r["error"]
            if r["defaults_set"]:
                defaults = r["defaults"]
            all_ok = all_ok and r["ok"]
            unknown.extend(r["unknown"])
    if first_error:
        return Prediction("error", [first_error])
    return Prediction("ok" if all_ok else "unknown", defaults=defaults, unknown=unknown)


def _run_execs(ctx, pkg: str, files: dict, pdir, exec_loaded: list) -> None:
    """Run the package's #exec lines (execs.py) into the context: every one runs in
    UCC's first pass, before any function body compiles. A package whose #exec lines
    aren't all modelled goes in ctx.exec_packages, so a miss stays "don't know"."""
    from pathlib import Path
    from .importer import import_class, expand_includes
    from . import execs
    import json
    ctx.building.add(pkg.lower())
    extra = {k.replace("\\", "/").lower(): v for k, v in files.items()
             if not k.lower().endswith(".uc")}
    sources = []
    for name, src in files.items():
        if not name.lower().endswith(".uc"):
            continue
        im = import_class(src)
        if im.hang_line is not None:
            continue
        # Expand first: an #exec can live in an #include file (WSUTComp's HUDs).
        text = expand_includes(im.script_text(), lambda rel: (
            lambda v: None if v is None else _decode_text(v))(
                extra.get(rel.replace("\\", "/").lower())))
        if _EXEC_LINE.search(text):
            sources.append((name, text))
    if not sources:
        return
    try:
        cfg = json.load(open(Path.home() / ".sweeney" / "config.json"))
        install = Path(ctx.compiled_root or cfg.get("install_root"))
    except Exception:
        install = None
    res = execs.run_package(pkg, sources, pdir, install, ctx.file_exports)
    for p, objs in res.objects.items():
        for k, v in objs.items():
            ctx.exec_objects.setdefault(p, {}).setdefault(k, [])
            ctx.exec_objects[p][k] = list(dict.fromkeys(ctx.exec_objects[p][k] + v))
        if p != pkg.lower() and p not in exec_loaded:
            exec_loaded.append(p)         # PACKAGE=Other: Other is in memory now
    for p, path in res.loaded.items():
        ctx.package_paths.setdefault(p, path)
        ctx._exports.pop(p, None)
        ctx._imports_cache.pop(p, None)
        if p not in exec_loaded:
            exec_loaded.append(p)
    for path in res.into_files:
        # The file's objects now live in another package, but the packages it
        # imports are loaded (partly) as for any package.
        ctx.closure_roots.update(ctx.file_imports(path))
    if res.incomplete:
        ctx.exec_packages.add(pkg.lower())
        ctx.exec_incomplete[pkg.lower()] = res.incomplete


def _predict_package(ctx, pkg: str, files: dict, visible: list, overlays, first_name) -> dict:
    """One package of a predict() build. Its overlay stays on `overlays`, so the
    packages after it see its classes."""
    from pathlib import Path
    out = {"hang": None, "error": None, "defaults": None, "defaults_set": False,
           "ok": True, "unknown": []}
    # The package directory holds only these files: .uc under Classes, anything
    # else (an include) at the path its key gives, relative to the package.
    extra = {k.replace("\\", "/").lower(): v for k, v in files.items()
             if not k.lower().endswith(".uc")}

    def read(rel, extra=extra):
        v = extra.get(rel.replace("\\", "/").lower())
        return None if v is None else _decode_text(v)
    # Only #exec lines that weren't modelled leave objects we don't know about.
    pkg_exec = pkg.lower() in ctx.exec_packages
    views = _source_views(files, read)
    overlays.enter_context(ctx.overlay(pkg, views))
    for name in _parents_first(files, views):
        src = files[name]
        a = analyze(name, src, package=pkg, context=ctx, visible=visible, includes=read,
                    package_exec=pkg_exec)
        if a.view is not None:
            ctx.set_source_defaults(a.view.get("name") or Path(name).stem,
                                    a.defaults, a.view.get("super"))
        if a.hang_line is not None:
            # The importer reads the whole package before compiling: a hang wins.
            out["hang"] = Prediction("hang", [Diag(name, a.hang_line, HANG_MESSAGE)])
            return out
        if a.error is not None and out["error"] is None:
            out["error"] = a.error
        if name == first_name:
            out["defaults"], out["defaults_set"] = a.defaults, True
        out["ok"] = out["ok"] and a.compiled
        if not a.compiled and a.error is None:
            out["unknown"].append((name, a.unknown or "not modelled"))
    if out["unknown"]:
        out["unknown"].extend(ctx.exec_incomplete.get(pkg.lower(), []))
    return out


def _parents_first(files: dict, views: list[dict]) -> list[str]:
    """The package's .uc files in the order UCC compiles them: a class after its
    parent when both are in the package, otherwise as given."""
    from pathlib import Path
    names = [k for k in files if k.lower().endswith(".uc")]
    by_class = {Path(k).stem.lower(): k for k in names}
    parent = {(v.get("name") or "").lower(): (v.get("super") or "").split(".")[-1].lower()
              for v in views}
    out, seen = [], set()

    def visit(k, depth=0):
        if k in seen or depth > 64:
            return
        p = parent.get(Path(k).stem.lower())
        if p in by_class and by_class[p] != k:
            visit(by_class[p], depth + 1)
        if k not in seen:
            seen.add(k)
            out.append(k)
    for k in names:
        visit(k)
    return out


def _source_views(files: dict, read) -> list[dict]:
    """Declaration views of a package's classes, for the others to see."""
    from .importer import import_class, expand_includes
    from .lexer import tokenize, LexError
    from .decl import parse_declarations, DeclError
    from pathlib import Path
    views = []
    for name, src in files.items():
        if not name.lower().endswith(".uc"):
            continue
        try:
            im = import_class(src)
            if im.hang_line is not None:
                continue
            view = parse_declarations(tokenize(expand_includes(im.script_text(), read)), Path(name).stem)
        except (LexError, DeclError):
            continue
        view["_dependson"] = list(im.dependson)
        if view.get("name"):
            views.append(view)
    return views


def _decode_text(b: bytes) -> str:
    from .importer import decode
    return decode(b).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


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
