"""What the parser knows about other classes: packages, parents, structs and enums.

UCC compiles a class against the classes already loaded -- its own package's, built
from source, and its dependencies', read from their compiled packages. This index
mirrors that: classes with source (the engine checkout, the local corpus, any
directory added) are indexed from their declarations; any other class comes from a
compiled package in the install's System/, through reflect.py.

Type resolution follows UCC's order: a struct or enum declared in the class or an
ancestor, then one declared anywhere, then a class.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class ClassInfo:
    def __init__(self, name: str, package: str, super_: str | None):
        self.name, self.package, self.super = name, package, super_
        self.structs: dict[str, str] = {}   # lower name -> declared name
        self.enums: dict[str, str] = {}
        self.functions: dict[str, str] = {}  # lower name -> declared name
        self.states: dict[str, dict[str, str]] = {}   # state -> its functions
        self.config: str | None = None       # own config(Name), if declared
        self.class_flags: list[str] = []     # own flags (compiled: effective)
        self.within: str | None = None
        self.struct_fields: dict[str, list] = {}   # lower struct name -> its member fields
        self.compiled = False                # flags are already effective
        self.function_defs: dict[str, dict] = {}   # lower name -> function field
        self.has_localized = False
        self.has_config = False
        self.consts: dict[str, list[str]] = {}    # lower const name -> value tokens
        self.state_ext: dict[str, str] = {}       # lower state -> the state it extends
        self.vars: set[str] = set()

    def path(self) -> str:
        return f"{self.package}.{self.name}"


def _config() -> dict:
    try:
        with (Path.home() / ".sweeney" / "config.json").open() as f:
            return json.load(f)
    except Exception:
        return {}


class Context:
    def __init__(self):
        self.classes: dict[str, ClassInfo] = {}     # lower class name -> info
        self.struct_owner: dict[str, str] = {}      # lower struct -> lower class
        self.enum_owner: dict[str, str] = {}
        self._compiled_loaded = False
        self.compiled_class_flags: dict[str, list[str]] = {}
        self.by_package: dict[tuple[str, str], ClassInfo] = {}

    # ------------------------------------------------------------ building

    def add_source_dir(self, package: str, files: list[Path]) -> None:
        from .importer import import_class
        from .lexer import tokenize, LexError
        from .decl import parse_declarations, DeclError
        from .importer import expand_includes
        for f in files:
            try:
                im = import_class(f.read_bytes())
                text = expand_includes(im.script_text(), f.parent.parent
                                       if f.parent.name.lower() == "classes" else f.parent)
                view = parse_declarations(tokenize(text), f.stem)
            except (LexError, DeclError, OSError):
                continue
            self.add_view(package, view)

    def add_view(self, package: str, view: dict) -> None:
        name = view.get("name") or ""
        if not name:
            return
        sup = (view.get("super") or "").split(".")[-1] or None
        info = ClassInfo(name, package, sup)
        info.config = view.get("config")
        info.class_flags = list(view.get("class_flags") or [])
        info.within = view.get("within")
        for f in view.get("fields", []):
            low = f["name"].lower()
            if f["kind"] == "Struct":
                info.structs[low] = f["name"]
                info.struct_fields[low] = f.get("fields", [])
                self.struct_owner.setdefault(low, name.lower())
            elif f["kind"] == "Enum":
                info.enums[low] = f["name"]
                self.enum_owner.setdefault(low, name.lower())
            elif f["kind"] == "Function":
                info.functions[low] = f["name"]
                info.function_defs[low] = f
            elif f["kind"].endswith("Property"):
                info.vars.add(low)
                fl = f.get("flags") or []
                if "localized" in fl:
                    info.has_localized = True
                if "config" in fl or "globalconfig" in fl:
                    info.has_config = True
            elif f["kind"] == "Const":
                info.consts[low] = f.get("_const_tokens") or [str(f.get("value", "")).strip()]
            elif f["kind"] == "State":
                from .resolve import PROBE_NAMES
                info.states[low] = {g["name"].lower(): g["name"] for g in f.get("fields", [])
                                    if g["kind"] == "Function" and not
                                    (g.get("_ignored") and g["name"].lower() in PROBE_NAMES)}
                if f.get("super"):
                    # Source: the `extends` name. Compiled: a path, whose last part is
                    # the parent state (a same-named state in the parent class counts).
                    ext = f["super"].split(".")[-1]
                    if ext.lower() != low or "." not in f["super"]:
                        info.state_ext[low] = ext
        self.classes.setdefault(name.lower(), info)
        self.by_package.setdefault((package.lower(), name.lower()), info)

    def _load_compiled(self) -> None:
        """Fall back to every compiled package in the install, for classes with no
        source here. Loaded once, on first miss."""
        if self._compiled_loaded:
            return
        self._compiled_loaded = True
        inst = Path(_config().get("install_root") or "")
        tools = Path(__file__).resolve().parents[2] / "tools" / "uparse_oracle"
        if not (inst / "System").is_dir() or not tools.is_dir():
            return
        sys.path.insert(0, str(tools))
        try:
            import reflect
        except Exception:
            return
        intrinsic: list[tuple[str, str]] = []
        for u in sorted((inst / "System").glob("*.u")):
            try:
                pkg, objects, _ = reflect.load(u)
            except Exception:
                continue
            for path, o in objects.items():
                if o["kind"] == "Class":
                    self.compiled_class_flags.setdefault(o["name"].lower(), o["class_flags"])
                if o["kind"] != "Class" or o["name"].lower() in self.classes:
                    continue
                view = reflect.class_view(pkg, objects, path)
                self.add_view(pkg.name, view)
                self.classes[o["name"].lower()].compiled = True
            intrinsic.extend(pkg.imports_of_class("Class"))
        # Intrinsic classes (Font, Model, ...) are registered by the engine's C++ and
        # exported by no package; packages that use one import it, which names it.
        for name, path in intrinsic:
            if name.lower() not in self.classes and "." in path:
                self.classes[name.lower()] = ClassInfo(name, path.split(".")[0], None)

    # ------------------------------------------------------------ queries

    def info(self, cls: str | None, package: str | None = None) -> ClassInfo | None:
        """A class by name. `Pkg.Name` or `package` picks among same-named classes
        in different packages (WS3SPN and WSUTComp both have an Emitter_Damage)."""
        if not cls:
            return None
        parts = cls.split(".")
        if len(parts) == 2:
            package = parts[0]
        low = parts[-1].lower()
        if package and (package.lower(), low) in self.by_package:
            return self.by_package[(package.lower(), low)]
        if low not in self.classes:
            self._load_compiled()
        return self.classes.get(low)

    def ancestry(self, cls: str | None):
        seen = set()
        info = self.info(cls)
        while info and info.name.lower() not in seen:
            seen.add(info.name.lower())
            yield info
            info = self.info(info.super)

    def state_function(self, start: str | None, state: str, func: str, _seen=None) -> str | None:
        """Path of `func` as seen from `state`, searching from class `start` up: the
        state itself, then the state it extends, then the same-named state in each
        ancestor class."""
        _seen = _seen if _seen is not None else set()
        sl, fl = state.lower(), func.lower()
        for info in self.ancestry(start):
            key = (info.path().lower(), sl)
            if key in _seen:
                return None
            _seen.add(key)
            st = info.states.get(sl)
            if st is None:
                continue
            if fl in st:
                return f"{info.path()}.{state}.{st[fl]}"
            ext = info.state_ext.get(sl)
            if ext:
                r = self.state_function(info.name, ext, func, _seen)
                if r:
                    return r
        return None

    def compiled_flags(self, name: str) -> list[str]:
        """The flags of a class as compiled in the install, whether or not it has
        source here: for flags only the engine binaries know (see resolve.py)."""
        self._load_compiled()
        return self.compiled_class_flags.get(name.lower(), [])

    def effective_flags(self, info: ClassInfo) -> list[str]:
        """A class's flags including what it inherits and implies."""
        if info.compiled:
            return info.class_flags
        flags = [f for f in info.class_flags if not f.startswith("_")]
        if "_native" in info.class_flags:
            for f in self.compiled_flags(info.name):
                if f in ("cacheable", "safereplace") and f not in flags:
                    flags.append(f)
        if info.has_localized and "localized" not in flags:
            flags.append("localized")
        if info.has_config and "config" not in flags:
            flags.append("config")
        parent = self.info(info.super) if info.super else None
        if parent is not None and parent is not info:
            for f in self.effective_flags(parent):
                if f in ("localized", "transient", "perobjectconfig", "safereplace",
                         "instanced", "cacheable", "placeable", "editinlinenew",
                         "collapsecategories", "config") and f not in flags:
                    flags.append(f)
        for cut, flag in (("_notplaceable", "placeable"), ("_noteditinlinenew", "editinlinenew"),
                          ("_dontcollapsecategories", "collapsecategories")):
            if cut in info.class_flags and flag in flags:
                flags.remove(flag)
        return flags

    def has_flag(self, class_path: str, flag: str) -> bool:
        info = self.info(class_path)
        return bool(info) and flag in self.effective_flags(info)

    def struct_needs_ctor(self, struct_path: str, own: dict | None = None, _seen=None) -> bool:
        parts = struct_path.split(".")
        if len(parts) < 2:
            return False
        sname, cname = parts[-1].lower(), parts[-2]
        members = None
        if own is not None and own["name"].lower() == cname.lower():
            for f in own["fields"]:
                if f["kind"] == "Struct" and f["name"].lower() == sname:
                    members = f.get("fields", [])
        if members is None:
            info = self.info(cname)
            members = info.struct_fields.get(sname) if info else None
        if not members:
            return False
        _seen = _seen or set()
        if struct_path.lower() in _seen:
            return False
        _seen.add(struct_path.lower())
        for m in members:
            if m["kind"] in ("StrProperty", "ArrayProperty") or "needctorlink" in (m.get("flags") or []):
                return True
            if m["kind"] in ("StructProperty", "UnresolvedProperty") and m.get("type"):
                r = self.resolve_type(m["type"], cname)
                if r and r[0] == "struct" and self.struct_needs_ctor(r[1], None, _seen):
                    return True
        return False

    def resolve_type(self, word: str, cls: str, own: dict | None = None):
        """('struct'|'enum'|'class', path) for a type name used in class `cls`.

        `own` is the class's own view while it is being parsed (its structs and enums
        may not be in the index yet). Returns None when nothing matches.
        """
        low = word.split(".")[-1].lower()
        qualified = "." in word
        if own is not None and not qualified:
            owner_path = f"{own['package']}.{own['name']}"
            stack = [(owner_path, own["fields"])]
            while stack:
                path, fields = stack.pop(0)
                for f in fields:
                    if f["name"].lower() == low and f["kind"] in ("Struct", "Enum"):
                        return ("struct" if f["kind"] == "Struct" else "enum"), f"{path}.{f['name']}"
                    if f["kind"] == "Struct":
                        stack.append((f"{path}.{f['name']}", f.get("fields", [])))
            parent = own.get("super")
        else:
            parent = cls
        if not qualified:
            for info in self.ancestry(parent):
                if low in info.structs:
                    return "struct", f"{info.path()}.{info.structs[low]}"
                if low in info.enums:
                    return "enum", f"{info.path()}.{info.enums[low]}"
        if own is not None and low == own["name"].lower():
            return "class", f"{own['package']}.{own['name']}"
        target = self.info(word, own["package"] if own is not None else None)
        if target is not None:
            return "class", target.path()
        self._load_compiled()
        for owners, kind in ((self.struct_owner, "struct"), (self.enum_owner, "enum")):
            if low in owners:
                info = self.classes[owners[low]]
                table = info.structs if kind == "struct" else info.enums
                return kind, f"{info.path()}.{table[low]}"
        return None


_DEFAULT: Context | None = None


def default_context() -> Context:
    """The engine checkout and the local corpus, from ~/.sweeney/config.json."""
    global _DEFAULT
    if _DEFAULT is not None:
        return _DEFAULT
    ctx = Context()
    eng = Path(_config().get("engine_source") or "")
    if eng.is_dir():
        for d in sorted(eng.iterdir()):
            if d.is_dir():
                ctx.add_source_dir(d.name, sorted(d.glob("*.uc")))
    local = Path.home() / ".sweeney" / "oracle" / "corpus-local.txt"
    if local.exists():
        for line in local.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            d = Path(line).expanduser()
            cls_dir = d / "Classes" if (d / "Classes").is_dir() else d
            pkg = cls_dir.parent.name if cls_dir.name == "Classes" else d.name
            ctx.add_source_dir(pkg, sorted(cls_dir.glob("*.uc")))
    _DEFAULT = ctx
    return ctx
