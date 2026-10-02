"""Resolve a parsed declaration view into the shape of the compiled class.

decl.py reads one file; this settles what needs other classes: whether a type name
is a struct, an enum or a class, the full paths UCC stores (Engine.Actor,
Core.Object.Vector), the inner property of an array, which parent function a
function overrides, and what the class inherits (config name, within, flags).
"""

from __future__ import annotations

from .context import Context

# Class flags a subclass inherits from its parent (measured from compiled packages).
# Class flags passed down from the parent (measured from compiled packages). A class
# inherits only from its parent, so `notplaceable`, `noteditinlinenew` and
# `dontcollapsecategories` cut a flag off for the whole subtree (WeaponPickup's
# descendants stay unplaceable under a placeable Pickup). `config` means the class
# has config properties, its own or inherited; config(Name) alone doesn't set it.
INHERITED_CLASS_FLAGS = {"localized", "transient", "perobjectconfig", "safereplace",
                         "instanced", "cacheable", "placeable", "editinlinenew",
                         "collapsecategories", "config"}
CUT_BY = {"_notplaceable": "placeable", "_noteditinlinenew": "editinlinenew",
          "_dontcollapsecategories": "collapsecategories"}
# Flags the engine's C++ sets on a native class; UCC reads them from the binaries, so
# for a `native` class they come from its compiled package.
NATIVE_REGISTERED_FLAGS = {"cacheable", "safereplace"}


def _has_flag(fields: list[dict], wanted) -> bool:
    return any(set(wanted) & set(f.get("flags") or []) for f in fields
               if f["kind"].endswith("Property"))


def _has_localized(fields: list[dict]) -> bool:
    """A class is `localized` if it declares a localized property (or inherits one)."""
    return any("localized" in (f.get("flags") or []) for f in fields
               if f["kind"].endswith("Property"))
# Names UCC treats as probes: `ignores` on one clears a bit in the state's probe mask
# instead of adding a stub function. Measured by ignoring every non-final function of
# Object, Actor, Pawn, Controller, PlayerController and AIController in a probe
# state and reading back which got stubs; the rest got stubs.
PROBE_NAMES = {
    "aihearsound", "animend", "attach", "basechange", "beginplay", "beginstate", "bump",
    "created", "destroyed", "detach", "encroachedby", "encroachingon", "endedrotation",
    "endstate", "enemynotvisible", "falling", "gainedchild", "headvolumechange",
    "hearnoise", "hitwall", "landed", "lostchild", "mayfall", "modifyvelocity",
    "notifybump", "notifyheadvolumechange", "notifyhitwall", "notifylanded",
    "notifyphysicsvolumechange", "physicsvolumechange", "playertick", "postbeginplay",
    "postnetreceive", "posttouch", "prebeginplay", "prepareformove", "seemonster",
    "seeplayer", "specialhandling", "tick", "timer", "touch", "trigger", "untouch",
    "untrigger", "updateeyeheight", "zonechange",
}
# Flags an `ignores` stub copies from the function it intercepts.
STUB_COPIED_FLAGS = {"exec", "final", "latent", "preoperator", "iterator", "static"}

# Flags every compiled class carries.
COMPILED_CLASS_FLAGS = ["compiled", "parsed"]


def resolve_class(view: dict, package: str, ctx: Context, source: bytes | None = None) -> dict:
    name = view["name"]
    own = {"package": package, "name": name, "fields": view["fields"],
           "super": view.get("super")}
    path = f"{package}.{name}"

    sup_info = ctx.info(view.get("super")) if view.get("super") else None
    out = {"name": name}
    out["super"] = sup_info.path() if sup_info else (view.get("super") or None)

    flags = [f for f in view.get("class_flags", []) if not f.startswith("_")]
    if "_native" in view.get("class_flags", []):
        for f in ctx.compiled_flags(name):
            if f in NATIVE_REGISTERED_FLAGS and f not in flags:
                flags.append(f)
    parent = ctx.info(view.get("super")) if view.get("super") else None
    if parent is not None:
        for f in ctx.effective_flags(parent):
            if f in INHERITED_CLASS_FLAGS and f not in flags:
                flags.append(f)
    for cut, flag in CUT_BY.items():
        if cut in view.get("class_flags", []) and flag in flags:
            flags.remove(flag)
    if "localized" not in flags and _has_localized(view["fields"]):
        flags.append("localized")
    if "config" not in flags and _has_flag(view["fields"], ("config", "globalconfig")):
        flags.append("config")
    for f in COMPILED_CLASS_FLAGS:
        if f not in flags:
            flags.append(f)
    out["class_flags"] = flags
    out["config"] = view.get("config") or next(
        (a.config for a in ctx.ancestry(view.get("super")) if a.config), None) or "System"
    within = view.get("within") or next(
        (a.within for a in ctx.ancestry(view.get("super")) if a.within), None)
    w = ctx.info(within) if within else None
    out["within"] = w.path() if w else (within or "Core.Object")

    r = _Resolver(ctx, own, path, view.get("super"), source)
    out["fields"] = [r.field(f, path) for f in view["fields"]]
    return out


INHERITED_FUNCTION_FLAGS = ("net", "netreliable")


def _before(field: dict, pos) -> bool:
    """Whether a class-level field was declared before position `pos`."""
    return pos is None or field.get("_pos", 0) < pos


class _Resolver:
    def __init__(self, ctx: Context, own: dict, class_path: str, parent: str | None,
                 source: bytes | None):
        self.ctx, self.own, self.class_path, self.parent = ctx, own, class_path, parent
        self.source = source

    def type_of(self, word: str):
        return self.ctx.resolve_type(word, self.own["name"], self.own)

    def field(self, f: dict, owner_path: str, state: dict | None = None) -> dict:
        kind = f["kind"]
        out = {k: v for k, v in f.items() if not k.startswith("_") and k != "inner_type"}
        if kind == "Struct":
            out["fields"] = [self.field(g, f"{owner_path}.{f['name']}") for g in f.get("fields", [])]
            if f.get("super"):
                r = self.type_of(f["super"])
                out["super"] = r[1] if r else f["super"]
            return out
        if kind == "Enum":
            return out
        if kind == "Const":
            out["value"] = self._const_text(f)
            return out
        if kind == "State":
            spath = f"{owner_path}.{f['name']}"
            fields = []
            for g in f.get("fields", []):
                if g.get("_ignored"):
                    stub = self._ignore_stub(g["name"], spath, f.get("_pos"))
                    if stub:
                        fields.append(stub)
                    continue
                fields.append(self.field(g, spath, state=f))
            out["fields"] = fields
            out.pop("super", None)
            sup = self._state_super(f)
            if sup:
                out["super"] = sup
            return out
        if kind == "Function":
            fpath = f"{owner_path}.{f['name']}"
            out["fields"] = [self.field(g, fpath) for g in f.get("fields", [])]
            sup = self._function_super(f["name"], state)
            if sup:
                out["super"] = sup
                # An override keeps the replication flags of the function it overrides,
                # wherever up the chain they were set.
                fl = out.setdefault("flags", [])
                for x in self._replication_flags(f["name"]):
                    if x not in fl:
                        fl.append(x)
            return out
        return self._property(f, owner_path)

    def _property(self, f: dict, owner_path: str) -> dict:
        out = {k: v for k, v in f.items() if not k.startswith("_") and k != "inner_type"}
        kind = f["kind"]
        if kind == "UnresolvedProperty":
            r = self.type_of(f["type"])
            if r is None:
                out["kind"] = "ObjectProperty"
                out["type"] = f["type"]
            elif r[0] == "struct":
                out["kind"], out["type"] = "StructProperty", r[1]
                self._struct_ctor(out)
            elif r[0] == "enum":
                out["kind"] = "ByteProperty"
                out.pop("type", None)
                out["enum"] = r[1]
            else:
                out["kind"], out["type"] = "ObjectProperty", r[1]
                if self.ctx.has_flag(r[1], "instanced"):
                    fl = out.setdefault("flags", [])
                    fl += [x for x in ("editinline", "exportobject") if x not in fl]
        elif kind == "ByteProperty" and f.get("enum"):
            r = self.type_of(f["enum"])
            if r:
                out["enum"] = r[1]
        elif kind == "StructProperty" and f.get("type"):
            r = self.type_of(f["type"])
            if r:
                out["type"] = r[1]
                self._struct_ctor(out)
        elif kind == "ClassProperty":
            out["type"] = "Core.Class"
            m = self.ctx.info(f.get("meta_class") or "Object")
            out["meta_class"] = m.path() if m else f.get("meta_class")
        elif kind == "DelegateProperty":
            out["type"] = f"{self.class_path}.{f['type']}" if "." not in f["type"] else f["type"]
        elif kind == "ArrayProperty":
            out["inner"] = f"{owner_path}.{f['name']}.{f['name']}"
            inner = f.get("inner_type") or {}
            if "automated" in (out.get("flags") or []) and inner.get("kind") == "UnresolvedProperty":
                r = self.type_of(inner.get("type", ""))
                if r and r[0] == "class" and self.ctx.has_flag(r[1], "instanced"):
                    if "editinline" not in out["flags"]:
                        out["flags"].append("editinline")
        if isinstance(out.get("array_dim"), dict):
            out["array_dim"] = self._const_dim(out["array_dim"]["const"])
        return out

    def _struct_ctor(self, out: dict) -> None:
        """A struct holding a string, an array or such a struct needs constructing."""
        if self.ctx.struct_needs_ctor(out["type"], self.own):
            fl = out.setdefault("flags", [])
            if "needctorlink" not in fl:
                fl.append("needctorlink")

    def _const_text(self, f: dict) -> str:
        if self.source is None or "_const_span" not in f:
            return " " + " ".join(f.get("_const_tokens", []))
        a, b = f["_const_span"]
        return self.source[a:b]

    def _const_dim(self, toks: list[str]):
        if len(toks) != 1:
            return 1
        low = toks[0].lower()
        for g in self.own["fields"]:
            if g["kind"] == "Const" and g["name"].lower() == low:
                try:
                    return int(self._const_text(g).strip())
                except ValueError:
                    return 1
        for info in self.ctx.ancestry(self.parent):
            if low in info.consts:
                try:
                    return int(" ".join(info.consts[low]).strip())
                except ValueError:
                    return 1
        return 1

    def _replication_flags(self, name: str) -> list[str]:
        low = name.lower()
        for g in self.own["fields"]:
            if g["kind"] == "Function" and g["name"].lower() == low and "net" in (g.get("flags") or []):
                return [x for x in INHERITED_FUNCTION_FLAGS if x in g["flags"]]
        for info in self.ctx.ancestry(self.parent):
            fn = info.function_defs.get(low)
            if fn and "net" in (fn.get("flags") or []):
                return [x for x in INHERITED_FUNCTION_FLAGS if x in fn["flags"]]
        return []

    def _function_def(self, path: str) -> dict | None:
        """The function field at a path like Pkg.Class.Func (class-level only)."""
        parts = path.split(".")
        if len(parts) != 3:
            return None
        info = self.ctx.info(parts[1], parts[0])
        if info is None:
            return None
        f = info.function_defs.get(parts[2].lower())
        if f is not None and info.name.lower() == self.own["name"].lower():
            return None
        if f is not None and "net" not in (f.get("flags") or []) and not info.compiled:
            # A source-parsed parent's net flags come from its replication block,
            # applied to its own view; look it up through the replicated set.
            return f
        return f

    def _ignore_stub(self, name: str, state_path: str, state_pos=None) -> dict | None:
        """The empty function `ignores` inserts, or None for a probe name. It copies
        the intercepted function's parameters and some flags; that function -- the
        class's own, else the nearest ancestor's -- is its super."""
        low = name.lower()
        if low in PROBE_NAMES:
            return None
        found, found_path = None, None
        for g in self.own["fields"]:
            if g["kind"] == "Function" and g["name"].lower() == low and \
                    _before(g, state_pos):
                found, found_path = g, f"{self.class_path}.{g['name']}"
                break
        if found is None:
            for info in self.ctx.ancestry(self.parent):
                if low in info.function_defs:
                    found = info.function_defs[low]
                    found_path = f"{info.path()}.{found['name']}"
                    break
        flags = ["public"]
        parms = []
        if found is not None:
            flags += [x for x in (found.get("flags") or []) if x in STUB_COPIED_FLAGS]
            parms = [p for p in found.get("fields", []) if "parm" in (p.get("flags") or [])]
        fpath = f"{state_path}.{found['name'] if found else name}"
        stub = {"name": found["name"] if found else name, "kind": "Function", "flags": flags,
                "native": 0, "precedence": 0,
                "fields": [self._property(p, fpath) if p["kind"] == "UnresolvedProperty"
                           or "_word" in p else dict(p) for p in parms]}
        if found_path:
            stub["super"] = found_path
            stub["flags"] += [x for x in self._replication_flags(stub["name"]) if x not in flags]
        return stub

    def _function_super(self, name: str, state: dict | None) -> str | None:
        low = name.lower()
        if state is not None:
            # A state function overrides, in order: the function in the state it
            # extends; the same-named state's in the nearest ancestor class; the
            # nearest ancestor's class-level function. Never the class's own
            # class-level function (measured: Pawn.Dying.AnimEnd -> Actor.AnimEnd).
            if state.get("super"):
                ext = state["super"].lower()
                for g in self.own["fields"]:
                    if g["kind"] == "State" and g["name"].lower() == ext:
                        for h in g.get("fields", []):
                            if h["kind"] == "Function" and h["name"].lower() == low:
                                return f"{self.class_path}.{g['name']}.{h['name']}"
                r = self.ctx.state_function(self.parent, state["super"], name)
                if r:
                    return r
            r = self.ctx.state_function(self.parent, state["name"], name)
            if r:
                return r
            # The class's own function, if UCC had parsed it before the state
            # (Console.Typing.KeyEvent -> Console.KeyEvent; but Pawn's AnimEnd comes
            # after state Dying, so Pawn.Dying.AnimEnd -> Actor.AnimEnd).
            for g in self.own["fields"]:
                if g["kind"] == "Function" and g["name"].lower() == low and \
                        _before(g, state.get("_pos")):
                    return f"{self.class_path}.{g['name']}"
        for info in self.ctx.ancestry(self.parent):
            if low in info.functions:
                return f"{info.path()}.{info.functions[low]}"
        return None

    def _state_super(self, f: dict) -> str | None:
        if f.get("super"):
            for g in self.own["fields"]:
                if g["kind"] == "State" and g["name"].lower() == f["super"].lower():
                    return f"{self.class_path}.{g['name']}"
            for info in self.ctx.ancestry(self.parent):
                if f["super"].lower() in info.states:
                    return f"{info.path()}.{f['super']}"
        for info in self.ctx.ancestry(self.parent):
            if f["name"].lower() in info.states:
                return f"{info.path()}.{f['name']}"
        return None


# ---------------------------------------------------------------- declaration errors

def deferred_errors(view: dict, package: str, ctx: Context) -> list[tuple[int, int, str]]:
    """Declaration errors that need other classes, as (token position, line, message).
    A missing superclass is fatal before parsing starts, so it sorts first (-1)."""
    out: list[tuple[int, int, str]] = []
    name = view.get("name") or ""
    sup = view.get("super")
    if sup and ctx.info(sup) is None:
        out.append((-1, 0, f"Superclass {sup} of class {name} not found"))
        return out
    own = {"package": package, "name": name, "fields": view["fields"], "super": sup}

    def check_type(t: dict | None, thing: str = "") -> None:
        if not t:
            return
        if t.get("kind") == "UnresolvedProperty":
            if ctx.resolve_type(t["type"], name, own) is None:
                msg = t.get("_bad_def") or f"Unrecognized type '{t['type']}'"
                out.append((t.get("_tpos", 0), t.get("_line", 0), msg))
        elif t.get("kind") == "ClassProperty" and t.get("meta_class") and "_meta_tpos" in t:
            r = ctx.resolve_type(t["meta_class"], name, own)
            if r is None or r[0] != "class":
                out.append((t["_meta_tpos"], t["_meta_line"],
                            f"'class': Limitor '{t['meta_class']}' is not a class name"))
        elif t.get("kind") == "ArrayProperty":
            check_type(t.get("inner_type"))

    def check_prop(f: dict) -> None:
        check_type(f.get("_type"))
        dim = f.get("array_dim")
        if isinstance(dim, dict):
            cname = dim["const"][0].lower()
            known = any(g["kind"] == "Const" and g["name"].lower() == cname for g in view["fields"]) \
                or any(cname in i.consts for i in ctx.ancestry(sup))
            if not known:
                out.append((f.get("_tpos", 0), dim.get("_line", f.get("_line", 0)),
                            f"{dim.get('_thing', 'Variable declaration')}: Illegal array size 1"))

    for f in view["fields"]:
        kind = f["kind"]
        if kind.endswith("Property"):
            check_prop(f)
        elif kind == "Struct":
            for m in f.get("fields", []):
                if m["kind"].endswith("Property"):
                    check_prop(m)
            if f.get("super") and ctx.resolve_type(f["super"], name, own) is None:
                out.append((f.get("_tpos", 0), 0, "Cast of NULL to Struct failed"))
        elif kind == "Function":
            _function_errors(f, None, own, view, package, ctx, out, check_prop)
        elif kind == "State":
            _state_errors(f, own, view, package, ctx, out, check_prop)

    names = {g["name"].lower() for g in view["fields"]}
    for rname, line, pos in view.get("_replicated_at", []):
        low = rname.lower()
        if low in names:
            continue
        if any(low in i.functions or _has_var(i, low) for i in ctx.ancestry(sup)):
            # Measured: only the class's own vars and functions can be replicated here.
            out.append((pos, line, f"Bad variable or function '{rname}' in replication definition"))
        else:
            out.append((pos, line, f"Unrecognized variable '{rname}' name in replication definition"))
    return out


def _has_var(info, low: str) -> bool:
    return low in getattr(info, "vars", set())


def _parent_function(low: str, sup: str | None, ctx: Context):
    for info in ctx.ancestry(sup):
        f = info.function_defs.get(low)
        if f is not None:
            return info, f
    return None, None


def _parms(f: dict) -> list[dict]:
    return [p for p in f.get("fields", []) if "parm" in (p.get("flags") or [])
            and "return" not in (p.get("flags") or [])]


def _returns(f: dict) -> dict | None:
    for p in f.get("fields", []):
        if "return" in (p.get("flags") or []):
            return p
    return None


def _function_errors(f, state, own, view, package, ctx, out, check_prop) -> None:
    pos, line = f.get("_tpos", 0), f.get("_line", 0)
    for p in f.get("fields", []):
        if p["kind"].endswith("Property"):
            check_prop(p)
    if f.get("_ret"):
        r = f["_ret"]
        if r.get("kind") == "UnresolvedProperty" and ctx.resolve_type(r["type"], own["name"], own) is None:
            out.append((r.get("_tpos", pos), r.get("_line", line), r.get("_bad_def", "Bad function definition")))
            return
    if f.get("_conflict_state"):
        out.append((pos, line, f"'{f['name']}' conflicts with 'Function {package}.{own['name']}.{f['_conflict_state']}.{f['name']}'"))
        return
    if f.get("_conflict"):
        out.append((pos, line, f"'{f['name']}' conflicts with 'Function {package}.{own['name']}.{f['name']}'"))
        return
    if "operator" in (f.get("flags") or []) or "delegate" in (f.get("flags") or []):
        return
    if state is not None:
        return
    pinfo, pf = _parent_function(f["name"].lower(), own.get("super"), ctx)
    if pf is None:
        return
    pflags = set(pf.get("flags") or [])
    flags = set(f.get("flags") or [])
    word = f.get("_kind_word") or "function"
    if ("static" in pflags) != ("static" in flags):
        out.append((pos, line, f"Function '{f['name']}' specifiers differ from original"))
        return
    differs = "final" in pflags
    if len(_parms(pf)) != len(_parms(f)):
        differs = True
    if (_returns(pf) is None) != (_returns(f) is None):
        differs = True
    if differs:
        out.append((pos, line, f"Redefinition of '{word} {f['name']}' differs from original in {pinfo.name}"))


def _state_errors(st, own, view, package, ctx, out, check_prop) -> None:
    sup = own.get("super")
    if st.get("super"):
        ext = st["super"].lower()
        in_own = any(g["kind"] == "State" and g["name"].lower() == ext for g in view["fields"])
        in_parents = any(ext in i.states for i in ctx.ancestry(sup))
        spos, sline = st.get("_super_tpos", st.get("_tpos", 0)), st.get("_super_line", 0)
        if any(st["name"].lower() in i.states for i in ctx.ancestry(sup)):
            out.append((spos, sline, f"'Extends' not allowed here: state '{st['name']}' "
                                     "overrides version in parent class"))
        elif not in_own and not in_parents:
            out.append((spos, sline, f"'extends': Parent state '{st['super']}' not found"))
    for g in st.get("fields", []):
        if g.get("_ignored"):
            low = g["name"].lower()
            if low in PROBE_NAMES:
                continue
            target = None
            for h in view["fields"]:
                if h["kind"] == "Function" and h["name"].lower() == low and \
                        h.get("_pos", 0) < st.get("_pos", 1 << 30):
                    target = h
            if target is None:
                _, target = _parent_function(low, sup, ctx)
            if target is None:
                out.append((g.get("_tpos", 0), g.get("_line", 0),
                            f"'Ignores': '{g['name']}' is not a function"))
            elif "final" in (target.get("flags") or []):
                out.append((g.get("_tpos", 0), g.get("_line", 0),
                            f"'{g['name']}': Cannot ignore final functions"))
        elif g["kind"] == "Function":
            _function_errors(g, st, own, view, package, ctx, out, check_prop)
