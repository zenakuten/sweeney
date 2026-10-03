"""Function bodies: UCC's second pass -- statements, expressions and their types.

UCC compiles a body in one type-driven pass: every expression is compiled against
the type its context requires, and most messages (Type mismatch in '=', Call to 'F':
bad or missing parameter 2, '?': Expression has no effect) come out of that. This
module reproduces those semantics, measured against UCC:

- An expression is compiled against a required type. Operators are found by the
  symbol they're written with, among every operator function visible from the
  class; the overload is chosen by conversion cost, and a lower precedence number
  binds tighter.
- A mismatch converts automatically only where UCC's conversion table allows it
  (byte/int/float widening, float to int truncation); `coerce` parameters accept any
  conversion; out parameters require a variable of exactly the type.
- A statement must be an assignment, a call, or an expression with a side effect.

Anything this doesn't model raises Unsupported, which makes the prediction "don't
know" -- never a guess. CompileError carries UCC's message and line.
"""

from __future__ import annotations

import dataclasses

from .lexer import Token, IDENT, INT, FLOAT, NAME, STRING, SYMBOL, RAW, OBJECT

MAXINT = 1 << 31
MAX_PRECEDENCE = 255


class CompileError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.message, self.line = message, line


class Unsupported(Exception):
    pass


# ---------------------------------------------------------------- types

@dataclasses.dataclass(frozen=True)
class T:
    kind: str                       # none byte int bool float object name string struct delegate pointer
    enum: str | None = None         # enum path, for byte
    cls: str | None = None          # class path, for object (None: the None literal)
    meta: str | None = None         # metaclass path, when cls is Core.Class
    struct: str | None = None       # struct path
    func: object = None             # delegate signature
    dim: int = 1                    # 1 scalar, 0 dynamic array, >1 static array
    flags: frozenset = frozenset()  # out (an l-value / requires one), const, coerce, optional, skip
    const: bool = False             # a constant token
    value: object = None

    def with_(self, **kw) -> "T":
        return dataclasses.replace(self, **kw)

    def add(self, *flags) -> "T":
        return self.with_(flags=self.flags | frozenset(flags))

    def drop(self, *flags) -> "T":
        return self.with_(flags=self.flags - frozenset(flags))

    def struct_name(self) -> str:
        return (self.struct or "").split(".")[-1].lower()

    def conv_kind(self) -> str:
        """The kind UCC's conversion table indexes by: vector and rotator are their own."""
        if self.kind == "struct" and self.struct_name() in ("vector", "rotator"):
            return self.struct_name()
        return self.kind


NONE = T("none")
CLASS_PATH = "Core.Class"
OBJECT_PATH = "Core.Object"

# GetConversion: (dest, src) -> (cast exists, autoconvert, truncates). From UCC's table.
_AC, _TAC, _PLAIN = (True, True, False), (True, True, True), (True, False, False)
CONVERSIONS = {
    ("byte", "int"): _TAC, ("byte", "bool"): _PLAIN, ("byte", "float"): _TAC, ("byte", "string"): _PLAIN,
    ("int", "byte"): _AC, ("int", "bool"): _PLAIN, ("int", "float"): _TAC, ("int", "string"): _PLAIN,
    ("bool", "byte"): _PLAIN, ("bool", "int"): _PLAIN, ("bool", "float"): _PLAIN,
    ("bool", "object"): _PLAIN, ("bool", "name"): _PLAIN, ("bool", "vector"): _PLAIN,
    ("bool", "rotator"): _PLAIN, ("bool", "string"): _PLAIN,
    ("float", "byte"): _AC, ("float", "int"): _AC, ("float", "bool"): _PLAIN, ("float", "string"): _PLAIN,
    ("vector", "rotator"): _PLAIN, ("vector", "string"): _PLAIN,
    ("rotator", "vector"): _PLAIN, ("rotator", "string"): _PLAIN,
    ("string", "byte"): _PLAIN, ("string", "int"): _PLAIN, ("string", "bool"): _PLAIN,
    ("string", "float"): _PLAIN, ("string", "object"): _PLAIN, ("string", "name"): _PLAIN,
    ("string", "vector"): _PLAIN, ("string", "rotator"): _PLAIN,
}


def conversion(dest: T, src: T):
    return CONVERSIONS.get((dest.conv_kind(), src.conv_kind()))


class TypeSystem:
    """Class and struct relations, through the context."""

    def __init__(self, ctx, own_name: str, own_package: str, own_super: str | None, own_structs):
        self.ctx, self.own_name, self.own_package = ctx, own_name, own_package
        self.own_super, self.own_structs = own_super, own_structs

    def class_chain(self, path: str | None) -> list[str]:
        """[class name, parent, ...] lowercased; own class included."""
        if not path:
            return []
        name = path.split(".")[-1]
        out = []
        if name.lower() == self.own_name.lower():
            out.append(name.lower())
            name = self.own_super
        for info in self.ctx.ancestry(name):
            out.append(info.name.lower())
        return out

    def is_child(self, child: str | None, parent: str | None) -> bool:
        if parent is None or child is None:
            return False
        pname = parent.split(".")[-1].lower()
        if pname == "object":
            return True
        chain = self.class_chain(child)
        if not chain:
            raise Unsupported(f"class {child}")
        if pname in chain:
            return True
        last = self.ctx.info(chain[-1]) if chain[-1] != self.own_name.lower() else None
        if last is not None and last.super is None and last.name.lower() != "object":
            raise Unsupported(f"intrinsic hierarchy of {child}")
        return False

    def struct_is_child(self, child: str | None, parent: str | None) -> bool:
        if child is None or parent is None:
            return False
        if _same_struct(child, parent):
            return True
        sdef = self.struct_def(child)
        while sdef is not None and sdef.get("super"):
            if _same_struct(sdef["super"], parent):
                return True
            sdef = self.struct_def(sdef["super"])
        return False

    def struct_def(self, path: str) -> dict | None:
        parts = path.split(".")
        sname = parts[-1].lower()
        if (len(parts) < 2 or parts[-2].lower() == self.own_name.lower()) and sname in self.own_structs:
            return self.own_structs[sname]
        if len(parts) >= 2:
            info = self.ctx.info(parts[-2], parts[0] if len(parts) == 3 else None)
            if info is not None:
                return info.struct_defs.get(sname)
        owner = self.ctx.struct_owner.get(sname)
        if owner:
            return self.ctx.classes[owner].struct_defs.get(sname)
        return None

    # -------------------------------------------------------- UCC's matching

    def matches(self, dest: T, src: T, identity: bool) -> bool:
        """FPropertyBase::MatchesType."""
        if "out" in dest.flags:
            if "const" in src.flags or "out" not in src.flags:
                return False
            if dest.kind != "struct":
                identity = True
        if dest.kind == "none" and (src.kind == "none" or not identity):
            return True
        if dest.kind != src.kind:
            return False
        if dest.dim != src.dim:
            return False
        if dest.kind == "byte":
            # Enums are distinct by their declaring class: a renamed class's ELinkColor
            # isn't LinkAttachment.ELinkColor.
            return _same_path(dest.enum, src.enum) or (dest.enum is None and not identity)
        if dest.kind == "object":
            if identity:
                return _same(dest.cls, src.cls) and _same(dest.meta, src.meta)
            if src.cls is None:
                return True
            if self.is_child(src.cls, dest.cls):
                if not _same(dest.cls, CLASS_PATH) or self.is_child(src.meta or OBJECT_PATH, dest.meta or OBJECT_PATH):
                    return True
            return False
        if dest.kind == "struct":
            if identity:
                return _same_struct(dest.struct, src.struct)
            return self.struct_is_child(src.struct, dest.struct)
        if dest.kind == "delegate":
            return True
        return True

    def cost(self, dest: T, src: T) -> int:
        """FScriptCompiler::ConversionCost."""
        conv = conversion(dest, src)
        if self.matches(dest, src, True):
            return 0
        if "out" in dest.flags:
            return MAXINT
        if self.matches(dest, src, False):
            result = 1
            if src.kind == "object" and src.cls is not None:
                chain = self.class_chain(src.cls)
                target = (dest.cls or OBJECT_PATH).split(".")[-1].lower()
                for c in chain:
                    if c == target:
                        break
                    result += 1
            return result
        if dest.dim != 1 or src.dim != 1:
            return MAXINT
        if dest.kind == "byte" and dest.enum is not None:
            return MAXINT
        if dest.kind == "object" and dest.cls is not None:
            return MAXINT
        if ("coerce" in dest.flags and conv is None) or ("coerce" not in dest.flags and not (conv and conv[1])):
            return MAXINT
        if conv and conv[2]:
            return 104
        if src.kind in ("int", "pointer", "byte") and dest.kind == "float":
            return 103
        return 101


def _same_path(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a.lower() == b.lower()


def _same_struct(a, b) -> bool:
    """Structs are the same when owner and name match: Seed_X.S is not X.S."""
    if a is None or b is None:
        return a is b
    pa, pb = a.lower().split("."), b.lower().split(".")
    if len(pa) >= 2 and len(pb) >= 2:
        return pa[-2:] == pb[-2:]
    return pa[-1] == pb[-1]


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a.split(".")[-1].lower() == b.split(".")[-1].lower()


@dataclasses.dataclass
class _OwnClass:
    name: str
    _path: str

    def path(self) -> str:
        return self._path


# ---------------------------------------------------------------- fields

@dataclasses.dataclass
class Func:
    name: str
    friendly: str
    params: list            # [(name, T)]
    ret: T | None
    flags: set
    owner: str              # class name
    precedence: int = 0

    @property
    def numparms(self) -> int:
        return len(self.params) + (1 if self.ret is not None else 0)


@dataclasses.dataclass
class Var:
    name: str
    t: T
    owner: str              # class name, or "" for locals/params
    local: bool = False
    flags: set = dataclasses.field(default_factory=set)


class Scope:
    """Name lookup for one function body: locals and params, the state chain, the
    class and its ancestors, then the `within` class -- FindField's order."""

    def __init__(self, ts: TypeSystem, ctx, own_view: dict, func_field: dict | None,
                 state_field: dict | None):
        self.ts, self.ctx, self.view = ts, ctx, own_view
        self.own = own_view["name"]
        self.package = ts.own_package
        self.func = func_field
        self.state = state_field
        self.locals: dict[str, Var] = {}
        self.body_consts: dict[str, dict] = {}
        if func_field is not None:
            for f in func_field.get("fields", []):
                fl = set(f.get("flags") or [])
                if "return" in fl:
                    continue
                t = self.field_type(f, self.own)
                if "parm" in fl:
                    t = t.add("out")          # a parameter is an l-value in the body
                else:
                    t = t.add("out")
                if "const" in fl:
                    t = t.add("const")
                self.locals[f["name"].lower()] = Var(f["name"], t, "", local=True, flags=fl)

    # -------------------------------------------------------- types of fields

    def field_type(self, f: dict, owner: str) -> T:
        kind = f["kind"]
        dim = f.get("array_dim", 1)
        if isinstance(dim, dict):
            dim = self.const_int(dim.get("const", ["1"])[0], owner)
        fl = frozenset(x for x in ("const", "coerce", "optional", "skip") if x in (f.get("flags") or []))
        if "out" in (f.get("flags") or []) and "return" not in (f.get("flags") or []):
            fl = fl | {"out"}
        base = self.kind_type(f, owner)
        if kind == "ArrayProperty":
            return base.with_(flags=fl)                 # dim 0: a dynamic array
        return base.with_(dim=dim if isinstance(dim, int) else 2, flags=fl)

    def kind_type(self, f: dict, owner: str) -> T:
        kind = f["kind"]
        if kind == "ByteProperty":
            return T("byte", enum=self.resolve_path(f.get("enum"), owner, "enum"))
        if kind == "IntProperty":
            return T("int")
        if kind == "BoolProperty":
            return T("bool")
        if kind == "FloatProperty":
            return T("float")
        if kind == "NameProperty":
            return T("name")
        if kind == "StrProperty":
            return T("string")
        if kind == "PointerProperty":
            return T("pointer")
        if kind == "ObjectProperty":
            return T("object", cls=self.resolve_path(f.get("type"), owner, "class") or OBJECT_PATH)
        if kind == "ClassProperty":
            return T("object", cls=CLASS_PATH,
                     meta=self.resolve_path(f.get("meta_class"), owner, "class") or OBJECT_PATH)
        if kind == "StructProperty":
            return T("struct", struct=self.resolve_path(f.get("type"), owner, "struct"))
        if kind == "DelegateProperty":
            fn = f.get("type") or ""
            if "." not in fn:
                fn = f"{owner}.{fn}"          # the declaring class's delegate
            return T("delegate", func=fn)
        if kind == "ArrayProperty":
            inner = f.get("_inner_field") or f.get("_inner") or f.get("inner_field")
            if inner is None and f.get("inner_type"):
                it = f["inner_type"]
                inner = {"kind": it["kind"], "type": it.get("type"), "enum": it.get("enum"),
                         "meta_class": it.get("meta_class"), "name": f["name"]}
            if inner is None:
                raise Unsupported("array element type")
            return self.kind_type(inner, owner).with_(dim=0)
        if kind == "UnresolvedProperty":
            r = self.ctx.resolve_type(f.get("type", ""), owner, self._own_for(owner))
            if r is None:
                raise Unsupported(f"type {f.get('type')}")
            if r[0] == "struct":
                return T("struct", struct=r[1])
            if r[0] == "enum":
                return T("byte", enum=r[1])
            return T("object", cls=r[1])
        raise Unsupported(kind)

    def _own_for(self, owner: str):
        if owner.lower() == self.own.lower():
            return {"package": self.package, "name": self.own, "fields": self.view["fields"],
                    "super": self.view.get("super")}
        return None

    def resolve_path(self, word: str | None, owner: str, want: str) -> str | None:
        if not word:
            return None
        if word.count(".") >= 1 and want == "class":
            return word
        if word.count(".") >= 2:
            return word
        r = self.ctx.resolve_type(word, owner, self._own_for(owner))
        if r is None:
            if want == "class" and word.lower() in ("object", "class"):
                return f"Core.{word}"
            raise Unsupported(f"type {word}")
        return r[1]

    def const_int(self, name: str, owner: str) -> int:
        try:
            return int(name)
        except ValueError:
            pass
        for f in self.view["fields"]:
            if f["kind"] == "Const" and f["name"].lower() == name.lower():
                try:
                    return int(str(f.get("value", "")).strip())
                except ValueError:
                    raise Unsupported("const array size")
        # The const is the declaring class's: its own, or one it inherits.
        chains = [self.ctx.ancestry(self.view.get("super"))]
        if owner and owner.split(".")[-1].lower() != self.own.lower():
            chains.insert(0, self.ctx.ancestry(owner.split(".")[-1]))
        for chain in chains:
            for info in chain:
                if name.lower() in info.consts:
                    try:
                        return int(" ".join(info.consts[name.lower()]).strip())
                    except ValueError:
                        raise Unsupported("const array size")
        raise Unsupported(f"const array size {name} in {owner}")

    # -------------------------------------------------------- lookup

    def function(self, f: dict, owner: str) -> Func:
        params, ret = [], None
        for p in f.get("fields", []):
            fl = p.get("flags") or []
            if "return" in fl:
                ret = self.field_type(p, owner).drop("out")
            elif "parm" in fl:
                params.append((p["name"], self.field_type(p, owner)))
        return Func(f["name"], f.get("_friendly") or f["name"], params, ret,
                    set(f.get("flags") or []), owner, f.get("precedence") or 0)

    def class_members(self, cls: str | None):
        """(owner name, fields) for the class and each ancestor, own class first."""
        if cls is None:
            return
        name = cls.split(".")[-1]
        if name.lower() == self.own.lower():
            yield self.own, self.view["fields"]
            name = self.view.get("super")
            if not name:
                return
        last = None
        for info in self.ctx.ancestry(name):
            last = info
            yield from self._members_of(info)
        if last is not None and last.name.lower() != "object" and last.super is None:
            # An intrinsic class (Class, Font, ...): its parent isn't recorded, but every
            # class is an Object.
            obj = self.ctx.info("Object", "Core")
            if obj is not None:
                yield from self._members_of(obj)

    def _members_of(self, info):
        if True:
            yield info.name, list(info.var_defs.values()) + list(info.function_defs.values()) \
                + [{"kind": "Const", "name": k, "_tokens": v} for k, v in info.consts.items()] \
                + [{"kind": "Enum", "name": info.enums[k], "values": v, "_path": f"{info.path()}.{info.enums[k]}"}
                   for k, v in info.enum_values.items()] \
                + [{"kind": "Struct", "name": info.structs[k], "_path": f"{info.path()}.{info.structs[k]}"}
                   for k in info.struct_defs]

    def find(self, name: str, field_class: str | None = None, cls: str | None = None,
             in_function: bool = True):
        """FindField: a ('var'|'func'|'const'|'enum'|'struct', object) or None."""
        low = name.lower()
        if cls is None and in_function and field_class in (None, "var") and low in self.locals:
            return "var", self.locals[low]
        if cls is None and in_function and field_class is None and low in self.body_consts:
            return "const", (self.body_consts[low], self.own)
        if cls is None and self.state is not None and field_class in (None, "func"):
            fn = self._state_function(low)
            if fn is not None:
                return "func", fn
        for owner, fields in self.class_members(cls or self.own):
            for f in fields:
                if f["name"].lower() != low:
                    continue
                k = f["kind"]
                if k.endswith("Property") and field_class in (None, "var"):
                    if f.get("flags") and "parm" in f["flags"]:
                        continue
                    t = self.field_type(f, owner).add("out")
                    return "var", Var(f["name"], t, owner, flags=set(f.get("flags") or []))
                if k == "Function" and field_class in (None, "func"):
                    return "func", self.function(f, owner)
                if k == "Const" and field_class is None:
                    return "const", (f, owner)
                if k == "Enum" and field_class is None:
                    path = f.get("_path") or f"{self.package}.{owner}.{f['name']}"
                    return "enum", (path, f.get("values") or [])
        return None

    def _state_function(self, low: str):
        """A function as seen from inside the current state: the state, the state it
        extends, the same-named state up the hierarchy."""
        st = self.state
        seen = set()
        while st is not None and st["name"].lower() not in seen:
            seen.add(st["name"].lower())
            for g in st.get("fields", []):
                if g["kind"] == "Function" and g["name"].lower() == low and not g.get("_ignored"):
                    return self.function(g, self.own)
            ext = st.get("super")
            if not ext:
                break
            ext = ext.split(".")[-1]          # resolved views hold a path
            nxt = next((g for g in self.view["fields"] if g["kind"] == "State"
                        and g["name"].lower() == ext.lower()), None)
            if nxt is None:
                return self._parent_state_function(ext.lower(), low)
            st = nxt
        return self._parent_state_function(self.state["name"].lower(), low)

    def _parent_state_function(self, sname: str, low: str):
        for info in self.ctx.ancestry(self.view.get("super")):
            st = info.state_function_defs.get(sname)
            if st and low in st:
                return self.function(st[low], info.name)
            ext = info.state_ext.get(sname)
            if ext:
                sdefs = info.state_function_defs.get(ext.lower())
                if sdefs and low in sdefs:
                    return self.function(sdefs[low], info.name)
        return None

    def operators(self, symbol: str, pre: bool) -> list[Func]:
        """Every operator written `symbol` visible here, of the right kind."""
        out = []
        for owner, fields in self.class_members(self.own):
            for f in fields:
                if f["kind"] != "Function":
                    continue
                fl = set(f.get("flags") or [])
                if "operator" not in fl:
                    continue
                if (f.get("_friendly") or "").lower() != symbol.lower():
                    continue
                if ("preoperator" in fl) != pre:
                    continue
                out.append(self.function(f, owner))
        return out

    def struct_members(self, path: str) -> list[dict] | None:
        sdef = self.ts.struct_def(path)
        if sdef is None:
            return None
        members = [m for m in sdef.get("fields", []) if m["kind"].endswith("Property")]
        if sdef.get("super"):
            parent = self.struct_members(sdef["super"])
            if parent is None:
                return None
            members = parent + members
        return members


# ---------------------------------------------------------------- the compiler

NONE_CONTEXT_ASSERT = ("Assertion failed: Token.PropertyClass != NULL "
                       "[File:C:\\GameDev\\ut2004\\Editor\\Src\\UnScrCom.cpp] [Line: 2930]")

# Statements that CheckAllow(ALLOW_Cmd), and the name UCC gives them when refused.
CMD_WORDS = {"switch": "'Switch'", "if": "'If'", "while": "'While'", "do": "'Do'",
             "for": "'For'", "foreach": "'ForEach'", "assert": "'Assert'"}


class Body:
    """Compiles one function body (or state code) from its tokens."""

    def __init__(self, tokens: list[Token], scope: Scope, static: bool, kind: str,
                 eof_line: int, close: Token | None = None):
        self.t = tokens
        self.close = close            # the '}' after the body: what UCC reads next at its end
        self.i = 0
        self.s = scope
        self.ts = scope.ts
        self.static = static          # in a static function: no instance access
        self.kind = kind              # "function" or "state"
        self.eof_line = eof_line
        self.got_affector = False
        self.nests: list[dict] = []
        self.vardecl_ok = True        # no command yet: locals still allowed
        self.last_field: str | None = None
        self.labels: set[str] = set()
        self.gotos: list[tuple[str, int]] = []

    # -------------------------------------------------------- tokens

    def peek(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def next(self) -> Token | None:
        tok = self.peek()
        if tok is not None:
            self.i += 1
        return tok

    def at(self, text: str, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok is not None and tok.kind in (IDENT, SYMBOL) and tok.text.lower() == text

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def line(self) -> int:
        """InputLine after an unget: where the next token starts (or the last line)."""
        tok = self.peek()
        if tok is not None:
            return tok.line
        return self.t[-1].line if self.t else self.eof_line

    def last_line(self) -> int:
        return self.t[self.i - 1].line if self.i else self.line()

    def error(self, msg: str, line: int | None = None) -> CompileError:
        return CompileError(msg, line if line is not None else self.line())

    def require(self, sym: str, tag: str) -> None:
        """RequireSymbol: Missing 'x' in tag."""
        if not self.accept(sym):
            raise self.error(f"Missing '{sym}' in {tag}")

    # -------------------------------------------------------- statements

    def compile_condition(self) -> None:
        """A replication condition: the tokens after '(' through the closing ')'."""
        self.expr_required(T("bool"), "Replication condition")
        self.require(")", "Replication condition")

    def register_consts(self) -> None:
        """`const X = value;` inside a body is a constant of the function."""
        for k in range(len(self.t) - 3):
            a, b, c = self.t[k], self.t[k + 1], self.t[k + 2]
            if a.kind == IDENT and a.text.lower() == "const" and b.kind == IDENT and \
                    c.kind == SYMBOL and c.text == "=":
                vals = []
                j = k + 3
                while j < len(self.t) and not (self.t[j].kind == SYMBOL and self.t[j].text == ";"):
                    vals.append(self.t[j].text if self.t[j].kind != STRING
                                else '"' + self.t[j].value + '"')
                    j += 1
                self.s.body_consts[b.text.lower()] = {"kind": "Const", "name": b.text,
                                                      "_tokens": vals}

    def compile(self) -> None:
        """The body between the braces (self.t excludes them)."""
        while self.peek() is not None:
            self.statement()
        if self.nests:
            raise Unsupported("unclosed block")
        for label, line in self.gotos:
            if label.lower() not in self.labels:
                # Checked when the block ends: reported at its closing brace.
                raise CompileError(f"Label '{label}' not found in this block of code",
                                   self.close.line if self.close is not None else line)

    def statement(self) -> None:
        tok = self.next()
        need_semicolon = True
        w = tok.text.lower() if tok.kind == IDENT else None
        if tok.kind == SYMBOL and tok.text == "#":
            raise Unsupported("directive in a body")
        if w == "local" and not (self.kind == "function" and self.vardecl_ok and
                                 all(n["kind"] == "block" for n in self.nests)):
            # Locals come first: the first command clears ALLOW_VarDecl, and no nest
            # (if, loop, switch) or state code grants it.
            raise self.error("'Local' is not allowed here")
        if w in CMD_WORDS:
            self._cmd(CMD_WORDS[w])
        if w in ("local", "const"):
            # Declared in the first pass (a const may sit in a body); skip it.
            while self.peek() is not None and not self.at(";"):
                self.next()
        elif w in ("if", "while"):
            self.require("(", f"'{tok.text.capitalize() if w == 'if' else 'While'}'")
            self.expr_required(T("bool"), "'If'" if w == "if" else "'While'")
            self.require(")", "'If'" if w == "if" else "'While'")
            need_semicolon = False
            self.block_or_statement(w)
        elif w == "do":
            need_semicolon = False
            self.block_or_statement("do")
        elif w == "for":
            self.require("(", "'For'")
            self.affector()
            self.require(";", "'For'")
            self.expr_required(T("bool"), "'For'")
            self.require(";", "'For'")
            self.affector()
            self.require(")", "'For'")
            need_semicolon = False
            self.block_or_statement("for")
        elif w == "foreach":
            self.foreach_expr()
            need_semicolon = False
            self.block_or_statement("foreach")
        elif w == "switch":
            r = self.expr(NONE, "'Switch'")
            if r.dim != 1:
                raise self.error("Can't switch on arrays")
            if not self.accept("{"):
                raise self.error("Missing '{' in 'Switch'")
            self.nests.append({"kind": "switch", "type": r.drop("out")})
            need_semicolon = False
        elif w == "case":
            sw = self._find_nest("switch")
            if sw is None:
                raise self.error("'Class' is not allowed here")
            self.expr_required(sw["type"], "'Case'")
            self.require(":", "'Case'")
            sw["cased"] = True
            need_semicolon = False
        elif w == "default" and self._find_nest("switch") is not None and self.at(":"):
            self.next()
            self._find_nest("switch")["cased"] = True
            need_semicolon = False
        elif w == "return":
            if self.kind != "function":
                raise self.error("'Return' is not allowed here")
            ret = self._return_type()
            if ret is not None:
                self.expr_required(ret.drop("out"), "'Return'")
        elif w in ("break", "continue"):
            if w not in self._allowed():
                raise self.error(f"'{w.capitalize()}' is not allowed here")
        elif w == "goto":
            if self.kind == "state":
                self.expr_required(T("name"), "'Goto'")
            else:
                lab = self.next()
                if lab is None:
                    raise self.error("Goto: Missing label")
                self.gotos.append((lab.text, lab.line))
        elif w == "assert":
            self.expr_required(T("bool"), "'Assert'")
        elif w == "stop":
            if self.kind != "state":
                raise self.error("'Stop' is not allowed here")
        elif tok.kind == SYMBOL and tok.text == "{":
            self.nests.append({"kind": "block"})
            need_semicolon = False
        elif tok.kind == SYMBOL and tok.text == "}":
            if not self.nests:
                raise Unsupported("unbalanced '}'")
            self._pop()
            need_semicolon = False
        elif tok.kind == SYMBOL and tok.text == ";":
            need_semicolon = False
        elif tok.kind == IDENT and self.at(":"):
            self.next()
            self.labels.add(tok.text.lower())
            need_semicolon = False
        else:
            self._cmd("Expression")
            self.i -= 1
            self.affector()
        if need_semicolon and not self.accept(";"):
            nxt = self.next()
            if nxt is not None:
                line = nxt.line
                if nxt.kind == IDENT and nxt.text.lower() == "else" and self.peek() is not None:
                    line = self.peek().line   # UCC has looked past 'else' (for 'else if')
                raise CompileError(f"Missing ';' before '{nxt.text}'", line)
            if self.close is not None:
                raise CompileError(f"Missing ';' before '{self.close.text}'", self.close.line)
            raise CompileError("Missing ';'", self.eof_line)

    def block_or_statement(self, kind: str) -> None:
        if self.accept("{"):
            self.nests.append({"kind": kind})
            return
        depth = len(self.nests)
        self.nests.append({"kind": kind, "single": True})
        self.statement()
        while len(self.nests) > depth + 1:
            self.statement()
        if len(self.nests) == depth + 1:
            self._pop()

    def _pop(self) -> None:
        n = self.nests.pop()
        if n["kind"] == "if":
            if self.peek() is not None and self.peek().kind == IDENT and self.peek().text.lower() == "else":
                self.next()
                if self.at("if"):
                    self.next()
                    self.require("(", "'Else If'")
                    self.expr_required(T("bool"), "'Else If'")
                    self.require(")", "'Else If'")
                    self.block_or_statement("if")
                else:
                    self.block_or_statement("else")
        elif n["kind"] == "do":
            if self.peek() is not None and self.peek().kind == IDENT and self.peek().text.lower() == "until":
                self.next()
                self.require("(", "'Until'")
                self.expr_required(T("bool"), "'Until'")
                self.require(")", "'Until'")
            elif self.at("while"):
                raise self.error("The loop syntax is do...until, not do...while")
            else:
                raise Unsupported("do without until")

    def _cmd(self, thing: str) -> None:
        """CheckAllow(thing, ALLOW_Cmd): refused where commands aren't allowed (a
        switch before its first case); and once a command is seen, no more locals.
        return, break, goto and labels check other flags, so they don't count."""
        if "cmd" not in self._allowed():
            raise self.error(f"{thing} is not allowed here")
        self.vardecl_ok = False

    def _allowed(self) -> set:
        """UCC's ALLOW_ flags at this point, as PushNest computes them: a loop grants
        break and continue; a switch only case and default, then statements and break
        once a case is seen (so `continue` straight inside a switch is refused); an if
        passes its parent's on. Braces push nothing."""
        a = {"return", "cmd", "label"} if self.kind == "function" else {"cmd", "label", "statecmd"}
        for n in self.nests:
            k = n["kind"]
            if k in ("if", "else"):
                a = {"elseif"} | (a & {"cmd", "label", "break", "continue", "statecmd", "return"})
            elif k in ("while", "do", "for"):
                a = {"break", "continue"} | (a & {"cmd", "label", "statecmd", "return"})
            elif k == "foreach":
                a = {"iterator", "break", "continue"} | (a & {"cmd", "label", "return"})
            elif k == "switch":
                a = {"case", "default"} | (a & {"statecmd", "return"})
                if n.get("cased"):
                    a |= {"cmd", "label", "break"}
        return a

    def _find_nest(self, kind: str):
        for n in reversed(self.nests):
            if n["kind"] == kind:
                return n
        return None

    def _return_type(self) -> T | None:
        f = self.s.func
        for p in (f or {}).get("fields", []):
            if "return" in (p.get("flags") or []):
                return self.s.field_type(p, self.s.own)
        return None

    def foreach_expr(self) -> None:
        self.allow_iterator = True
        r = self.expr(NONE, "'ForEach'")
        if getattr(self, "allow_iterator", False):
            self.allow_iterator = False
            raise self.error("'ForEach': An iterator expression is required")

    # -------------------------------------------------------- affector

    def affector(self) -> None:
        """CompileAffector: an assignment, a call, or something with a side effect."""
        self.got_affector = False
        code, r = self.compile_expr(NONE, None)
        if code < 0:
            nxt = self.next()
            raise CompileError(f"'{nxt.text if nxt else ''}': Bad command or expression",
                               nxt.line if nxt else self.eof_line)
        if self.accept("="):
            if "out" not in r.flags:
                raise self.error("'=': Left value is not a variable")
            self.expr_required(r.drop("out"), "'='")
            return
        if self.got_affector:
            return
        nxt = self.next()
        text = nxt.text if nxt else ""
        line = nxt.line if nxt else self.eof_line
        if r.kind != "none":
            raise CompileError(f"'{text}': Expression has no effect", line)
        raise CompileError(f"'{text}': Bad command or expression", line)

    # -------------------------------------------------------- expressions

    def expr_required(self, required: T, tag: str) -> T:
        code, r = self.compile_expr(required, tag)
        return r

    def expr(self, required: T, tag: str) -> T:
        code, r = self.compile_expr(required, tag)
        return r

    def compile_expr(self, required: T, tag: str | None, max_prec: int = MAX_PRECEDENCE,
                     hint: T | None = None):
        """CompileExpr: (1 matched / 0 nothing / -1 mismatch, result type)."""
        tok = self.operand(required, hint or required)
        tok = self.postfix(tok, required)
        tok = self.operators_after(tok, max_prec)

        if tok.kind == "none" and required.kind != "none":
            if tag:
                raise self.error(f"Bad or missing expression in {tag}")
            return 0, tok
        if not self.ts.matches(required, tok, False):
            conv = conversion(required, tok)
            if "out" in required.flags:
                if tag:
                    if tok.const:
                        raise self.error("Expecting a variable, not a constant")
                    if "const" in tok.flags:
                        raise self.error(f"Const mismatch in Out variable {tag}")
                    raise self.error(f"Type mismatch in Out variable {tag}")
                return -1, tok
            if required.dim != 1 or tok.dim != 1:
                if tag:
                    raise self.error(f"Array mismatch in {tag}")
                return -1, tok
            ok = (conv is not None) if "coerce" in required.flags else bool(conv and conv[1])
            if ok and (required.kind != "byte" or required.enum is None):
                tok = T(required.kind, struct=required.struct)
            else:
                if tag:
                    raise self.error(f"Type mismatch in {tag}")
                return -1, tok
        return (1 if tok.kind != "none" else 0), tok

    # operands -------------------------------------------------------------

    def operand(self, required: T, hint: T) -> T:
        tok = self.peek()
        if tok is None:
            return NONE
        k, text = tok.kind, tok.text
        low = text.lower() if k == IDENT else None

        # Signed numbers: a sign joins a number only in operand position.
        if k == SYMBOL and text in "+-" and self.peek(1) is not None and \
                self.peek(1).kind in (INT, FLOAT) and self.peek(1).start == tok.end:
            self.i += 2
            num = self.t[self.i - 1]
            v = -num.value if text == "-" else num.value
            return self._const_number(num.kind, v, required)
        if k in (INT, FLOAT):
            self.next()
            return self._const_number(k, tok.value, required)
        if k == STRING:
            self.next()
            return T("string", const=True, value=tok.value)
        if k == NAME:
            self.next()
            return T("name", const=True, value=tok.value)
        if k == SYMBOL and text == "(":
            self.next()
            code, r = self.compile_expr(required, None)
            if code == 0 or r.kind == "none":
                raise self.error("Bad or missing expression in parenthesis")
            if not self.accept(")"):
                raise self.error("Missing ')' in expression")
            return r
        if k != IDENT:
            return NONE

        # Constants spelled as identifiers.
        if low in ("true", "false"):
            self.next()
            return T("bool", const=True, value=(low == "true"))
        if low == "none":
            self.next()
            if required.kind == "delegate":
                return T("delegate", const=True)
            return T("object", cls=None, const=True)
        if low in ("vect", "rot", "rng") and self.at("(", 1):
            return self._struct_const(low)
        if low == "arraycount":
            self.next()
            self.require("(", "'ArrayCount'")
            save_aff = self.got_affector
            code, r = self.compile_expr(NONE, "'ArrayCount'")
            self.got_affector = save_aff
            if not code:
                raise self.error("Bad or missing expression in 'ArrayCount'")
            self.require(")", "'ArrayCount'")
            if r.dim <= 1:
                raise self.error("ArrayCount argument is not an array")
            return T("int", const=True, value=r.dim)
        if low == "self":
            self.next()
            if self.static:
                raise self.error("'self' is not allowed here")
            return T("object", cls=f"{self.s.package}.{self.s.own}")
        if low == "new":
            self.next()
            parent_cls = f"{self.s.package}.{self.s.own}"
            if self.accept("("):
                if not self.accept(")"):
                    _, ptok = self.compile_expr(T("object", cls=OBJECT_PATH), "'new' parent object")
                    parent_cls = ptok.cls or OBJECT_PATH
                    if self.accept(","):
                        self.expr_required(T("string"), "'new' name")
                        if self.accept(","):
                            self.expr_required(T("int"), "'new' flags")
                    self.require(")", "'new'")
            code, cls_tok = self.compile_expr(T("object", cls=CLASS_PATH, meta=OBJECT_PATH), "'new'")
            if not cls_tok.meta:
                raise self.error("'new': Invalid class")
            within = self._within_of(cls_tok.meta)
            if not self.ts.is_child(parent_cls, within):
                raise self.error(f"'new': {cls_tok.meta.split('.')[-1]} objects must reside in "
                                 f"{within.split('.')[-1]} objects, not {parent_cls.split('.')[-1]} objects")
            if self.accept("("):
                self.require(")", "'new' constructor parameters")
            return T("object", cls=cls_tok.meta)
        # Object literal: Type'Pkg.Name' (lexed as an identifier then an object/name).
        nxt = self.peek(1)
        if nxt is not None and nxt.kind in (OBJECT, NAME) and low not in ("case", "return", "goto"):
            info = self.s.ctx.info(text)
            if info is not None:
                is_actor = any(a.name.lower() == "actor" for a in self.s.ctx.ancestry(info.name))
                if not is_actor or nxt.kind == OBJECT:
                    self.i += 2
                    from . import literal_problem
                    problem = literal_problem(self.s.ctx, self.s.package, self.s.own, info.name, nxt.text)
                    if problem is not None and problem != "unknown":
                        raise CompileError(problem, nxt.line)
                    if info.name.lower() == "class":
                        target = self._class_info(nxt.text)
                        if target is None:
                            raise Unsupported("class literal")
                        return T("object", cls=CLASS_PATH, meta=target.path(), const=True)
                    # The constant takes its object's real class (Material'x' may be a
                    # Texture).
                    hit = self.s.ctx.find_loaded(nxt.text, info.name)
                    if hit == "ambiguous" and len({h[1].lower() for h in self.s.ctx.last_hits}) == 1:
                        hit = self.s.ctx.last_hits[0]   # several objects, all one class
                    if hit is None or hit == "ambiguous":
                        raise Unsupported(f"object literal {info.name}'{nxt.text}' -> {hit}")
                    real = self.s.ctx.info(hit[1])
                    if real is None:
                        raise Unsupported("object literal class")
                    return T("object", cls=real.path(), const=True)
        # Enum tag through the hint.
        if hint is not None and hint.kind == "byte" and hint.enum:
            values = self._enum_values(hint.enum)
            if values is not None:
                for idx, v in enumerate(values):
                    if v.lower() == low:
                        self.next()
                        return T("byte", enum=hint.enum, const=True, value=idx)
        # Conversions: byte(x), int(x), ..., vector(x), rotator(x).
        if low in ("byte", "int", "bool", "float", "name", "string", "vector", "rotator", "button") \
                and self.at("(", 1):
            return self._conversion(low)
        # Dynamic casts: Class(x), class<X>(x), Enum(x).
        cast = self._dynamic_cast()
        if cast is not None:
            return cast
        return self.field_expr(None, required, is_self=True, concrete=not self.static)

    def _class_info(self, name: str):
        """A class by name, the one being compiled included (it isn't in the context)."""
        if name.lower() == self.s.own.lower():
            return _OwnClass(self.s.own, f"{self.s.package}.{self.s.own}")
        return self.s.ctx.info(name)

    def _const_number(self, kind: str, value, required: T) -> T:
        if kind == INT:
            t = T("int", const=True, value=value)
        else:
            t = T("float", const=True, value=value)
        return self._attempt_const(t, required)

    def _attempt_const(self, t: T, required: T) -> T:
        """AttemptToConvertConstant."""
        rk = required.kind
        if rk == "int" and t.kind in ("int", "byte"):
            return T("int", const=True, value=t.value)
        if rk == "float" and t.kind in ("float", "int", "byte"):
            return T("float", const=True, value=float(t.value))
        if rk == "byte" and required.enum is None:
            v = t.value
            if t.kind == "byte" or (t.kind == "int" and 0 <= v < 255) or \
                    (t.kind == "float" and 0 <= v < 255 and v == int(v)):
                return T("byte", const=True, value=int(v))
        return t

    def _struct_const(self, which: str) -> T:
        self.i += 2                      # name, '('
        n = 2 if which == "rng" else 3
        noun = {"vect": "vector", "rot": "rotation", "rng": "range"}[which]
        for k in range(n):
            if k:
                if not self.accept(","):
                    raise self.error(f"Missing ',' in {'rotation' if which == 'rot' else 'vector' if which == 'vect' else 'range'}")
            sign = 1
            if self.at("-") or self.at("+"):
                sign = -1 if self.next().text == "-" else 1
            num = self.peek()
            if num is None or num.kind not in (INT, FLOAT):
                comp = {"vect": ("X", "Y", "Z"), "rot": ("Pitch", "Yaw", "Roll"),
                        "rng": ("Min", "Max")}[which][k]
                raise self.error(f"Missing {comp} component of {noun}")
            self.next()
        if not self.accept(")"):
            raise self.error(f"Missing ')' in {noun}")
        struct = {"vect": "Core.Object.Vector", "rot": "Core.Object.Rotator", "rng": "Core.Object.Range"}[which]
        return T("struct", struct=struct, const=True)

    def _enum_values(self, path: str):
        parts = path.split(".")
        if len(parts) >= 2:
            if parts[-2].lower() == self.s.own.lower():
                for f in self.s.view["fields"]:
                    if f["kind"] == "Enum" and f["name"].lower() == parts[-1].lower():
                        return f.get("values")
            info = self.s.ctx.info(parts[-2], parts[0] if len(parts) == 3 else None)
            if info is not None:
                return info.enum_values.get(parts[-1].lower())
        return None

    def _conversion(self, low: str) -> T:
        self.i += 2
        to = {"byte": T("byte"), "int": T("int"), "bool": T("bool"), "float": T("float"),
              "name": T("name"), "string": T("string"), "button": T("string"),
              "vector": T("struct", struct="Core.Object.Vector"),
              "rotator": T("struct", struct="Core.Object.Rotator")}[low]
        code, frm = self.compile_expr(NONE, low)
        if frm.kind == "none":
            raise self.error(f"'{low}' conversion: Bad or missing expression")
        conv = conversion(to, frm)
        if conv is None:
            if to.conv_kind() == frm.conv_kind():
                raise self.error(f"No need to cast '{_kind_name(to)}' to itself")
            raise self.error(f"Can't convert '{_kind_name(frm)}' to '{_kind_name(to)}'")
        if not self.accept(")"):
            raise self.error("Missing ')' in type conversion")
        return to

    def _dynamic_cast(self):
        tok = self.peek()
        nxt = self.peek(1)
        if nxt is None or not (nxt.kind == SYMBOL and nxt.text in ("(", "<")):
            return None
        info = self._class_info(tok.text)
        if info is None:
            enum = self.s.find(tok.text, None)
            if (enum is None or enum[0] != "enum") and nxt.text == "(":
                r = self.s.ctx.resolve_type(tok.text, self.s.own, self.s._own_for(self.s.own))
                if r is not None and r[0] == "enum":
                    enum = ("enum", (r[1], self._enum_values(r[1]) or []))
            if enum is not None and enum[0] == "enum" and nxt.text == "(":
                save = self.i
                self.i += 2
                # An error inside the argument escapes, as UCC's appThrowf does;
                # only a mismatch (code -1) backs off to "maybe a call".
                code, inner = self.compile_expr(T("byte"), None)
                if code != 1 or not self.accept(")"):
                    self.i = save
                    return None
                return T("byte", enum=enum[1][0])
            return None
        # A function of that name in scope takes precedence only if this isn't a cast
        # that parses: UCC tries the cast first and backs off.
        save = self.i
        self.i += 1
        meta = OBJECT_PATH
        if info.name.lower() == "class" and self.accept("<"):
            m = self.next()
            mi = self._class_info(m.text) if m is not None else None
            if mi is None or not self.accept(">") or not self.at("("):
                self.i = save
                return None
            meta = mi.path()
        if not self.accept("("):
            self.i = save
            return None
        code, inner = self.compile_expr(T("object", cls=OBJECT_PATH), None)
        if code != 1 or not self.accept(")"):
            self.i = save
            return None
        dest = info.path()
        src_cls = inner.cls
        if src_cls is None or self.ts.is_child(src_cls, dest) and (
                not _same(src_cls, CLASS_PATH) or not _same(dest, CLASS_PATH) or
                self.ts.is_child(inner.meta or OBJECT_PATH, meta)):
            raise self.error(f"Cast from '{(src_cls or 'None').split('.')[-1]}' to '{info.name}' is unnecessary")
        if self.ts.is_child(dest, src_cls):
            out = T("object", cls=dest)
            if _same(dest, CLASS_PATH):
                out = out.with_(meta=meta)
            return out
        raise self.error(f"Cast from '{src_cls.split('.')[-1]}' to '{info.name}' will always fail")

    # fields -----------------------------------------------------------------

    def field_expr(self, cls: str | None, required: T, is_self: bool, concrete: bool) -> T:
        """CompileFieldExpr for the identifier at the cursor; NONE if it isn't one."""
        tok = self.peek()
        if tok is None or tok.kind != IDENT:
            return NONE
        low = tok.text.lower()
        field_class = None
        force_scope = cls
        if low == "default" and self.at(".", 1):
            self.i += 2
            field_class = "var"
            tok = self.peek()
            return self._field(tok, force_scope, required, is_self, concrete, field_class, default=True)
        if low == "static" and self.at(".", 1):
            self.i += 2
            return self._field(self.peek(), force_scope, required, is_self, concrete, "func", static=True)
        if low == "global" and self.at(".", 1):
            if not is_self:
                raise self.error("Can only use 'global' with self")
            self.i += 2
            return self._field(self.peek(), force_scope, required, False, concrete, "func")
        if low == "super":
            if self.at("(", 1):
                if not is_self:
                    raise self.error("Can only use 'super(classname)' with self")
                self.i += 2
                ctok = self.next()
                if ctok is None or ctok.kind != IDENT:
                    raise self.error("Missing class name")
                # Any loaded class will do: UCC's "does not expand" check compares
                # the class with itself.
                target = self._class_info(ctok.text)
                if target is None:
                    raise CompileError(f"Bad class name '{ctok.text}'", ctok.line)
                self.require(")", "'super(classname)'")
                self.require(".", "'super(classname)'")
                save = self.i
                try:
                    return self._field(self.peek(), target.path(), required, False, concrete, "func")
                except Unsupported:
                    if self.s.state is None:
                        raise
                    # In a state the lookup starts at that class's same-named state,
                    # which may hold a function the class doesn't.
                    self.i = save
                    raise Unsupported("super(Class) in state")
            if not is_self:
                raise self.error("Can only use 'super' with self")
            if not self.at(".", 1):
                raise Unsupported("super")
            self.i += 2
            if self.s.state is not None:
                # In a state, super calls the version above this one; its signature is
                # the function's own, wherever it's declared.
                return self._field(self.peek(), None, required, False, concrete, "func",
                                   unknown_in=self._super_state_scope())
            parent = self.s.view.get("super")
            return self._field(self.peek(), parent, required, False, concrete, "func")
        return self._field(tok, force_scope, required, is_self, concrete, None)

    def _field(self, tok, cls, required: T, is_self: bool, concrete: bool, field_class,
               default=False, static=False, unknown_in: str | None = None) -> T:
        if tok is None or tok.kind != IDENT:
            if tok is None or not field_class:
                raise Unsupported("field after specifier")
            raise self._unknown_field(field_class, self._member_text(), cls, unknown_in, tok.line)
        found = self.s.find(tok.text, field_class, cls, in_function=cls is None and is_self)
        if found is None:
            # UCC looks in the scope's ClassWithin next and emits an Outer hop, for
            # any context, not only self.
            within = self._within_of(cls)
            if within.split(".")[-1].lower() != "object":
                found = self.s.find(tok.text, field_class, within, in_function=False)
        if found is not None and found[0] == "func" and required.kind == "delegate" and not self.at("(", 1):
            # A function named where a delegate is wanted: the function as a delegate.
            fn = found[1]
            want = self._delegate_sig(required)
            if want is not None and (len(want.params) != len(fn.params) or
                                     (want.ret is None) != (fn.ret is None)):
                raise self.error(f"'{fn.friendly}' mismatches delegate '{want.friendly}'")
            self.next()
            return T("delegate")
        if found is not None and found[0] == "func" and "delegate" in found[1].flags and not self.at("(", 1):
            alt = self.s.find(f"__{found[1].name}__Delegate", "var", cls, in_function=False)
            if alt is None:
                raise Unsupported("delegate property")
            found = alt
        if found is None:
            if field_class:
                raise self._unknown_field(field_class, tok.text, cls, unknown_in, tok.line)
            return NONE
        kind, obj = found
        self.last_field = tok.text        # Token.Identifier, as written
        if kind == "enum":
            path, values = obj
            if self.at(".", 1):
                self.i += 2
                tag = self.next()
                if tag is None:
                    raise self.error(f"Missing enum tag after '{path.split('.')[-1]}'")
                if tag.text.lower() == "enumcount":
                    return T("byte", enum=path, const=True, value=len(values))
                for idx, v in enumerate(values):
                    if v.lower() == tag.text.lower():
                        return T("byte", enum=path, const=True, value=idx)
                raise CompileError(f"Missing enum tag after '{path.split('.')[-1]}'", tag.line)
            return NONE
        if kind == "var":
            v = obj
            self.next()
            if "private" in v.flags and v.owner and v.owner.lower() != self.s.own.lower():
                raise Unsupported("private access")
            if "protected" in v.flags and v.owner and \
                    v.owner.lower() not in self.ts.class_chain(self.s.own):
                raise Unsupported("protected access")
            if default and v.local:
                raise self.error("You can't access the default value of static and local variables")
            if not concrete and not default and not v.local:
                raise self.error("You can only access default values of variables here")
            t = v.t
            if v.name.lower() == "class" and (v.owner or "").lower() == "object":
                t = t.with_(meta=(cls or f"{self.s.package}.{self.s.own}"))
            if v.name.lower() == "outer" and (v.owner or "").lower() == "object":
                w = self._within_of(cls)
                t = t.with_(cls=w)
            return t
        if kind == "func":
            fn = obj
            if not self.at("(", 1):
                return NONE
            self.i += 2
            return self._call(fn, tok, concrete, static)
        if kind == "const":
            f, owner = obj
            self.next()
            return self._const_value(f, required)
        return NONE

    def _delegate_sig(self, t: T):
        """The function a delegate type stands for, if it can be found."""
        path = t.func or ""
        name = path.split(".")[-1]
        cls = ".".join(path.split(".")[:-1]) or None
        found = self.s.find(name, "func", cls, in_function=False) if name else None
        return found[1] if found else None

    def _const_value(self, f: dict, required: T) -> T:
        """A named constant: its stored text read as one literal token."""
        from .lexer import tokenize, LexError
        text = f.get("value")
        if text is None:
            toks = f.get("_tokens") or f.get("_const_tokens")
            text = " ".join(toks) if toks else None
        if text is None:
            raise Unsupported("const value")
        try:
            toks = tokenize(str(text))
        except LexError:
            raise self.error("Error in constant")
        if not toks:
            raise self.error("Error in constant")
        sign = 1
        if toks[0].kind == SYMBOL and toks[0].text in "+-" and len(toks) > 1:
            sign = -1 if toks[0].text == "-" else 1
            toks = toks[1:]
        t0 = toks[0]
        if t0.kind == INT:
            return self._attempt_const(T("int", const=True, value=sign * t0.value), required)
        if t0.kind == FLOAT:
            return self._attempt_const(T("float", const=True, value=sign * t0.value), required)
        if t0.kind == STRING:
            return T("string", const=True, value=t0.value)
        if t0.kind == NAME:
            return T("name", const=True, value=t0.value)
        if t0.kind == IDENT and t0.text.lower() in ("true", "false"):
            return T("bool", const=True, value=t0.text.lower() == "true")
        raise Unsupported("const kind")

    def _unknown_field(self, field_class: str, name: str, cls, unknown_in, line) -> CompileError:
        """UCC's "Unknown Function/Property 'X' in '<scope's full name>'"."""
        what = "Function" if field_class == "func" else "Property"
        return CompileError(f"Unknown {what} '{name}' in '{unknown_in or self._scope_name(cls)}'", line)

    def _scope_name(self, cls) -> str:
        """GetFullName of the scope a specifier searched: a class, or for self the
        function (or state code) being compiled."""
        pkg, own = self.s.package, self.s.own
        if cls is not None:
            info = self._class_info(cls.split(".")[-1])
            if info is None:
                raise Unsupported("scope name")
            return f"Class {info.path()}"
        st = f"{self.s.state['name']}." if self.s.state is not None else ""
        if self.s.func is not None:
            return f"Function {pkg}.{own}.{st}{self.s.func['name']}"
        if self.s.state is not None:
            return f"State {pkg}.{own}.{self.s.state['name']}"
        raise Unsupported("scope name")

    def _super_state_scope(self) -> str:
        """Where super. looks from a state: the state it extends, else the same-named
        state up the class chain, else (no parent state) the parent class."""
        st = self.s.state
        parent = self.s.view.get("super")
        ext = st.get("super")
        if ext:
            if any(g["kind"] == "State" and g["name"].lower() == ext.split(".")[-1].lower()
                   for g in self.s.view["fields"]):
                return f"State {self.s.package}.{self.s.own}.{ext.split('.')[-1]}"
            name = ext.split(".")[-1]
        else:
            name = st["name"]
        for anc in self.s.ctx.ancestry(parent):
            if name.lower() in anc.states:
                return f"State {anc.path()}.{name}"
        if ext:
            raise Unsupported("super state")
        info = self.s.ctx.info(parent)
        if info is None:
            raise Unsupported("super scope")
        return f"Class {info.path()}"

    def _within_of(self, cls: str | None) -> str:
        """ClassWithin of a class (own class by default), as a path."""
        if cls is None or cls.split(".")[-1].lower() == self.s.own.lower():
            w = self.s.view.get("within")
        else:
            info = self.s.ctx.info(cls)
            w = None
            for a in (self.s.ctx.ancestry(info.name) if info else []):
                if a.within:
                    w = a.within
                    break
        return w or OBJECT_PATH

    def _call(self, fn: Func, name_tok, concrete: bool, static: bool) -> T:
        self.got_affector = True
        if "private" in fn.flags and fn.owner.lower() != self.s.own.lower():
            raise Unsupported("private function")
        if "protected" in fn.flags and fn.owner.lower() not in self.ts.class_chain(self.s.own):
            raise Unsupported("protected function")
        if "latent" in fn.flags and self.kind != "state":
            raise self.error(f"{fn.name} is not allowed here")
        if "static" not in fn.flags and not concrete:
            raise self.error("Can't call instance functions from within static functions")
        if static and "static" not in fn.flags:
            raise self.error(f"Function '{fn.name}' is not static")
        if "iterator" in fn.flags:
            if not getattr(self, "allow_iterator", False):
                raise self.error(f"{fn.name} is not allowed here")
            self.allow_iterator = False
        iterator_class = None
        results = []
        for n, (pname, ptype) in enumerate(fn.params):
            if n == 1 and iterator_class is not None:
                ptype = ptype.with_(cls=iterator_class)
            if n != 0 and not self.accept(","):
                if "optional" not in ptype.flags:
                    raise self.error(f"Call to '{fn.name}': missing or bad parameter {n + 1}")
                break
            code, r = self.compile_expr(ptype, None)
            if code == -1:
                raise self.error(f"Call to '{name_tok.text}': type mismatch in parameter {n + 1}")
            if code == 0:
                if "optional" not in ptype.flags:
                    raise self.error(f"Call to '{name_tok.text}': bad or missing parameter {n + 1}")
                if self.at(")"):
                    break
            results.append(r)
            if "iterator" in fn.flags and n == 0 and r.const and _same(r.cls, CLASS_PATH):
                iterator_class = r.meta
        close = self.next()
        if close is None or close.kind != SYMBOL:
            line = close.line if close is not None else self.eof_line
            raise CompileError(f"Call to '{name_tok.text}': Bad expression or missing ')'", line)
        if close.text != ")":
            raise CompileError(f"Call to '{name_tok.text}': Bad '{close.text}' or missing ')'", close.line)
        if fn.ret is None:
            return NONE
        ret = fn.ret.drop("out")
        if name_tok.text.lower() == "spawn" and results and results[0].meta:
            ret = ret.with_(cls=results[0].meta)
        elif name_tok.text.lower() in ("createdataobject", "loaddataobject"):
            meta = next((r.meta for r in results if r.meta), None)
            if meta:
                ret = ret.with_(cls=meta)
        return ret

    # postfix: member access, arrays -----------------------------------------

    def postfix(self, tok: T, required: T) -> T:
        while True:
            if tok.dim == 0 and self.at("."):
                self.next()
                m = self.next()
                ml = m.text.lower() if m is not None else ""
                if ml == "length":
                    tok = T("int", flags=frozenset({"out"}))
                elif ml in ("insert", "remove"):
                    self.require("(", f"'{ml}(...)'")
                    self.expr_required(T("int"), f"'{ml}(...)'")
                    self.require(",", f"'{ml}(...)'")
                    self.expr_required(T("int"), f"'{ml}(...)'")
                    self.require(")", f"'{ml}(...)'")
                    self.got_affector = True
                    return NONE
                else:
                    raise self.error("Invalid property or function call on a dynamic array")
            elif tok.dim != 1:
                if not self.at("["):
                    return tok
                self.next()
                name = self.last_field
                self.expr_required(T("int"), "array index")
                if not self.accept("]"):
                    raise self.error(f"{name or ''} is an array; expecting ']'")
                tok = tok.with_(dim=1)
            elif tok.kind == "struct" and self.at("."):
                self.next()
                m = self.peek()
                if m is not None and m.kind != IDENT:
                    raise CompileError(f"Unknown member '{self._member_text()}' in struct "
                                       f"'{(tok.struct or '').split('.')[-1]}'", m.line)
                m = self.next()
                members = self.s.struct_members(tok.struct or "")
                if members is None:
                    raise Unsupported("struct members")
                member = next((x for x in members if x["name"].lower() == (m.text.lower() if m else "")), None)
                if member is None:
                    raise CompileError(f"Unknown member '{m.text if m else ''}' in struct "
                                       f"'{(tok.struct or '').split('.')[-1]}'", m.line if m else self.eof_line)
                mt = self.s.field_type(member, self.s.own)
                self.last_field = m.text
                tok = mt.with_(flags=(tok.flags & {"out", "const"}) | (mt.flags & {"const"}))
            elif tok.kind == "object" and self.at("."):
                self.next()
                if _same(tok.cls, CLASS_PATH) and (self.at("default") or self.at("static")):
                    was = self.got_affector
                    self.got_affector = False
                    tok = self.field_expr(tok.meta, required, is_self=False, concrete=False)
                    called = self.got_affector
                    self.got_affector = was or called
                    if tok.kind == "none" and not called:
                        raise Unsupported("class context")
                    continue
                m = self.peek()
                if tok.cls is None:
                    # A member of the None literal: UCC trips an assertion (the
                    # message is this 64-bit 3374 build's; the line is its source's).
                    raise CompileError(NONE_CONTEXT_ASSERT, 0)
                if m is not None and m.kind != IDENT:
                    raise CompileError(f"Unrecognized member '{self._member_text()}' in class "
                                       f"'{tok.cls.split('.')[-1]}'", m.line)
                was = self.got_affector
                self.got_affector = False
                r = self.field_expr(tok.cls, required, is_self=False, concrete=True)
                called = self.got_affector
                self.got_affector = was or called
                if r.kind == "none" and not called:
                    if m is not None and m.kind == IDENT and self.s.find(m.text, None, tok.cls, False) is None:
                        chain = self.ts.class_chain(tok.cls)
                        last = self.s.ctx.info(chain[-1]) if chain else None
                        if last is not None and last.super is None and last.name.lower() != "object":
                            raise Unsupported("member of an intrinsic class")
                        raise CompileError(f"Unrecognized member '{m.text}' in class "
                                           f"'{tok.cls.split('.')[-1]}'", m.line)
                tok = r
            else:
                return tok

    def _member_text(self) -> str:
        """The token UCC reads where a member name should be. Reading an operand, a
        sign touching a digit is part of the number: C.-5 names a member '-5'."""
        m = self.peek()
        n = self.peek(1)
        if m.kind == SYMBOL and m.text in ("-", "+") and n is not None and \
                n.kind in (INT, FLOAT) and n.start == m.end:
            return m.text + n.text
        return m.text

    # operators ----------------------------------------------------------------

    def operators_after(self, tok: T, max_prec: int) -> T:
        while True:
            op = self.peek()
            if op is None or op.kind not in (SYMBOL, IDENT):
                return tok
            pre = tok.kind == "none"
            links = self.s.operators(op.text, pre)
            if not links:
                return tok
            prec = links[-1].precedence
            numparms = min(3, min(f.numparms for f in links))
            if prec >= max_prec:
                return tok
            self.next()
            right = NONE
            if numparms == 3 or pre:
                code, right = self.compile_expr(NONE, None, prec, tok)
                if right.kind == "none":
                    raise self.error(f"Bad or missing expression after '{op.text}'")
            best, best_match, matches = None, 0, 0
            any_left = any_right = False
            for f in links:
                this = 0
                parms = [p for _, p in f.params]
                k = 0
                if f.numparms == 3 or not pre:
                    c = self.ts.cost(parms[k], tok)
                    this = c
                    any_left = any_left or c != MAXINT
                    k += 1
                if f.numparms == 3 or pre:
                    c = self.ts.cost(parms[k], right)
                    this = max(this, c)
                    any_right = any_right or c != MAXINT
                if (best is None or this < best_match) and f.numparms == numparms:
                    best, best_match, matches = f, this, 1
                elif this == best_match:
                    matches += 1
            if best_match == MAXINT:
                if op.text in ("==", "!=") and tok.kind == "struct" and right.kind == "struct" \
                        and _same_struct(tok.struct, right.struct):
                    tok = T("bool")
                    continue
                if any_left and not any_right:
                    raise self.error(f"Right type is incompatible with '{op.text}'")
                if any_right and not any_left:
                    raise self.error(f"Left type is incompatible with '{op.text}'")
                raise self.error(f"Types are incompatible with '{op.text}'")
            if matches > 1:
                raise self.error(f"Operator '{op.text}': Can't resolve overload ({matches} matches of quality {best_match})")
            parms = [p for _, p in best.params]
            if any("out" in p.flags for p in parms):
                self.got_affector = True
            tok = best.ret.drop("out") if best.ret is not None else NONE


def _kind_name(t: T) -> str:
    return {"byte": "Byte", "int": "Int", "bool": "Bool", "float": "Float", "object": "Object",
            "name": "Name", "string": "String", "struct": "Struct", "vector": "Struct",
            "rotator": "Struct", "delegate": "Delegate", "pointer": "Pointer"}.get(t.kind, t.kind)


# ---------------------------------------------------------------- driver

def compile_bodies(view_raw_tokens, body_spans, own_view: dict, ctx, package: str,
                   eof_line: int):
    """Compile every function body and state code block. Returns the first
    CompileError (by position), None if all compile, or raises Unsupported."""
    own_structs = {}

    def collect(fields, path):
        for f in fields:
            if f["kind"] == "Struct":
                own_structs[f["name"].lower()] = f
                collect(f.get("fields", []), path)
    collect(own_view["fields"], "")
    ts = TypeSystem(ctx, own_view["name"], package, own_view.get("super"), own_structs)
    errors = []
    # A const declared in any body is visible from every body (measured: LibHTTP4's
    # HttpUtil declares DAYS_PER_YEAR in one function and uses it in another).
    shared_consts: dict = {}
    for span in body_spans:
        probe_scope = Scope(ts, ctx, own_view, None, None)
        probe = Body(view_raw_tokens[span[2]:span[3]], probe_scope, False, "function", eof_line)
        probe.register_consts()
        shared_consts.update(probe_scope.body_consts)
    from .resolve import replication_name_error
    for span in body_spans:
        func, state, start, end, kind, rep = span
        scope = Scope(ts, ctx, own_view, func, state)
        scope.body_consts.update(shared_consts)
        static = func is not None and "static" in (func.get("flags") or [])
        close = view_raw_tokens[end] if kind != "replication" and end < len(view_raw_tokens) \
            and view_raw_tokens[end].text == "}" else None
        b = Body(view_raw_tokens[start:end], scope, static,
                 "function" if kind == "replication" else kind, eof_line, close)
        try:
            if kind == "replication":
                b.compile_condition()
                for rname, line, pos in rep["names"]:
                    msg = replication_name_error(rname, own_view, ctx)
                    if msg:
                        return CompileError(msg, line)
            else:
                b.compile()
        except CompileError as e:
            return e
    return None
