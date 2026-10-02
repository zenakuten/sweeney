"""defaultproperties: what UCC stores for a class's defaults, and the errors it logs.

The defaults text (importer.Imported.defaults) is imported line by line after the
class compiles, as UCC's ImportProperties does, onto a copy of the parent's
defaults; then only values that differ from the parent's are stored. The result has
reflect.decode_defaults' shape, so the scoreboard compares it with what UCC stored.

Rules, each pinned by a probe in tests/uparse (suites/defaults.jsonl, dp-*):
- Lines are re-split on `|` and cut at `//`, both outside double quotes. A line is
  `Name=Value`, `Name(i)=Value` or `Name[i]=Value`; trailing `;` and blanks go.
- A property called `Name` is never imported. An unknown property, a missing `=`
  and an out-of-range index are skipped without failing the build. A property set
  twice is `redundant data: <line>`.
- int: a leading `-` or digit, read by atoi (`3.9` -> 3, `12abc` -> 12, `0x10` and
  `+5` -> nothing). float: atof (`2e3`, `1.25f`). byte: digits, wrapped (300 -> 44).
  An enum takes only an enum name -- a number is silently dropped. bool: 1/True/0/
  False in any case, quoted or not; anything else is dropped.
- name: one token -- a quoted string, else a run of letters, digits, `_` and `-`,
  else a single character. So `'Foo'` stores `'`, `name'Foo'` stores `name`, and
  `Foo Bar` stores `Foo`.
- string: must be double-quoted (else `Missing '"' in string default properties`);
  a backslash takes the next character literally (`"a\\41b"` is `a41b`).
- object: `Class'Pkg.Name'` (loading the package from disk if needed) or a bare
  path to an object already loaded; else `unresolved reference to '<value>'`.
- struct: `(A=1,B=2)`. A member name is everything up to `=`, spaces included, so
  a space before a member is `Unknown member  X in S` -- logged, and parsing goes on,
  so the last bad member is the message that shows. Members not mentioned keep the
  current value (the parent's, or zero for the class's own property).
"""

from __future__ import annotations

import re
import struct as _struct
from pathlib import Path

UNKNOWN = object()      # a value we can't predict: the whole prediction becomes None

BINARY_STRUCTS = {"vector": ("x", "y", "z"), "rotator": ("pitch", "yaw", "roll"),
                  "color": ("b", "g", "r", "a")}


class _Unpredictable(Exception):
    """A value we can't predict. `errors` says whether it could also hide an error
    (an unknown struct can; a delegate to a function that exists can't)."""

    def __init__(self, reason: str = "", errors: bool = True):
        super().__init__(reason)
        self.errors = errors


# ---------------------------------------------------------------- C-style readers

def _atoi(s: str) -> int:
    m = re.match(r"\s*([-+]?\d+)", s)
    if not m:
        return 0
    n = int(m.group(1))
    n &= 0xFFFFFFFF
    return n - (1 << 32) if n & 0x80000000 else n


def _atof(s: str) -> float:
    m = re.match(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", s)
    v = float(m.group(1)) if m else 0.0
    return round(_struct.unpack("<f", _struct.pack("<f", v))[0], 6)


def _read_token(buf: str, dotted: bool = False) -> tuple[str, str] | None:
    """ReadToken: a quoted string (\\\\ is a backslash, \\XX a hex byte), else an
    identifier run, else one character. Returns (token, rest) or None."""
    if buf.startswith('"'):
        out, i = [], 1
        while i < len(buf) and buf[i] not in '"\r\n':
            if buf[i] != "\\":
                out.append(buf[i])
                i += 1
            elif buf[i + 1:i + 2] == "\\":
                out.append("\\")
                i += 2
            else:
                try:
                    out.append(chr(int(buf[i + 1:i + 3], 16)))
                except ValueError:
                    out.append("\0")
                i += 3
        if buf[i:i + 1] != '"':
            return None
        return "".join(out), buf[i + 1:]
    if buf[:1].isalnum():
        i = 0
        while i < len(buf) and (buf[i].isalnum() or buf[i] in "_-" or (dotted and buf[i] == ".")):
            i += 1
        return buf[:i], buf[i:]
    if not buf:
        return "", ""
    return buf[0], buf[1:]


def _parse_token_string(buf: str) -> tuple[str, str]:
    """ParseToken with escapes, for a delimited string: quoted, else up to a blank."""
    buf = buf.lstrip(" \t")
    if buf.startswith('"'):
        out, i = [], 1
        while i < len(buf) and buf[i] != '"':
            c = buf[i]
            i += 1
            if c == "\\":
                if i >= len(buf):
                    break
                c = buf[i]
                i += 1
            out.append(c)
        if buf[i:i + 1] == '"':
            i += 1
        return "".join(out), buf[i:]
    m = re.match(r"[^ \t]*", buf)
    return m.group(0), buf[m.end():]


# ---------------------------------------------------------------- the importer

class DefaultsImporter:
    def __init__(self, ctx, class_name: str, package: str, props: dict, parent_values: dict,
                 enum_values, struct_members, object_exists, own_structs):
        self.ctx = ctx
        self.class_name, self.package = class_name, package
        self.props = props                  # lower name -> resolved property field
        self.values = {k: dict(v) for k, v in parent_values.items()}
        self.parent = parent_values
        self.enum_values = enum_values      # enum path -> [names]
        self.struct_members = struct_members  # struct path -> member fields
        self.object_exists = object_exists  # (class or None, path) -> class name or None
        self.errors: list[str] = []         # every logged line, in order
        self.failed = False                 # an error that fails the build
        self.values_known = True            # every stored value predicted
        self.errors_known = True            # no line we couldn't check for errors
        self.subobjects: set[str] = set()   # Begin Object Name=... in this block
        self.unknown_reason = None

    def path(self) -> str:
        return f"{self.package}.{self.class_name}"

    def log_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.failed = True

    def run(self, lines: list[str]) -> None:
        self.subobject_class: dict[str, str] = {}
        for raw in _split_lines(lines):
            m = re.match(r"\s*begin\s+object\b.*?\bname\s*=\s*(\w+)", raw, re.I)
            if m:
                c = re.search(r"\bclass\s*=\s*([\w.]+)", raw, re.I)
                if c and self._class_missing(c.group(1)):
                    continue              # never created
                self.subobjects.add(m.group(1).lower())
                if c:
                    self.subobject_class[m.group(1).lower()] = c.group(1)
        self._defined = set()
        self._stack: list = []
        for raw in _split_lines(lines):
            try:
                self._line(raw)
            except _Unpredictable as e:
                # Skip this line: its value is unknown, and maybe whether it errors.
                self.values_known = False
                if e.errors:
                    self.errors_known = False
                    self.unknown_reason = self.unknown_reason or str(e)

    def _line(self, raw: str) -> None:
        defined = self._defined
        if True:
            s = raw.lstrip(" \t")
            low = s.lower()
            if re.match(r"begin\s+object\b", low):
                # A subobject: its lines set the subobject class's properties. Nothing
                # is stored in this class's defaults, but its errors still fail the build.
                m = re.search(r"\bclass\s*=\s*([\w.]+)", s, re.I)
                if m and self._class_missing(m.group(1)):
                    # ParseObject finds no class: nothing is created and the depth
                    # stays, so the lines that follow set the enclosing object's
                    # properties, and its End Object closes the enclosing one.
                    return
                self._stack.append(self._subobject_props(m.group(1) if m else None))
                return
            if re.match(r"end\s+object\b", low):
                if self._stack:
                    self._stack.pop()
                return
            if self._stack:
                frame = self._stack[-1]
                if frame is None:
                    raise _Unpredictable("subobject class")
                saved = (self.props, self.values, self.parent, self.class_name, self.package,
                         self._defined)
                self.props, self.values, self.parent = frame["props"], {}, {}
                self.package, self.class_name = frame["path"].split(".", 1)
                self._defined = frame["defined"]
                try:
                    self._assign(raw, s)
                finally:
                    (self.props, self.values, self.parent, self.class_name, self.package,
                     self._defined) = saved
                return
            self._assign(raw, s)

    def _class_missing(self, cls: str) -> bool:
        """Sure that no class of that name is loaded."""
        if self.ctx.visible is None or self.ctx.info(cls) is not None:
            return False
        return cls.split(".")[-1].lower() != self.class_name.lower()

    def _subobject_props(self, cls: str | None):
        """{lower name: property} for a subobject's class, or None if unknown."""
        if not cls:
            return None
        info = self.ctx.info(cls)
        if info is None:
            return None
        props = {}
        for anc in self.ctx.ancestry(info.name):
            for low, f in anc.var_defs.items():
                if low not in props:
                    props[low] = {**self.resolve_inner(f, anc.name), "_owner_path": anc.path()}
        return {"props": props, "path": info.path(), "defined": set()}

    def _assign(self, raw: str, s: str) -> None:
        defined = self._defined
        if True:
            m = re.match(r"([^=(\[]*)", s)
            token = m.group(1).rstrip(" \t")
            rest = s[m.end():]
            if not rest:
                return
            index = -1
            if rest[0] in "([":
                index = _atoi(rest[1:])
                close = re.search(r"[)\]]", rest)
                if not close:
                    return              # an ExecWarning: no build failure
                rest = rest[close.end():]
            rest = rest.lstrip(" \t")
            if not rest.startswith("="):
                return                  # "Missing '='": an ExecWarning only
            value = rest[1:].lstrip(" \t")
            prop = self.props.get(token.lower())
            if prop is None:
                prop = self.props.get(f"__{token}__delegate".lower())
                if prop is None:
                    self.errors.append(f"{self.path()}: Unknown property in defaults: {raw}")
                    return
            dim = prop.get("array_dim", 1)
            if not isinstance(dim, int):
                dim = 1 << 30             # a constant we didn't resolve: don't judge
            if index >= dim and prop["kind"] != "ArrayProperty":
                self.errors.append(f"{self.path()}: Out of bound array default property ({index}/{dim})")
                return
            key = (prop["name"].lower(), index)
            if key in defined:
                self.log_error(f"redundant data: {raw}")
                return
            defined.add(key)
            if prop["name"].lower() == "name":
                return
            value = value.rstrip(" \t;")
            if prop["kind"] == "StrProperty" and (not value or value[0] != '"' or value[-1] != '"'):
                self.log_error(f"{self.path()}: Missing '\"' in string default properties : {raw}")
            name = prop["name"].lower()
            if prop["kind"] == "ArrayProperty" and index > -1:
                arr = list(self.values.get(name, {}).get("0", []) or [])
                inner = self._inner(prop)
                while len(arr) <= index:
                    arr.append(self._zero(inner))
                got = self._import(inner, value, arr[index], prop)
                if got is not None:
                    arr[index] = got
                self.values.setdefault(name, {})["0"] = arr
            elif prop["kind"] == "DelegateProperty":
                if value.lower() == "none":
                    return
                parts = value.split(".")
                target = parts[-1].lower()
                if len(parts) == 2:
                    # Object.Function: a subobject (or class) and a function of its class.
                    if parts[0].lower() == "none":
                        self.log_error(f"{self.path()}: Delegate assignment failed: {raw}")
                        return
                    owner = self.subobject_class.get(parts[0].lower()) or parts[0]
                    info = self.ctx.info(owner)
                    if info is None:
                        raise _Unpredictable("delegate object", errors=False)
                    exists = any(target in a.functions for a in self.ctx.ancestry(info.name))
                else:
                    exists = self.function_exists(target)
                if not exists:
                    self.log_error(f"{self.path()}: Delegate assignment failed: {raw}")
                    return
                raise _Unpredictable("delegate defaults", errors=False)   # stored, not decoded
            else:
                idx = str(max(index, 0))
                cur = self.values.get(name, {}).get(idx, self._zero(prop))
                got = self._import(prop, value, cur, prop)
                if got is not None:
                    self.values.setdefault(name, {})[idx] = got

    # -------------------------------------------------------- per-type import

    def _inner(self, prop: dict) -> dict:
        """An array property's element type: resolved (own class), compiled (`_inner`)
        or raw from source (`inner_type`)."""
        inner = prop.get("inner_field") or prop.get("_inner_field") or prop.get("_inner")
        if inner is None and prop.get("inner_type"):
            it = prop["inner_type"]
            inner = {"name": prop["name"], "kind": it["kind"], "type": it.get("type"),
                     "enum": it.get("enum"), "meta_class": it.get("meta_class")}
            inner = self.resolve_inner(inner, prop.get("_owner_path", self.path()).split(".")[-1])
        if inner is None:
            raise _Unpredictable("array element type")
        return inner

    def _zero(self, prop: dict):
        k = prop["kind"]
        if k in ("IntProperty", "ByteProperty"):
            return 0
        if k == "FloatProperty":
            return 0.0
        if k == "BoolProperty":
            return False
        if k == "StructProperty":
            return self._struct_zero(prop.get("type", ""))
        if k == "ArrayProperty":
            return []
        return None

    def _struct_zero(self, path: str):
        sname = path.split(".")[-1].lower()
        if sname in BINARY_STRUCTS:
            zero = 0.0 if sname == "vector" else 0
            return {m: zero for m in BINARY_STRUCTS[sname]}
        members = self.struct_members(path)
        if members is None:
            raise _Unpredictable(f"struct {path}")
        return {m["name"].lower(): self._zero(m) for m in members if m["kind"].endswith("Property")}

    def _import(self, prop: dict, buf: str, cur, owner: dict):
        """The new value, or None when UCC leaves the current one unchanged. Raises
        _Unpredictable for what can't be modelled yet."""
        r = self._import_rest(prop, buf, cur, owner)
        return None if r is None else r[0]

    def _import_rest(self, prop: dict, buf: str, cur, owner: dict):
        """(value, remaining text) or None. Struct members need the remainder."""
        k = prop["kind"]
        if k == "IntProperty":
            if buf[:1] == "-" or buf[:1].isdigit():
                n = re.match(r"[-0-9]*", buf)
                return _atoi(buf), buf[n.end():]
            return None if cur is None else (cur, buf)
        if k == "FloatProperty":
            m = re.match(r"[^,)\r\n]*", buf)
            return _atof(buf), buf[m.end():]
        if k == "BoolProperty":
            t = _read_token(buf)
            if t is None:
                return None
            tok, rest = t
            if tok.lower() in ("1", "true"):
                return True, rest
            if tok.lower() in ("0", "false"):
                return False, rest
            return None
        if k == "ByteProperty":
            if prop.get("enum"):
                t = _read_token(buf)
                if t is None:
                    return None
                tok, rest = t
                names = self.enum_values(prop["enum"])
                if names is None:
                    raise _Unpredictable("enum values")
                for i, n in enumerate(names):
                    if n.lower() == tok.lower():
                        return i, rest
                if rest[:1].isdigit():
                    n = re.match(r"\d*", rest)
                    return _atoi(rest) & 0xFF, rest[n.end():]
                return None
            if buf[:1].isdigit():
                n = re.match(r"\d*", buf)
                return _atoi(buf) & 0xFF, buf[n.end():]
            return None
        if k == "NameProperty":
            t = _read_token(buf)
            if t is None:
                return None
            tok, rest = t
            return (["name", tok] if tok and tok.lower() != "none" else None), rest
        if k == "StrProperty":
            s, rest = _parse_token_string(buf)
            return (["str", s] if s else None), rest
        if k in ("ObjectProperty", "ClassProperty"):
            return self._import_object(prop, buf, owner)
        if k == "StructProperty":
            return self._import_struct(prop, buf, cur)
        if k == "ArrayProperty":
            return self._import_array(prop, buf)
        raise _Unpredictable(k)

    def _import_array(self, prop: dict, buf: str):
        """UArrayProperty::ImportText: (a,b,,c). The array starts empty; a skipped
        element is zeroed; each element is imported delimited."""
        if not buf.startswith("("):
            return None
        inner = self._inner(prop)
        arr: list = []
        rest = buf[1:]
        index = 0
        while rest[:1] != ")":
            if not rest:
                raise _Unpredictable("array: runs off the line")
            while rest[:1] == ",":
                rest = rest[1:]
                if index >= len(arr):
                    arr.append(self._zero(inner))
                index += 1
                if rest[:1] == ")":
                    return arr, rest[1:]
            if index >= len(arr):
                arr.append(self._zero(inner))
            r = self._import_rest(inner, rest, arr[index], prop)
            index += 1
            if r is None:
                return None
            arr[index - 1] = r[0] if r[0] != [None] else None
            rest = r[1]
            if rest[:1] != ",":
                break
            rest = rest[1:]
        if rest[:1] != ")":
            return None
        return arr, rest[1:]

    def _import_object(self, prop: dict, buf: str, owner: dict):
        t = _read_token(buf, dotted=True)
        if t is None:
            return None
        tok, rest = t
        if tok.lower() == "none":
            return [None], rest           # unwrapped below to None
        want = "class" if prop["kind"] == "ClassProperty" else (prop.get("type") or "Core.Object")
        full = f"{'ClassProperty' if prop['kind'] == 'ClassProperty' else 'ObjectProperty'} " \
               f"{owner.get('_owner_path') or self.path()}.{owner['name']}"
        if owner.get("kind") == "ArrayProperty":
            # A dynamic array's element is its Inner property, an object inside it.
            full += f".{owner['name']}"
        r2 = rest.lstrip(" ")
        if r2.startswith("'"):
            t2 = _read_token(r2[1:], dotted=True)
            if t2 is None or not t2[1].startswith("'"):
                return None
            obj_path, rest = t2[0], t2[1][1:]
            cls = tok
            if self._class_missing(cls):
                self.log_error(f"{full}: unresolved cast in '{buf}'")
                return None
            found = self.object_exists(cls, obj_path, True)
        else:
            obj_path = tok
            cls = None
            found = self.object_exists(want.split(".")[-1] if prop["kind"] != "ClassProperty"
                                       else "class", obj_path, False)
        if found is UNKNOWN:
            # Objects of the package being built, subobjects of this block, packages
            # #exec makes: not checkable here, but not errors in practice.
            raise _Unpredictable("object reference", errors=False)
        if not found:
            self.log_error(f"{full}: unresolved reference to '{buf}'")
            return None
        path, obj_class = found
        if prop["kind"] == "ClassProperty":
            if not self.ctx_is_child(path, prop.get("meta_class") or "Core.Object"):
                return None               # wrong metaclass: silently not set
        elif cls is not None and not self.ctx_is_child_class(obj_class, want):
            self.log_error(f"{full}: bad cast in '{buf}'")
        return ["obj", path], rest

    def ctx_is_child(self, class_path: str, meta: str) -> bool:
        meta_low = meta.split(".")[-1].lower()
        for info in self.ctx.ancestry(class_path):
            if info.name.lower() == meta_low:
                return True
        return meta_low == "object"

    def ctx_is_child_class(self, obj_class: str, want_path: str) -> bool:
        want_low = want_path.split(".")[-1].lower()
        if want_low == "object" or obj_class.lower() == want_low:
            return True
        chain = list(self.ctx.ancestry(obj_class))
        if not chain or (chain[-1].super is None and chain[-1].name.lower() != "object"):
            return True                   # unknown (intrinsic) hierarchy: assume fine
        return any(i.name.lower() == want_low for i in chain)

    def _import_struct(self, prop: dict, buf: str, cur):
        if not buf.startswith("("):
            return None
        sname = prop.get("type", "")
        value = dict(cur) if isinstance(cur, dict) else self._struct_zero(sname)
        binary = sname.split(".")[-1].lower() in BINARY_STRUCTS
        members = None if binary else self.struct_members(sname)
        if not binary and members is None:
            raise _Unpredictable(f"struct {sname}")
        if binary:
            kind = "FloatProperty" if sname.split(".")[-1].lower() == "vector" else (
                "ByteProperty" if sname.split(".")[-1].lower() == "color" else "IntProperty")
            members = [{"name": m, "kind": kind} for m in BINARY_STRUCTS[sname.split(".")[-1].lower()]]
        by_name = {m["name"].lower(): m for m in members if m["kind"].endswith("Property")}
        label = prop["name"]
        i = 1
        original = buf
        while i < len(buf) and buf[i] != ")":
            m = re.match(r"[^=\[]*", buf[i:i + 63])
            name = m.group(0)
            i += len(name)
            element = 0
            if buf[i:i + 1] == "[":
                j = i + 1
                while j < len(buf) and buf[j].isdigit():
                    j += 1
                if buf[j:j + 1] != "]":
                    self.log_error(f"{label}::ImportText: Illegal array element in: {original}")
                    return None
                element = _atoi(buf[i + 1:j])
                i = j + 1
            if buf[i:i + 1] != "=":
                self.log_error(f"{label}::ImportText: Illegal or missing key name in: {original}")
                return None
            i += 1
            member = by_name.get(name.lower())
            if member is not None:
                if buf[i:i + 1] not in (",", ")"):
                    if element == 0:
                        r = self._import_rest(member, buf[i:], value.get(name.lower()), member)
                        if r is None:
                            self.log_error(f"{label}::ImportText failed in: {original}")
                            return None
                        value[name.lower()] = r[0] if r[0] != [None] else None
                        i = len(buf) - len(r[1])
                    else:
                        r = self._import_rest(member, buf[i:], None, member)
                        if r is None:
                            self.log_error(f"{label}::ImportText failed in: {original}")
                            return None
                        # Elements past 0 don't show in the decoded form but still
                        # make the struct differ; kept under a private key.
                        value[f"{name.lower()}[{element}]"] = r[0]
                        i = len(buf) - len(r[1])
            else:
                self.log_error(f"Unknown member {name} in {label}")
                sub = 0
                while i < len(buf) and buf[i] not in "\r\n" and (sub > 0 or buf[i] not in "),"):
                    if buf[i] == '"':
                        i += 1
                        while i < len(buf) and buf[i] not in '"\r\n':
                            i += 1
                    elif buf[i] == "(":
                        sub += 1
                    elif buf[i] == ")":
                        sub -= 1
                    i += 1
            if buf[i:i + 1] == ",":
                i += 1
            elif buf[i:i + 1] != ")":
                self.log_error(f"{label}::ImportText: Bad termination in: {original}")
                return None
        return value, buf[i + 1:]

    # -------------------------------------------------------- result

    def stored(self) -> dict:
        """Values that differ from the parent's: what UCC writes."""
        out = {}
        for name, elems in self.values.items():
            for idx, v in elems.items():
                if v == [None]:
                    v = None
                    elems[idx] = None
                pv = self.parent.get(name, {}).get(idx)
                if pv is None:
                    pv = self._zero(self.props[name]) if name in self.props else None
                if v != pv and not (v is None and pv in (None, [], 0, False, "")):
                    if v is None:
                        continue
                    if isinstance(v, dict):
                        v = {k: x for k, x in v.items() if "[" not in k}
                    out.setdefault(name, {})[idx] = v
        return out


def _split_lines(lines: list[str]) -> list[str]:
    """ParseLine (not exact): `|` ends a line and `//` starts a comment, outside
    double quotes."""
    out = []
    for line in lines:
        cur, quoted, i = [], False, 0
        while i < len(line):
            ch = line[i]
            if not quoted and line.startswith("//", i):
                break
            if not quoted and ch == "|":
                out.append("".join(cur))
                cur = []
                i += 1
                continue
            if ch == '"':
                quoted = not quoted
            cur.append(ch)
            i += 1
        out.append("".join(cur))
    return [l for l in out if l.strip()]


# ---------------------------------------------------------------- glue

def predict_defaults(view: dict, lines: list[str], package: str, ctx, stock_packages=(),
                     has_exec: bool = False, package_exec: bool = True):
    """(stored defaults or None, list of logged lines, failed?) for a resolved class."""
    name = view["name"]
    sup = view.get("super")
    parent_values = ctx.effective_defaults(sup) if sup else {}
    unknown_parent = False
    if parent_values is None:
        parent_values, unknown_parent = {}, True
    if any(v is None for v in parent_values.values()):
        parent_values = {k: v for k, v in parent_values.items() if v is not None}
        unknown_parent = True

    own_fields = view["fields"]
    own_structs = {f["name"].lower(): f for f in own_fields if f["kind"] == "Struct"}

    def resolve_prop(f: dict, owner: str) -> dict:
        """A property field with a settled kind (compiled fields already are)."""
        if f.get("kind") == "UnresolvedProperty":
            r = ctx.resolve_type(f.get("type", ""), owner)
            g = dict(f)
            if r is None:
                g["kind"] = "ObjectProperty"
            elif r[0] == "struct":
                g["kind"], g["type"] = "StructProperty", r[1]
            elif r[0] == "enum":
                g["kind"], g["enum"] = "ByteProperty", r[1]
            else:
                g["kind"], g["type"] = "ObjectProperty", r[1]
            return g
        return f

    props: dict[str, dict] = {}
    for f in own_fields:
        if f["kind"].endswith("Property"):
            props.setdefault(f["name"].lower(), f)
    for info in ctx.ancestry(sup):
        for low, f in info.var_defs.items():
            if low not in props:
                # UCC names a property in errors by the class that declares it
                # (ClassProperty Engine.Inventory.AttachmentClass).
                props[low] = {**resolve_prop(f, info.name), "_owner_path": info.path()}

    def enum_values(path: str):
        parts = path.split(".")
        if len(parts) >= 2:
            info = ctx.info(parts[-2], parts[0] if len(parts) == 3 else None)
            if parts[-2].lower() == name.lower():
                for f in own_fields:
                    if f["kind"] == "Enum" and f["name"].lower() == parts[-1].lower():
                        return f.get("values")
            if info is not None:
                return info.enum_values.get(parts[-1].lower())
        return None

    def struct_members(path: str, _depth=0):
        """A struct's members, its parent struct's included (Plane extends Vector)."""
        parts = path.split(".")
        sname = parts[-1].lower()
        sdef, owner = None, None
        if len(parts) >= 2 and parts[-2].lower() == name.lower() and sname in own_structs:
            sdef, owner = own_structs[sname], name
            members = list(sdef.get("fields", []))
        else:
            info = ctx.info(parts[-2], parts[0]) if len(parts) >= 2 else None
            if info is None or info.struct_fields.get(sname) is None:
                return None
            owner = info.name
            members = [resolve_prop(m, info.name) for m in info.struct_fields[sname]]
            sdef = info.struct_defs.get(sname) if hasattr(info, "struct_defs") else None
        sup = (sdef or {}).get("super")
        if sup and _depth < 8:
            r = ctx.resolve_type(sup, owner)
            if r is None or r[0] != "struct":
                return None
            parent = struct_members(r[1], _depth + 1)
            if parent is None:
                return None
            members = parent + members
        return members

    loaded = {p.lower() for p in stock_packages} | {package.lower()}

    def object_exists(cls, path: str, quoted: bool):
        if "." not in path:
            if path.lower() in _current_importer.subobjects:
                return UNKNOWN            # a subobject declared in this block
            hit = ctx.find_loaded(path)
            if hit is None:
                return None
            return UNKNOWN
        pkg_name = path.split(".")[0]
        if pkg_name.lower() in (package.lower(), name.lower()):
            # Objects of the package being built. Pkg.Name or Class.Name is a
            # subobject created so far, a member of the class, or a class of the
            # package; without #exec lines nothing else is there.
            parts = path.split(".")
            if len(parts) != 2 or has_exec or package_exec:
                return UNKNOWN
            leaf = parts[1].lower()
            if leaf in _current_importer.subobjects:
                return UNKNOWN
            if pkg_name.lower() == name.lower() and leaf in own_names:
                return UNKNOWN
            if pkg_name.lower() == package.lower():
                info = ctx.info(parts[1], package)
                if info is not None and info.package.lower() == package.lower():
                    return (info.path(), "Class")
            return None
        if cls == "class" or (cls or "").lower() == "class":
            info = ctx.info(path)
            return (info.path(), "Class") if info is not None and \
                info.package.lower() == pkg_name.lower() else None
        if not quoted and pkg_name.lower() not in loaded:
            return UNKNOWN                # bare path into a package UCC may not have loaded
        exports = ctx.package_exports(pkg_name)
        if exports is None:
            # No package of that name: the path may start with a group or class inside
            # a loaded package. Failing that, UCC can't load it either -- unless this
            # class's #exec lines create it.
            hit = ctx.find_loaded(path)
            if hit == "ambiguous":
                return UNKNOWN
            if hit is not None:
                return hit
            return UNKNOWN if has_exec else None
        hit = exports.get(path.lower())
        if hit is None:
            # Pkg.Name also finds an object inside a group (Sound'AnnouncerClassic.prepare').
            leaf = path.split(".")[-1].lower()
            matches = [v for k, v in exports.items() if k.split(".")[-1] == leaf]
            if len(matches) == 1:
                hit = matches[0]
            elif matches:
                return UNKNOWN
            else:
                return None
        def in_memory(cand):
            return cand if ctx.loaded_object(pkg_name, cand[0]) else UNKNOWN
        if cls is None:
            return in_memory(hit)
        unknown = False
        for cand in ctx.export_all(pkg_name, hit[0]) or [hit]:
            if cand[1].lower() == cls.lower():
                return in_memory(cand)
            chain = list(ctx.ancestry(cand[1]))
            if not chain or (chain[-1].super is None and chain[-1].name.lower() != "object"):
                unknown = True            # intrinsic classes: hierarchy unknown here
            elif any(i.name.lower() == cls.lower() for i in chain):
                return in_memory(cand)
        return UNKNOWN if unknown else None

    imp = DefaultsImporter(ctx, name, package, props, parent_values, enum_values,
                           struct_members, object_exists, own_structs)
    imp.resolve_inner = resolve_prop
    globals()["_current_importer"] = imp
    own_fns = {f["name"].lower() for f in own_fields if f["kind"] == "Function"}
    own_names = {f["name"].lower() for f in own_fields}
    imp.function_exists = lambda low: low in own_fns or any(
        low in i.functions for i in ctx.ancestry(sup))
    imp.run(lines)
    globals()["last_unknown_reason"] = imp.unknown_reason
    if imp.failed and not imp.errors_known:
        # An error we saw, but a line we skipped may have logged later: the last
        # message UCC shows is beyond what we can follow.
        return None, [], False, False
    try:
        stored = imp.stored() if imp.values_known and not unknown_parent else None
    except _Unpredictable:
        stored = None
    return stored, imp.errors, imp.failed, imp.errors_known
