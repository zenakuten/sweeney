#!/usr/bin/env python3
"""Restore a built map's level text from the map it was rebuilt from.

    python3 tools/fix_level_text.py --dry-run all
    python3 tools/fix_level_text.py DM-Elucidation

UT2004 colours a string with an ESC byte followed by three RGB bytes
(\\x1b\\xff\\x01\\x01 = bright red). A .t3d is a TEXT file, so UCC's exporter
writes that as the literal `(#FF0101)` -- and the importer does not convert it
back, so the rebuilt map shows the markup instead of the colour. It is not
recoverable from the .t3d, because `(#FF0101)` is also a legal thing for an
author to type.

So the text is copied byte-for-byte out of the source map instead, straight into
the built package, using the same trick as set_author.py: the rewritten export is
appended after the import table and the export table moves to the end, so no
existing offset moves. Author is deliberately NOT touched -- set_author.py owns
that, and it has a suffix the source map does not have.
"""

import glob
import json
import os
import struct
import sys

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)
from uttexture.sweeney import install_root, work_root   # noqa: E402
ROOT = work_root()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INSTALL = install_root()

from uttexture import ue2                                       # noqa: E402
from set_author import put_index, put_size                 # noqa: E402

FIELDS = ("Title", "Description", "Author")


def merge_author(source, built):
    """Source Author, keeping a credit the built map already had appended.

    A .t3d writes strings as Author="...", so an embedded DOUBLE QUOTE ends the
    value early and the rest is lost: DM-Grendelkeep's 'Scott "Goose" McGregor'
    imported as 'Scott '. The source is the truth, but the built map may already
    carry a ", 4K by snarf" that set_author.py added, and that must survive.
    """
    if built == source or built.startswith(source):
        return built or source
    n = 0
    while n < len(source) and n < len(built) and source[n] == built[n]:
        n += 1
    tail = built[n:]
    return source + tail if tail.startswith(", ") else source


def string_props(pkg, export):
    """{name: (tag_start, tag_end, value_start, size, info, name_index)}."""
    data = bytes(pkg.data[export["offset"]:export["offset"] + export["size"]])
    r = ue2.Reader(data, 0)
    if export["flags"] & ue2.RF_HAS_STACK:
        node = r.index(); r.index(); r.skip(8); r.skip(4)
        if node:
            r.index()
    found = {}
    while True:
        tag_start = r.p
        idx = r.index()
        if not (0 <= idx < len(pkg.names)) or pkg.names[idx] == "None":
            return data, found
        info = r.u8()
        kind, code, is_array = info & 0x0F, (info >> 4) & 0x07, info & 0x80
        if kind == ue2.PT_STRUCT:
            r.index()                                      # struct name
        if kind == ue2.PT_BOOL:
            ue2._tag_size(r, code)
            continue
        size = ue2._tag_size(r, code)
        if is_array:
            # The element index is written AFTER the size and BEFORE the value
            # (ue2._read_properties). Recording value_start before consuming it
            # desyncs the walk and it runs off the end of the export.
            r.index()
        value_start = r.p
        r.p += size
        if not is_array:
            found.setdefault(pkg.names[idx], (tag_start, value_start, size, info, idx))


def read_text(pkg, export, field):
    data, props = string_props(pkg, export)
    if field not in props:
        return None
    _ts, value_start, size, _info, _idx = props[field]
    r = ue2.Reader(data, value_start)
    n = r.index()
    if n <= 0:
        return None
    return data[r.p:r.p + n - 1]


def rebuild_export(pkg, export, replacements):
    """Export bytes with each {field: raw text} replaced, or None if unchanged."""
    data, props = string_props(pkg, export)
    edits = []
    for field, text in replacements.items():
        if field not in props:
            continue
        tag_start, value_start, size, info, idx = props[field]
        r = ue2.Reader(data, value_start)
        n = r.index()
        current = data[r.p:r.p + n - 1] if n > 0 else b""
        if current == text:
            continue
        value = put_index(len(text) + 1) + text + b"\0"
        code, size_bytes = put_size(len(value))
        tag = put_index(idx) + bytes([(info & 0x8F) | (code << 4)]) + size_bytes
        edits.append((tag_start, value_start + size, tag + value))
    if not edits:
        return None
    out, prev = bytearray(), 0
    for start, end, blob in sorted(edits):
        out += data[prev:start] + blob
        prev = end
    out += data[prev:]
    return bytes(out)


def write_back(path, pkg, changed):
    """changed: {export index: new bytes}. Appends them and moves the table."""
    _f, _nc, _no, ec, eo, _ic, _io = struct.unpack_from("<IIIIIII", pkg.data, 8)
    head = bytes(pkg.data[:eo])
    r = ue2.Reader(bytes(pkg.data[eo:]), 0)
    entries = []
    for _ in range(ec):
        cls = r.index(); sup = r.index(); outer = r.i32(); obj = r.index()
        flags = r.u32(); size = r.index()
        offset = r.index() if size > 0 else 0
        entries.append([cls, sup, outer, obj, flags, size, offset])
    pkg.data.close(); pkg._fh.close()

    appended = bytearray()
    for i, blob in changed.items():
        entries[i][6] = eo + len(appended)
        entries[i][5] = len(blob)
        appended += blob
    table = bytearray()
    for cls, sup, outer, obj, flags, size, offset in entries:
        table += (put_index(cls) + put_index(sup) + struct.pack("<i", outer)
                  + put_index(obj) + struct.pack("<I", flags) + put_index(size))
        if size > 0:
            table += put_index(offset)
    out = bytearray(head + appended + table)
    struct.pack_into("<I", out, 8 + 16, eo + len(appended))
    tmp = path + ".tmp"
    open(tmp, "wb").write(out)
    os.replace(tmp, path)


def fix(source_path, built_path, dry_run=False):
    src = ue2.Package(source_path)
    want = {}
    for export in src.exports:
        if src.class_of(export) not in ("LevelInfo", "LevelSummary"):
            continue
        for field in FIELDS:
            text = read_text(src, export, field)
            if text is not None:
                want.setdefault(field, text)
    src.data.close(); src._fh.close()

    pkg = ue2.Package(built_path)
    if "Author" in want:
        for export in pkg.exports:
            if pkg.class_of(export) not in ("LevelInfo", "LevelSummary"):
                continue
            now = read_text(pkg, export, "Author")
            if now is not None:
                want["Author"] = merge_author(
                    want["Author"].decode("latin-1"),
                    now.decode("latin-1")).encode("latin-1", "replace")
                break
    changed, report = {}, []
    for i, export in enumerate(pkg.exports):
        if pkg.class_of(export) not in ("LevelInfo", "LevelSummary"):
            continue
        blob = rebuild_export(pkg, export, want)
        if blob:
            changed[i] = blob
            for field in FIELDS:
                now = read_text(pkg, export, field)
                if now is not None and field in want and now != want[field]:
                    report.append("%s.%s" % (pkg.class_of(export), field))
    name = os.path.basename(built_path)
    if not changed:
        print("  %-26s already matches the source" % name)
        pkg.data.close(); pkg._fh.close()
        return False
    print("  %-26s restoring %s" % (name, ", ".join(report) or "text"))
    if dry_run:
        pkg.data.close(); pkg._fh.close()
        return True
    write_back(built_path, pkg, changed)
    return True


def main():
    names = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not names or names == ["all"]:
        names = [os.path.basename(os.path.dirname(c)) for c in
                 sorted(glob.glob(os.path.join(ROOT, "maps", "*", "config.json")))]
    n = 0
    for name in names:
        cfg = os.path.join(ROOT, "maps", name, "config.json")
        if not os.path.exists(cfg):
            continue
        built = os.path.join(INSTALL, "Maps",
                             json.load(open(cfg))["map_name"] + ".ut2")
        source = os.path.join(INSTALL, "Maps", name + ".ut2")
        if not (os.path.exists(built) and os.path.exists(source)):
            continue
        if fix(source, built, dry_run=dry):
            n += 1
    print("%d map(s) %s" % (n, "would change" if dry else "fixed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
