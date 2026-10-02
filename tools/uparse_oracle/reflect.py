#!/usr/bin/env python3
"""Read the compiled script objects out of a UT2004 .u package.

This is oracle 3: what UCC actually produced for every class -- its properties,
functions, states, structs, enums and consts, with their flags and types, and the
class defaults -- to diff against what the parser predicts from the .uc.

    reflect.py Core.u                      summary of every class
    reflect.py Engine.u --class Actor      one class in full
    reflect.py Engine.u --json             everything, as JSON
    reflect.py --verify System/*.u         check every script object parses exactly

The layouts are for package version 128 / licensee 29 (UT2004), taken from the
public UELib library (github.com/EliotVU/Unreal-Library, MIT) and checked
mechanically: each export records its size, and the reader must consume exactly
that many bytes. --verify runs that check over whole packages.

Function bodies are bytecode, decoded into a small tree (see decode_script). UE2
does not record a body's size on disk -- object and name references inside it are
compact indices, so its length varies -- which means the body has to be decoded to
get past it, even when only the declarations are wanted.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "uttexture"))
from uttexture.ue2 import Package, Reader, _read_properties, RF_HAS_STACK  # noqa: E402

# ---------------------------------------------------------------- flags

PROPERTY_FLAGS = {
    0x1: "edit", 0x2: "const", 0x4: "input", 0x8: "exportobject", 0x10: "optional",
    0x20: "net", 0x40: "editconstarray", 0x80: "parm", 0x100: "out", 0x200: "skip",
    0x400: "return", 0x800: "coerce", 0x1000: "native", 0x2000: "transient",
    0x4000: "config", 0x8000: "localized", 0x10000: "travel", 0x20000: "editconst",
    0x40000: "globalconfig", 0x100000: "ondemand", 0x200000: "new",
    0x400000: "needctorlink", 0x800000: "noexport", 0x1000000: "button",
    0x2000000: "commentstring", 0x4000000: "editinline", 0x8000000: "edfindable",
    0x10000000: "editinlineuse", 0x20000000: "deprecated", 0x40000000: "editinlinenotify",
    0x80000000: "automated",
}
FUNCTION_FLAGS = {
    0x1: "final", 0x2: "defined", 0x4: "iterator", 0x8: "latent", 0x10: "preoperator",
    0x20: "singular", 0x40: "net", 0x80: "netreliable", 0x100: "simulated",
    0x200: "exec", 0x400: "native", 0x800: "event", 0x1000: "operator",
    0x2000: "static", 0x4000: "noexport", 0x8000: "const", 0x10000: "invariant",
    0x20000: "public", 0x40000: "private", 0x80000: "protected", 0x100000: "delegate",
}
CLASS_FLAGS = {
    0x1: "abstract", 0x2: "compiled", 0x4: "config", 0x8: "transient", 0x10: "parsed",
    0x20: "localized", 0x40: "safereplace", 0x80: "runtimestatic", 0x100: "noexport",
    0x200: "placeable", 0x400: "perobjectconfig", 0x800: "nativereplication",
    0x1000: "editinlinenew", 0x2000: "collapsecategories", 0x4000: "exportstructs",
    0x200000: "instanced", 0x400000: "hidedropdown", 0x800000: "cacheexempt",
    # Set by the engine's C++ on native classes such as Inventory, GameInfo and
    # Mutator, and inherited; UELib calls it Cacheable.
    0x2000000: "cacheable",
}
STATE_FLAGS = {0x1: "editable", 0x2: "auto", 0x4: "simulated"}
STRUCT_FLAGS = {0x1: "native", 0x2: "export", 0x4: "long", 0x8: "init"}

CPF_NET = 0x20
FUNC_NET = 0x40


def flag_names(value: int, table: dict) -> list[str]:
    names = [n for bit, n in table.items() if value & bit]
    unknown = value & ~sum(table)
    if unknown:
        names.append(f"0x{unknown:x}")
    return names


# ---------------------------------------------------------------- bytecode

OBJ_SIZE = 4    # in-memory size of an object reference in the script
NAME_SIZE = 4   # in-memory size of a name

EX_END_FUNCTION_PARMS = 0x16
EXTENDED_NATIVE = 0x60
FIRST_NATIVE = 0x70

# Operand layout of every non-native token. Letters, read in order:
#   e  an expression (a nested token)    o  object reference   n  name
#   b  byte    w  uint16    i  int32    f  float    v  12-byte vector/rotator
#   s  zero-terminated ANSI string       u  zero-terminated UTF-16 string
#   P  call parameters, expressions up to EX_EndFunctionParms
#   C  a case label: uint16, then an expression unless it is 0xFFFF (default:)
#   L  a label table: (name, int32) pairs ending at the name None
#   D  debug info: int32 version, int32 line, int32 textpos, byte opcode
TOKENS = {
    0x00: ("LocalVariable", "o"), 0x01: ("InstanceVariable", "o"),
    0x02: ("DefaultVariable", "o"), 0x04: ("Return", "e"), 0x05: ("Switch", "be"),
    0x06: ("Jump", "w"), 0x07: ("JumpIfNot", "we"), 0x08: ("Stop", ""),
    0x09: ("Assert", "we"), 0x0A: ("Case", "C"), 0x0B: ("Nothing", ""),
    0x0C: ("LabelTable", "L"), 0x0D: ("GotoLabel", "e"), 0x0E: ("EatString", "e"),
    0x0F: ("Let", "ee"), 0x10: ("DynArrayElement", "ee"), 0x11: ("New", "eeee"),
    0x12: ("ClassContext", "ewbe"), 0x13: ("MetaCast", "oe"), 0x14: ("LetBool", "ee"),
    0x15: ("LineNumber", "we"), 0x16: ("EndFunctionParms", ""), 0x17: ("Self", ""),
    0x18: ("Skip", "we"), 0x19: ("Context", "ewbe"), 0x1A: ("ArrayElement", "ee"),
    0x1B: ("VirtualFunction", "nP"), 0x1C: ("FinalFunction", "oP"),
    0x1D: ("IntConst", "i"), 0x1E: ("FloatConst", "f"), 0x1F: ("StringConst", "s"),
    0x20: ("ObjectConst", "o"), 0x21: ("NameConst", "n"), 0x22: ("RotationConst", "v"),
    0x23: ("VectorConst", "v"), 0x24: ("ByteConst", "b"), 0x25: ("IntZero", ""),
    0x26: ("IntOne", ""), 0x27: ("True", ""), 0x28: ("False", ""),
    0x29: ("NativeParm", "o"), 0x2A: ("NoObject", ""), 0x2C: ("IntConstByte", "b"),
    0x2D: ("BoolVariable", "e"), 0x2E: ("DynamicCast", "oe"), 0x2F: ("Iterator", "ew"),
    0x30: ("IteratorPop", ""), 0x31: ("IteratorNext", ""), 0x32: ("StructCmpEq", "oee"),
    0x33: ("StructCmpNe", "oee"), 0x34: ("UnicodeStringConst", "u"),
    0x36: ("StructMember", "oe"), 0x37: ("DynArrayLength", "e"),
    0x38: ("GlobalFunction", "nP"), 0x39: ("PrimitiveCast", "be"),
    0x3A: ("ReturnNothing", ""), 0x3B: ("DelegateCmpEq", "eee"),
    0x3C: ("DelegateCmpNe", "eee"), 0x3D: ("DelegateFunctionCmpEq", "eee"),
    0x3E: ("DelegateFunctionCmpNe", "eee"), 0x3F: ("EmptyDelegate", ""),
    0x40: ("DynArrayInsert", "eee"), 0x41: ("DynArrayRemove", "eee"),
    0x42: ("DebugInfo", "D"), 0x43: ("DelegateFunction", "onP"),
    0x44: ("DelegateProperty", "n"), 0x45: ("LetDelegate", "ee"),
    0x47: ("EndOfScript", ""), 0x48: ("Conditional", "ewewe"),
}


class ScriptError(Exception):
    pass


class _Script:
    """Decodes one bytecode body. `mem` tracks the in-memory offset, which is what
    jump targets and the stored script size are measured in."""

    def __init__(self, pkg: Package, r: Reader):
        self.pkg, self.r, self.mem = pkg, r, 0

    def ref(self) -> str | None:
        self.mem += OBJ_SIZE
        return self.pkg.ref_path(self.r.index())

    def name(self) -> str:
        self.mem += NAME_SIZE
        return self.pkg.names[self.r.index()]

    def fixed(self, fmt: str, size: int):
        v = struct.unpack_from(fmt, self.r.d, self.r.p)
        self.r.p += size
        self.mem += size
        return v if len(v) > 1 else v[0]

    def cstring(self, wide: bool) -> str:
        d, p = self.r.d, self.r.p
        if wide:
            end = p
            while d[end:end + 2] != b"\0\0":
                end += 2
            s = bytes(d[p:end]).decode("utf-16-le")
            n = end + 2 - p
        else:
            end = d.find(b"\0", p)
            s = bytes(d[p:end]).decode("latin-1")
            n = end + 1 - p
        self.r.p += n
        self.mem += n
        return s

    def token(self) -> list:
        """[mem offset, name, operands...]; nested tokens are lists too."""
        at = self.mem
        op = self.fixed("<B", 1)
        if op >= EXTENDED_NATIVE:
            if op < FIRST_NATIVE:
                index = ((op - EXTENDED_NATIVE) << 8) | self.fixed("<B", 1)
            else:
                index = op
            return [at, f"Native{index}", self.parms()]
        if op not in TOKENS:
            raise ScriptError(f"bad token 0x{op:02x} at script offset {at}")
        name, layout = TOKENS[op]
        out: list = [at, name]
        for c in layout:
            if c == "e":
                out.append(self.token())
            elif c == "o":
                out.append(self.ref())
            elif c == "n":
                out.append(self.name())
            elif c == "b":
                out.append(self.fixed("<B", 1))
            elif c == "w":
                out.append(self.fixed("<H", 2))
            elif c == "i":
                out.append(self.fixed("<i", 4))
            elif c == "f":
                out.append(self.fixed("<f", 4))
            elif c == "v":
                out.append(list(self.fixed("<3f" if name == "VectorConst" else "<3i", 12)))
            elif c == "s":
                out.append(self.cstring(False))
            elif c == "u":
                out.append(self.cstring(True))
            elif c == "P":
                out.append(self.parms())
            elif c == "C":
                target = self.fixed("<H", 2)
                out.append(target)
                if target != 0xFFFF:
                    out.append(self.token())
            elif c == "L":
                labels = []
                while True:
                    label = self.name()
                    offset = self.fixed("<i", 4)
                    if label == "None":
                        break
                    labels.append([label, offset])
                out.append(labels)
            elif c == "D":
                out.append(list(self.fixed("<iiiB", 13)))
        return out

    def parms(self) -> list:
        args = []
        while True:
            t = self.token()
            if t[1] == "EndFunctionParms":
                return args
            args.append(t)


def decode_script(pkg: Package, r: Reader, size: int) -> list:
    """Decode `size` in-memory bytes of bytecode starting at r; returns the tokens."""
    s = _Script(pkg, r)
    out = []
    while s.mem < size:
        out.append(s.token())
    if s.mem != size:
        raise ScriptError(f"script overran: decoded {s.mem} of {size} bytes")
    return out


# ---------------------------------------------------------------- objects

PROPERTY_CLASSES = {
    "ByteProperty", "IntProperty", "BoolProperty", "FloatProperty", "ObjectProperty",
    "ClassProperty", "NameProperty", "StrProperty", "StringProperty", "StructProperty",
    "ArrayProperty", "FixedArrayProperty", "MapProperty", "DelegateProperty",
    "PointerProperty",
}
STRUCT_CLASSES = {"Struct", "Function", "State", "Class"}


def _names(pkg: Package, r: Reader) -> list[str]:
    return [pkg.names[r.index()] for _ in range(r.index())]


def read_export(pkg: Package, i: int) -> dict:
    """Decode export i. Raises if the bytes don't parse to exactly its size."""
    e = pkg.exports[i]
    cls = pkg.class_of(e)
    if e["size"] == 0:                        # an empty slot in the export table
        return {"name": e["name"], "kind": cls, "path": "", "unparsed": True}
    r = Reader(pkg.data, e["offset"])
    end = e["offset"] + e["size"]
    out: dict = {"name": e["name"], "kind": cls, "path": pkg.export_path(i)}

    if cls not in STRUCT_CLASSES | PROPERTY_CLASSES | {"Enum", "Const", "TextBuffer"}:
        return {"name": e["name"], "kind": cls, "path": out["path"], "unparsed": True}
    if e["flags"] & RF_HAS_STACK:
        node = r.index(); r.index(); r.skip(8); r.skip(4)
        if node:
            r.index()
    if e["class_ref"] != 0:
        _read_properties(pkg, r)              # a script object's own: just "None"

    if cls == "TextBuffer":                   # a plain object, not a field
        r.u32(); r.u32()                      # Top, Pos
        out["text_len"] = len(r.string())
        if r.p != end:
            raise ScriptError(f"{out['path']} (TextBuffer): read {r.p - e['offset']} of "
                              f"{e['size']} bytes")
        return out
    if cls not in STRUCT_CLASSES | PROPERTY_CLASSES | {"Enum", "Const"}:
        # Not a script object (a texture, a sound, a defaults subobject...).
        out["unparsed"] = True
        return out

    out["super"] = pkg.ref_path(r.index())
    out["next"] = pkg.ref_path(r.index())

    if cls in STRUCT_CLASSES:
        r.index()                             # ScriptText
        out["children"] = pkg.ref_path(r.index())
        out["friendly_name"] = pkg.names[r.index()]
        sflags = r.u32()
        out["struct_flags"] = flag_names(sflags, STRUCT_FLAGS)
        out["line"] = r.i32()
        out["text_pos"] = r.i32()
        size = r.i32()
        out["script_size"] = size
        out["script"] = decode_script(pkg, r, size) if size else []

    if cls == "Function":
        out["native"] = r.u16()
        out["precedence"] = r.u8()
        flags = r.u32()
        out["flags"] = flag_names(flags, FUNCTION_FLAGS)
        out["rep_offset"] = r.u16() if flags & FUNC_NET else None
    elif cls in ("State", "Class"):
        out["probe_mask"] = struct.unpack_from("<Q", pkg.data, r.p)[0]; r.skip(8)
        out["ignore_mask"] = struct.unpack_from("<Q", pkg.data, r.p)[0]; r.skip(8)
        out["label_table"] = r.u16()
        out["state_flags"] = flag_names(r.u32(), STATE_FLAGS)
    if cls == "Class":
        out["class_flags"] = flag_names(r.u32(), CLASS_FLAGS)
        r.skip(16)                            # GUID
        deps = []
        for _ in range(r.index()):
            dep = pkg.ref_path(r.index()); deep = r.u32(); r.u32()   # ScriptTextCRC
            deps.append([dep, bool(deep)])
        out["dependencies"] = deps
        out["package_imports"] = _names(pkg, r)
        out["within"] = pkg.ref_path(r.index())
        out["config"] = pkg.names[r.index()]
        out["hide_categories"] = _names(pkg, r)
        out["defaults"] = _defaults(pkg, r)
        if r.p != end:
            # Defaults are the last thing in a class. A struct or array value whose
            # tag size is smaller than its data (seen in 2 of ~5000 classes, from
            # both Epic's compiler and the 3374 UCC) desyncs a size-trusting read.
            # The declarations above are unaffected, so keep them and flag this.
            out["defaults_desync"] = end - r.p
            r.p = end
    elif cls == "Enum":
        out["values"] = _names(pkg, r)
    elif cls == "Const":
        out["value"] = r.string()
    elif cls in PROPERTY_CLASSES:
        out["array_dim"] = r.i32()
        flags = r.u32()
        out["flags"] = flag_names(flags, PROPERTY_FLAGS)
        out["category"] = pkg.names[r.index()]
        out["rep_offset"] = r.u16() if flags & CPF_NET else None
        if cls in ("ByteProperty",):
            out["enum"] = pkg.ref_path(r.index())
        elif cls in ("ObjectProperty", "StructProperty", "DelegateProperty"):
            out["type"] = pkg.ref_path(r.index())
        elif cls == "ArrayProperty":
            out["inner"] = pkg.ref_path(r.index())   # the element's own property object
        elif cls == "ClassProperty":
            out["type"] = pkg.ref_path(r.index())
            out["meta_class"] = pkg.ref_path(r.index())
        elif cls == "MapProperty":
            out["key"] = pkg.ref_path(r.index()); out["value"] = pkg.ref_path(r.index())
        elif cls == "FixedArrayProperty":
            out["inner"] = pkg.ref_path(r.index()); out["count"] = r.index()

    if r.p != end:
        raise ScriptError(f"{out['path']} ({cls}): read {r.p - e['offset']} of "
                          f"{e['size']} bytes")
    return out


def _defaults(pkg: Package, r: Reader) -> dict:
    """Class defaults as {name: [[array index, kind, hex bytes, struct name]]}, raw.

    decode_defaults() turns these into values; the raw form stays because decoding
    an array needs its element type, which may live in another package.
    """
    props = _read_properties(pkg, r)
    return {k: [[p.index, p.kind, p.raw.hex(), p.struct_name] for p in v]
            for k, v in props.items()}


# Tagged-property kinds (the low 4 bits of a tag's info byte).
K_BYTE, K_INT, K_BOOL, K_FLOAT, K_OBJECT, K_NAME = 1, 2, 3, 4, 5, 6
K_ARRAY, K_STRUCT, K_STR = 9, 10, 13

# Structs stored as raw binary rather than as nested tagged properties.
BINARY_STRUCTS = {
    "vector": ("<3f", ("X", "Y", "Z")),
    "rotator": ("<3i", ("Pitch", "Yaw", "Roll")),
    "color": ("<4B", ("B", "G", "R", "A")),
}


def decode_value(pkg: Package, kind: int, struct_name: str | None, raw: bytes,
                 inner: dict | None = None):
    """One stored default as a JSON-friendly value.

    Names and strings are typed (["name", "Foo"], ["str", "Foo"]) so a name that
    came out as a lone apostrophe can't be mistaken for anything else. Values that
    can't be decoded yet come back as ["raw", kind, hex].
    """
    r = Reader(raw)
    if kind == K_BYTE:
        return raw[0]
    if kind == K_INT:
        return struct.unpack("<i", raw)[0]
    if kind == K_BOOL:
        return bool(raw[0])
    if kind == K_FLOAT:
        return round(struct.unpack("<f", raw)[0], 6)
    if kind == K_OBJECT:
        return ["obj", pkg.ref_path(r.index())]
    if kind == K_NAME:
        return ["name", pkg.names[r.index()]]
    if kind == K_STR:
        return ["str", r.string()]
    if kind == K_STRUCT:
        layout = BINARY_STRUCTS.get((struct_name or "").lower())
        if layout and len(raw) == struct.calcsize(layout[0]):
            vals = struct.unpack(layout[0], raw)
            return {k.lower(): (round(v, 6) if isinstance(v, float) else v)
                    for k, v in zip(layout[1], vals)}
        try:
            props = _read_properties(pkg, r)
            if r.p == len(raw):
                return {k.lower(): decode_value(pkg, p.kind, p.struct_name, p.raw)
                        for k, v in props.items() for p in v[:1]}
        except (IndexError, struct.error):
            pass
    if kind == K_ARRAY and inner:
        try:
            out, n = [], r.index()
            for _ in range(n):
                if inner["kind"] == "NameProperty":
                    out.append(["name", pkg.names[r.index()]])
                elif inner["kind"] == "StrProperty":
                    out.append(["str", r.string()])
                elif inner["kind"] == "IntProperty":
                    out.append(r.i32())
                elif inner["kind"] in ("ObjectProperty", "ClassProperty"):
                    out.append(["obj", pkg.ref_path(r.index())])
                elif inner["kind"] == "ByteProperty":
                    out.append(r.u8())
                elif inner["kind"] == "FloatProperty":
                    out.append(round(struct.unpack_from("<f", raw, r.p)[0], 6)); r.p += 4
                else:
                    raise ValueError(inner["kind"])
            if r.p == len(raw):
                return out
        except (IndexError, ValueError, struct.error):
            pass
    return ["raw", kind, raw.hex()]


def decode_defaults(pkg: Package, objects: dict, class_path: str) -> dict:
    """{property: {array index: value}} for a class's stored defaults.

    Property and struct member names are lowercased. UE2 names are case-insensitive
    and a package's name table keeps whichever spelling it saw first (a member
    declared `I` can come back as `i`), so the case says nothing about the source.
    Name *values* keep their case.

    Array element types come from the property declarations found in this package
    (the class and its supers here); an array declared in another package stays raw.
    """
    decl = {}
    path = class_path
    while path in objects:
        for f in fields_of(pkg, objects, objects[path].get("children")):
            decl.setdefault(f["name"].lower(), f)
        path = objects[path].get("super")
    out = {}
    for name, vals in objects[class_path].get("defaults", {}).items():
        f = decl.get(name.lower())
        inner = objects.get(f.get("inner")) if f and f["kind"] == "ArrayProperty" else None
        for index, kind, hexraw, struct_name in vals:
            out.setdefault(name.lower(), {})[str(index)] = decode_value(
                pkg, kind, struct_name, bytes.fromhex(hexraw), inner)
    return out


# ---------------------------------------------------------------- the class view

def fields_of(pkg: Package, objects: dict, head: str | None) -> list[dict]:
    """Walk a Children / NextField chain into a list of field dicts."""
    out, seen = [], set()
    while head and head in objects and head not in seen:
        seen.add(head)
        f = objects[head]
        out.append(f)
        head = f.get("next")
    return out


def class_view(pkg: Package, objects: dict, path: str) -> dict:
    c = objects[path]
    view = {k: c[k] for k in ("name", "super", "class_flags", "within", "config",
                              "hide_categories", "dependencies", "state_flags")}
    view["fields"] = [_field_view(pkg, objects, f) for f in fields_of(pkg, objects, c["children"])]
    view["defaults"] = c["defaults"]
    return view


def _field_view(pkg: Package, objects: dict, f: dict) -> dict:
    v = {"name": f["name"], "kind": f["kind"]}
    for k in ("flags", "array_dim", "category", "enum", "type", "meta_class", "inner",
              "count", "key", "value", "values", "native", "precedence", "struct_flags",
              "state_flags", "super", "rep_offset"):
        if k in f and f[k] not in (None, [], ""):
            v[k] = f[k]
    if f["kind"] in ("Function", "State", "Struct"):
        v["fields"] = [_field_view(pkg, objects, g) for g in fields_of(pkg, objects, f["children"])]
        if f.get("script_size"):
            v["script_size"] = f["script_size"]
    return v


def load(path: Path) -> tuple[Package, dict, list[str]]:
    """Every script object in the package, keyed by full path, plus errors."""
    pkg = Package(str(path))
    objects, errors = {}, []
    for i in range(len(pkg.exports)):
        try:
            o = read_export(pkg, i)
        except (ScriptError, IndexError, struct.error, UnicodeDecodeError) as ex:
            errors.append(f"{pkg.export_path(i)} ({pkg.class_of(pkg.exports[i])}): {ex}")
            continue
        if not o.get("unparsed"):
            objects[f"{pkg.name}.{o['path']}"] = o
    return pkg, objects, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("packages", nargs="+", type=Path)
    ap.add_argument("--class", dest="cls", help="show one class in full")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="check every script object parses to exactly its size")
    a = ap.parse_args()

    if a.verify:
        total_obj = total_err = 0
        for p in a.packages:
            _, objects, errors = load(p)
            total_obj += len(objects)
            total_err += len(errors)
            print(f"{p.name:32s} {len(objects):6d} objects  {len(errors):4d} errors")
            for e in errors[:5]:
                print("    " + e)
        print(f"{'TOTAL':32s} {total_obj:6d} objects  {total_err:4d} errors")
        return 1 if total_err else 0

    for p in a.packages:
        pkg, objects, errors = load(p)
        classes = [k for k, o in objects.items() if o["kind"] == "Class"]
        if a.cls:
            classes = [k for k in classes if objects[k]["name"].lower() == a.cls.lower()]
        views = {k: class_view(pkg, objects, k) for k in classes}
        if a.json or a.cls:
            print(json.dumps(views, indent=1))
        else:
            for k, v in views.items():
                kinds = {}
                for f in v["fields"]:
                    kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
                summary = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items()))
                print(f"{k}  extends {v['super']}  [{' '.join(v['class_flags'])}]  {summary}")
        for e in errors:
            print("error: " + e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
