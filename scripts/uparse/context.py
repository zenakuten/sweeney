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


# C++-only classes: (package, parent), from DECLARE_CLASS in the engine source.
INTRINSIC_PARENTS = {
    "field": ("Core", "Object"), "struct": ("Core", "Field"), "state": ("Core", "Struct"),
    "class": ("Core", "State"), "function": ("Core", "Struct"), "const": ("Core", "Field"),
    "enum": ("Core", "Field"), "property": ("Core", "Field"),
    "objectproperty": ("Core", "Property"), "classproperty": ("Core", "ObjectProperty"),
    "boolproperty": ("Core", "Property"), "intproperty": ("Core", "Property"),
    "strproperty": ("Core", "Property"), "floatproperty": ("Core", "Property"),
    "structproperty": ("Core", "Property"), "byteproperty": ("Core", "Property"),
    "arrayproperty": ("Core", "Property"), "nameproperty": ("Core", "Property"),
    "pointerproperty": ("Core", "Property"), "delegateproperty": ("Core", "Property"),
    "textbuffer": ("Core", "Object"), "package": ("Core", "Object"),
    "subsystem": ("Core", "Object"),
    "viewport": ("Engine", "Player"), "netconnection": ("Engine", "Player"),
    "font": ("Engine", "Object"), "primitive": ("Engine", "Object"),
    "staticmesh": ("Engine", "Primitive"), "model": ("Engine", "Primitive"),
    "mesh": ("Engine", "Primitive"), "lodmesh": ("Engine", "Mesh"),
    "vertmesh": ("Engine", "LodMesh"), "skeletalmesh": ("Engine", "LodMesh"),
    "convexvolume": ("Engine", "Primitive"), "fluidsurfaceprimitive": ("Engine", "Primitive"),
    "terrainprimitive": ("Engine", "Primitive"), "meshinstance": ("Engine", "Primitive"),
    "levelbase": ("Engine", "Object"), "level": ("Engine", "LevelBase"),
    "pendinglevel": ("Engine", "Object"), "client": ("Engine", "Object"),
    "audiosubsystem": ("Engine", "Subsystem"), "renderdevice": ("Engine", "Subsystem"),
    "renderresource": ("Engine", "Object"), "indexbuffer": ("Engine", "RenderResource"),
    "vertexstreambase": ("Engine", "RenderResource"), "vertexbuffer": ("Engine", "VertexStreamBase"),
    "meshanimation": ("Engine", "Object"), "staticmeshinstance": ("Engine", "Object"),
    "terrainsector": ("Engine", "Object"), "kmeshprops": ("Engine", "Object"),
    "polys": ("Engine", "Object"),
}


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
        self.struct_defs: dict[str, dict] = {}
        self.compiled = False                # flags are already effective
        self.function_defs: dict[str, dict] = {}   # lower name -> function field
        self.has_localized = False
        self.has_config = False
        self.consts: dict[str, list[str]] = {}    # lower const name -> value tokens
        self.state_ext: dict[str, str] = {}       # lower state -> the state it extends
        self.vars: set[str] = set()
        self.state_function_defs: dict[str, dict[str, dict]] = {}
        self.var_defs: dict[str, dict] = {}       # lower property name -> field
        self.enum_values: dict[str, list[str]] = {}

    def path(self) -> str:
        return f"{self.package}.{self.name}"


def _config() -> dict:
    try:
        with (Path.home() / ".sweeney" / "config.json").open() as f:
            return json.load(f)
    except Exception:
        return {}


class Context:
    def __init__(self, compiled_root: Path | None = None):
        self.compiled_root = compiled_root
        self.classes: dict[str, ClassInfo] = {}     # lower class name -> info
        self.struct_owner: dict[str, str] = {}      # lower struct -> lower class
        self.enum_owner: dict[str, str] = {}
        self._compiled_loaded = False
        self.compiled_class_flags: dict[str, list[str]] = {}
        self.compiled_defaults: dict[str, dict] = {}   # lower class -> own stored defaults
        self.compiled_super: dict[str, str] = {}
        self._effective: dict[str, dict] = {}
        self._exports: dict[str, dict | None] = {}
        self._export_all: dict = {}
        # Packages loaded whole (EditPackages, deps, the one being built); the rest
        # of `visible` is only what their import tables name.
        self.fully_loaded: set | None = None
        self.last_hits: list = []                 # find_loaded's in-memory matches
        # Packages being built whose classes have #exec lines: they may hold
        # objects no source declares (imported textures, sounds).
        self.exec_packages: set[str] = set()
        # Classes being built: lower name -> (own stored defaults or None, parent).
        self.source_defaults: dict[str, tuple] = {}
        self._imported_paths: dict[str, set] = {}
        self.by_package: dict[tuple[str, str], ClassInfo] = {}
        self.struct_owners: dict[str, list] = {}  # every class declaring a struct of that name
        self.enum_owners: dict[str, list] = {}
        self.visible: set[str] | None = None      # packages loaded in this build, if known
        self._imports_cache: dict[str, list[str]] = {}

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

    def _info_from_view(self, package: str, view: dict) -> "ClassInfo":
        """A ClassInfo for a view, without touching the by-name indexes."""
        saved = (self.classes, self.by_package, self.struct_owner, self.enum_owner)
        self.classes, self.by_package, self.struct_owner, self.enum_owner = {}, {}, {}, {}
        try:
            self.add_view(package, view)
            return next(iter(self.classes.values()))
        finally:
            self.classes, self.by_package, self.struct_owner, self.enum_owner = saved

    def import_closure(self, packages) -> set[str]:
        """The packages loading these pulls in: each compiled package loads the
        packages its import table names (UTDiscordBridge.u brings LibHTTP4)."""
        inst = Path(self.compiled_root or _config().get("install_root") or "")
        tools = Path(__file__).resolve().parents[2] / "tools" / "uttexture"
        sys.path.insert(0, str(tools))
        from uttexture.ue2 import Package
        out, todo = set(), [p.lower() for p in packages]
        while todo:
            p = todo.pop()
            if p in out:
                continue
            out.add(p)
            if p in self._imports_cache:
                todo.extend(self._imports_cache[p])
                continue
            deps = []
            u = inst / "System" / f"{p}.u"
            if not u.exists():
                hits = [q for q in (inst / "System").glob("*.u") if q.stem.lower() == p]
                u = hits[0] if hits else None
            paths: set = set()
            if u is not None:
                try:
                    pk = Package(str(u))
                    deps = [i["name"].lower() for i in pk.imports
                            if i["class"] == "Package" and i["outer"] == 0]
                    paths = {pk.import_path(-k - 1).lower() for k in range(len(pk.imports))}
                except Exception:
                    deps = []
            self._imported_paths[p] = paths
            self._imports_cache[p] = deps
            todo.extend(deps)
        return out

    def add_view(self, package: str, view: dict) -> None:
        name = view.get("name") or ""
        if not name:
            return
        if (package.lower(), name.lower()) in self.by_package:
            return                        # already known (compiled first): keep that view
        sup = (view.get("super") or "").split(".")[-1] or None
        info = ClassInfo(name, package, sup)
        info.config = view.get("config")
        info.class_flags = list(view.get("class_flags") or [])
        info.within = view.get("within")
        for f in view.get("fields", []):
            low = f["name"].lower()
            if f["kind"] == "Struct":
                info.structs[low] = f["name"]
                self.struct_owners.setdefault(low, []).append(info)
                info.struct_fields[low] = f.get("fields", [])
                info.struct_defs[low] = f

                self.struct_owner.setdefault(low, name.lower())
            elif f["kind"] == "Enum":
                info.enums[low] = f["name"]
                self.enum_owners.setdefault(low, []).append(info)
                info.enum_values[low] = list(f.get("values", []))
                self.enum_owner.setdefault(low, name.lower())
            elif f["kind"] == "Function":
                info.functions[low] = f["name"]
                info.function_defs[low] = f
            elif f["kind"].endswith("Property"):
                info.vars.add(low)
                info.var_defs.setdefault(low, f)
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
                info.state_function_defs[low] = {
                    g["name"].lower(): g for g in f.get("fields", [])
                    if g["kind"] == "Function" and not g.get("_ignored")}
                if f.get("super"):
                    # Source: the `extends` name. Compiled: a path, whose last part is
                    # the parent state (a same-named state in the parent class counts).
                    ext = f["super"].split(".")[-1]
                    if ext.lower() != low or "." not in f["super"]:
                        info.state_ext[low] = ext
        if not info.has_localized and not view.get("_compiled"):
            from .resolve import _has_localized
            info.has_localized = _has_localized(view.get("fields", []))
        self.classes.setdefault(name.lower(), info)
        self.by_package.setdefault((package.lower(), name.lower()), info)

    def _load_compiled(self) -> None:
        """Fall back to every compiled package in the install, for classes with no
        source here. Loaded once, on first miss."""
        if self._compiled_loaded:
            return
        self._compiled_loaded = True
        inst = Path(self.compiled_root or _config().get("install_root") or "")
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
                    low_name = o["name"].lower()
                    self.compiled_class_flags.setdefault(low_name, o["class_flags"])
                    if low_name not in self.compiled_defaults:
                        try:
                            self.compiled_defaults[low_name] = reflect.decode_defaults(pkg, objects, path)
                        except Exception:
                            self.compiled_defaults[low_name] = None
                        self.compiled_super[low_name] = (o.get("super") or "").split(".")[-1].lower()
                if o["kind"] != "Class":
                    continue
                if (pkg.name.lower(), o["name"].lower()) in self.by_package:
                    continue
                view = reflect.class_view(pkg, objects, path)
                if o["name"].lower() in self.classes:
                    # Same name, another package (Soltoolsv14/v15): keep it per package.
                    info = self._info_from_view(pkg.name, view)
                    info.compiled = True
                    self.by_package[(pkg.name.lower(), o["name"].lower())] = info
                    continue
                self.add_view(pkg.name, view)
                self.classes[o["name"].lower()].compiled = True
            intrinsic.extend(pkg.imports_of_class("Class"))
        # Intrinsic classes (Font, Model, ...) are registered by the engine's C++ and
        # exported by no package; packages that use one import it, which names it.
        for name, path in intrinsic:
            if name.lower() not in self.classes and "." in path:
                info = ClassInfo(name, path.split(".")[0], None)
                self.classes[name.lower()] = info
                self.by_package.setdefault((info.package.lower(), name.lower()), info)
        # Their parents, from the C++ DECLARE_CLASS lines; a parent no package names
        # is added too, so a hierarchy walk reaches Object.
        for low, (pkg_name, parent) in INTRINSIC_PARENTS.items():
            info = self.classes.get(low)
            if info is None or info.super is not None or info.name.lower() == "object":
                continue
            info.super = parent
            chain = parent
            while chain and chain.lower() not in self.classes:
                ppkg, pparent = INTRINSIC_PARENTS.get(chain.lower(), (pkg_name, "Object"))
                p = ClassInfo(chain, ppkg, pparent)
                self.classes[chain.lower()] = p
                self.by_package.setdefault((ppkg.lower(), chain.lower()), p)
                chain = pparent

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
        if self.visible is not None:
            # Only classes in packages this build loads (stock, dependencies, its own):
            # a mod built without WSUTComp can't see WSUTComp's classes.
            if package and package.lower() not in self.visible:
                return None
            if package and (package.lower(), low) in self.by_package:
                return self.by_package[(package.lower(), low)]
            self._load_compiled()
            for pkg in self.visible:
                hit = self.by_package.get((pkg, low))
                if hit is not None:
                    return hit
            return None
        if package and (package.lower(), low) in self.by_package:
            return self.by_package[(package.lower(), low)]
        if low not in self.classes:
            self._load_compiled()
        return self.classes.get(low)

    def _load_rank(self) -> dict:
        """{package: position} in the stock EditPackages list (Default.ini)."""
        if getattr(self, "_rank", None) is None:
            inst = Path(self.compiled_root or _config().get("install_root") or "")
            try:
                text = (inst / "System" / "Default.ini").read_text(encoding="latin-1")
                pk = [l.split("=", 1)[1].strip().lower() for l in text.splitlines()
                      if l.strip().lower().startswith("editpackages=")]
            except OSError:
                pk = []
            self._rank = {p: i for i, p in enumerate(pk)}
        return self._rank

    def overlay(self, package: str, views: list[dict]):
        """A context manager adding the classes of a package being built, from
        their source views, in place of anything known under the same names."""
        ctx = self

        class _Overlay:
            def __enter__(self_):
                self_.saved = (ctx.classes, ctx.by_package, ctx.struct_owner, ctx.enum_owner,
                               ctx.struct_owners, ctx.enum_owners, ctx._effective,
                               ctx.source_defaults)
                ctx.source_defaults = dict(ctx.source_defaults)
                ctx.classes, ctx.by_package = dict(ctx.classes), dict(ctx.by_package)
                ctx.struct_owner, ctx.enum_owner = dict(ctx.struct_owner), dict(ctx.enum_owner)
                ctx.struct_owners = {k: list(v) for k, v in ctx.struct_owners.items()}
                ctx.enum_owners = {k: list(v) for k, v in ctx.enum_owners.items()}
                ctx._effective = {}
                pkg = package.lower()
                names = [(v.get("name") or "").lower() for v in views]
                for low in names:
                    old = ctx.by_package.pop((pkg, low), None)
                    if ctx.classes.get(low) is not None and ctx.classes[low].package.lower() == pkg:
                        del ctx.classes[low]
                    if old is not None:
                        for d in (ctx.struct_owners, ctx.enum_owners):
                            for k in d:
                                d[k] = [i for i in d[k] if i is not old]
                for v in views:
                    ctx.add_view(package, v)
                for low in names:
                    info = ctx.by_package.get((pkg, low))
                    if info is not None:
                        ctx.classes[low] = info
                return ctx

            def __exit__(self_, *exc):
                (ctx.classes, ctx.by_package, ctx.struct_owner, ctx.enum_owner,
                 ctx.struct_owners, ctx.enum_owners, ctx._effective,
                 ctx.source_defaults) = self_.saved
        return _Overlay()

    def set_source_defaults(self, cls: str, own: dict | None, parent: str | None) -> None:
        """Record a built class's predicted defaults, for its subclasses."""
        self.source_defaults[cls.lower()] = (own, (parent or "").split(".")[-1].lower() or None)
        self._effective = {}

    def only(self, packages):
        """A context manager limiting lookups to these packages."""
        ctx = self

        class _Only:
            def __enter__(self_):
                self_.saved = ctx.visible, ctx.fully_loaded
                ctx.visible = ctx.import_closure(packages) if packages is not None else None
                ctx.fully_loaded = {p.lower() for p in packages} if packages is not None else None
                return ctx

            def __exit__(self_, *exc):
                ctx.visible, ctx.fully_loaded = self_.saved
        return _Only()

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

    def effective_defaults(self, cls: str | None) -> dict | None:
        """A class's defaults as its subclasses start from: its own stored values over
        its parent's, all the way down from Object, read from the compiled packages.
        None if any class in the chain isn't compiled or doesn't decode."""
        if not cls:
            return {}
        self._load_compiled()
        low = cls.split(".")[-1].lower()
        if low in self._effective:
            return self._effective[low]
        if low in self.source_defaults:
            # A class of the package being built, predicted from its source.
            own, parent = self.source_defaults[low]
        else:
            own = self.compiled_defaults.get(low)
            parent = self.compiled_super.get(low)
        if own is None:
            self._effective[low] = None
            return None
        base = self.effective_defaults(parent) if parent else {}
        if base is None:
            self._effective[low] = None
            return None
        merged = {k: (dict(v) if v is not None else None) for k, v in base.items()}
        for k, v in own.items():
            if any(isinstance(x, list) and x and x[0] == "raw" for x in v.values()):
                merged[k] = None          # can't decode: unknown
                continue
            if k in merged and merged[k] is None:
                continue                  # still unknown: an ancestor's value didn't decode
            merged.setdefault(k, {}).update(v)
        self._effective[low] = merged
        return merged

    def package_exports(self, package: str) -> dict | None:
        """{lower 'Pkg.Group.Name': (path, class name)} for a package on disk, from
        its export table. Searched as UCC's paths are: System, Textures, Sounds,
        StaticMeshes, Animations, Music."""
        low = package.lower()
        if low in self._exports:
            return self._exports[low]
        inst = Path(self.compiled_root or _config().get("install_root") or "")
        found = None
        for d, ext in (("System", ".u"), ("Textures", ".utx"), ("Sounds", ".uax"),
                       ("StaticMeshes", ".usx"), ("Animations", ".ukx"), ("Music", ".umx")):
            p = inst / d / f"{package}{ext}"
            if p.exists():
                found = p
                break
            if (inst / d).is_dir():
                for q in (inst / d).glob(f"*{ext}"):
                    if q.stem.lower() == low:
                        found = q
                        break
            if found:
                break
        if found is None:
            self._exports[low] = None
            return None
        tools = Path(__file__).resolve().parents[2] / "tools" / "uttexture"
        sys.path.insert(0, str(tools))
        from uttexture.ue2 import Package
        try:
            pkg = Package(str(found))
        except Exception:
            self._exports[low] = None
            return None
        out, every = {}, {}
        for i, e in enumerate(pkg.exports):
            path = f"{found.stem}.{pkg.export_path(i)}"
            out.setdefault(path.lower(), (path, pkg.class_of(e)))
            every.setdefault(path.lower(), []).append((path, pkg.class_of(e)))
        self._exports[low] = out
        self._export_all[low] = every
        return out

    def loaded_object(self, package: str, path: str):
        """Whether an object of `package` is in memory: True for a package loaded
        whole, or an object a loaded package imports; None (can't tell) otherwise:
        an imported object can pull others in (a SoundGroup its Sounds)."""
        if self.fully_loaded is None or package.lower() in self.fully_loaded:
            return True
        low = path.lower()
        if any(low in self._imported_paths.get(p, ()) for p in self.fully_loaded):
            return True
        return None

    def _is_a(self, cls: str, parent: str):
        """True/False, or None when the hierarchy runs into an unknown class."""
        if cls.lower() == parent.lower() or parent.lower() == "object":
            return True
        chain = list(self.ancestry(cls))
        if any(i.name.lower() == parent.lower() for i in chain):
            return True
        if not chain or (chain[-1].super is None and chain[-1].name.lower() != "object"):
            return None
        return False

    def export_all(self, package: str, path: str) -> list:
        """Every export at that path: one name can be a mesh and its animation."""
        if self.package_exports(package) is None:
            return []
        return self._export_all.get(package.lower(), {}).get(path.lower(), [])

    def find_loaded(self, path: str, cls: str | None = None):
        """UCC's ANY_PACKAGE lookup over the loaded packages: `path` may start with a
        package, or with a group or class inside any loaded package
        (Sounds.HeadShotted finds WSUTComp.Sounds.HeadShotted). With `cls`, only
        objects of that class or a subclass count (StaticFindObject's class filter:
        XEffects.GibBotCalf is a class and a static mesh). (path, class),
        "ambiguous", or None."""
        self.last_hits = []
        if self.visible is None:
            return "ambiguous"
        want = path.lower()
        first, leaf = want.split(".")[0], want.split(".")[-1]
        hits = []
        for pkg in self.visible:
            exports = self.package_exports(pkg)
            if not exports:
                continue
            for k in exports:
                if k == want or k.endswith("." + want) or \
                        ("." in want and pkg == first and k.split(".")[-1] == leaf):
                    hits.extend(self.export_all(pkg, k))
        if cls is not None:
            hits = [h for h in hits if self._is_a(h[1], cls) is not False]
        hits = list(dict.fromkeys(hits))
        self.last_hits = hits
        unsure = [h for h in hits if self.loaded_object(h[0].split(".")[0], h[0]) is None]
        if unsure:
            hits = [h for h in hits if h not in unsure]
            self.last_hits = hits
            if not hits:
                return "ambiguous"        # there, but maybe not in memory
        if not hits:
            return None
        return hits[0] if len(hits) == 1 else "ambiguous"

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
        if qualified:
            # Class.Struct / Class.Enum names the declaring class outright
            # (ONSPowerlinkOfficialSetupSupplement.TPowernodeSettings), even when the
            # class being compiled has its own struct of that name.
            parts = word.split(".")
            if len(parts) == 2:
                owner = self.info(parts[0], own["package"] if own is not None else None)
                if owner is not None:
                    if low in owner.structs:
                        return "struct", f"{owner.path()}.{owner.structs[low]}"
                    if low in owner.enums:
                        return "enum", f"{owner.path()}.{owner.enums[low]}"
                if own is not None and parts[0].lower() == own["name"].lower():
                    for f in own["fields"]:
                        if f["name"].lower() == low and f["kind"] in ("Struct", "Enum"):
                            kind = "struct" if f["kind"] == "Struct" else "enum"
                            return kind, f"{own['package']}.{own['name']}.{f['name']}"
        if own is not None and low == own["name"].lower():
            return "class", f"{own['package']}.{own['name']}"
        target = self.info(word, own["package"] if own is not None else None)
        if target is not None:
            return "class", target.path()
        self._load_compiled()
        rank = self._load_rank()
        for owners, kind in ((self.struct_owners, "struct"), (self.enum_owners, "enum")):
            # Any loaded class's struct or enum of that name; several packages may
            # declare one, so take the first in load order (stock packages first).
            cands = [i for i in owners.get(low, [])
                     if self.visible is None or i.package.lower() in self.visible]
            cands.sort(key=lambda i: rank.get(i.package.lower(), len(rank)))
            for info in cands:
                table = info.structs if kind == "struct" else info.enums
                if low in table:
                    return kind, f"{info.path()}.{table[low]}"
        return None


_DEFAULTS: dict = {}


def default_context(compiled_root: Path | None = None, prefer_compiled: bool = False) -> Context:
    """The engine checkout and the local corpus, from ~/.sweeney/config.json, with
    other classes read from the compiled packages under `compiled_root` (default:
    the install). Cached per root.

    With `prefer_compiled`, a class that has a compiled package comes from it rather
    than from source -- what UCC itself loads for a dependency, and the right view
    for predicting a build (the engine source is a v3369 dump; the install's
    packages may be 3374)."""
    key = f"{compiled_root or ''}|{prefer_compiled}"
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    ctx = Context(compiled_root)
    if prefer_compiled:
        ctx._load_compiled()
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
    _DEFAULTS[key] = ctx
    return ctx
