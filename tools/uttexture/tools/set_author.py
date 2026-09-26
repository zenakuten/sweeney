#!/usr/bin/env python3
"""Append a credit to a built map's Author, without rebuilding the map.

    python3 tools/set_author.py --dry-run ", 4K by snarf" all
    python3 tools/set_author.py ", 4K by snarf" DM-Corrugation

`Author` lives on both LevelInfo and LevelSummary as a tagged FString. Making it
longer changes that export's size, and a UE2 package stores export DATA before
the import and export tables -- so growing an export in place would shift every
later export, and with them every TLazyArray skip offset, which is an ABSOLUTE
file position embedded in each texture export. That corrupts the file.

Export data does not have to be contiguous or in order, though: the export table
gives each object its own offset and size. So the rewritten exports are appended
after the import table and the export table is moved to the end of the file:

    [header][names][export data ............][imports][new exports][export table]
     ^ name/import offsets unchanged          ^ unchanged           ^ header updated

Every original offset stays valid, the old bytes become a hole the loader never
visits, and only the header's export-table offset and the two moved entries
change. The editor rewrites the file compactly the next time it saves.
"""

import glob
import json
import os
import shutil
import struct
import sys

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)
from uttexture.sweeney import install_root, work_root   # noqa: E402
ROOT = work_root()
INSTALL = install_root()

from uttexture import ue2                                       # noqa: E402

FIXED = {0: 1, 1: 2, 2: 4, 3: 12, 4: 16}


def put_index(value):
    out = bytearray()
    negative = value < 0
    value = abs(value)
    first = (0x80 if negative else 0) | (value & 0x3F)
    value >>= 6
    if value:
        first |= 0x40
    out.append(first)
    while value:
        b = value & 0x7F
        value >>= 7
        if value:
            b |= 0x80
        out.append(b)
    return bytes(out)


def put_size(size):
    for code, fixed in FIXED.items():
        if fixed == size:
            return code, b""
    if size <= 0xFF:
        return 5, bytes([size])
    if size <= 0xFFFF:
        return 6, struct.pack("<H", size)
    return 7, struct.pack("<i", size)


def rewrite_author(pkg, export, suffix):
    """(new export bytes, old text, new text) or None if there is nothing to do."""
    data = bytes(pkg.data[export["offset"]:export["offset"] + export["size"]])
    r = ue2.Reader(data, 0)
    if export["flags"] & ue2.RF_HAS_STACK:
        node = r.index(); r.index(); r.skip(8); r.skip(4)
        if node:
            r.index()
    while True:
        tag_start = r.p
        idx = r.index()
        if not (0 <= idx < len(pkg.names)) or pkg.names[idx] == "None":
            return None
        info = r.u8()
        kind, code, is_array = info & 0x0F, (info >> 4) & 0x07, info & 0x80
        if kind == ue2.PT_STRUCT:
            r.index()                                      # struct name
        if kind == ue2.PT_BOOL:                                      # bool: size, no value
            ue2._tag_size(r, code)
            continue
        size = ue2._tag_size(r, code)
        if is_array:
            r.index()                                      # array element index
        value_start = r.p
        r.p += size
        if pkg.names[idx] != "Author" or is_array:
            continue
        vr = ue2.Reader(data, value_start)
        length = vr.index()
        if length <= 0:                                    # empty, or UTF-16
            return None
        old = data[vr.p:vr.p + length - 1]
        if old.endswith(suffix.encode("latin-1")):         # already credited
            return None
        new = old + suffix.encode("latin-1", "replace")
        value = put_index(len(new) + 1) + new + b"\0"
        new_code, size_bytes = put_size(len(value))
        tag = put_index(idx) + bytes([(info & 0x8F) | (new_code << 4)]) + size_bytes
        return (data[:tag_start] + tag + value + data[value_start + size:],
                old.decode("latin-1"), new.decode("latin-1"))


def patch(path, suffix, dry_run=False, backup=True):
    pkg = ue2.Package(path)
    targets = []
    for i, export in enumerate(pkg.exports):
        if pkg.class_of(export) not in ("LevelInfo", "LevelSummary"):
            continue
        done = rewrite_author(pkg, export, suffix)
        if done:
            targets.append((i, done))
    name = os.path.basename(path)
    if not targets:
        print("  %-26s nothing to change" % name)
        pkg.data.close(); pkg._fh.close()
        return False
    print("  %-26s %s -> %s" % (name, repr(targets[0][1][1]), repr(targets[0][1][2])))
    if dry_run:
        pkg.data.close(); pkg._fh.close()
        return True

    _flags, _nc, _no, ec, eo, _ic, _io = struct.unpack_from("<IIIIIII", pkg.data, 8)
    head = bytes(pkg.data[:eo])                            # header, names, data, imports
    r = ue2.Reader(bytes(pkg.data[eo:]), 0)
    entries = []
    for _ in range(ec):
        cls = r.index(); sup = r.index(); outer = r.i32(); obj = r.index()
        flags = r.u32(); size = r.index()
        offset = r.index() if size > 0 else 0
        entries.append([cls, sup, outer, obj, flags, size, offset])
    pkg.data.close(); pkg._fh.close()

    appended = bytearray()
    for i, (blob, _old, _new) in targets:
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
    struct.pack_into("<I", out, 8 + 16, eo + len(appended))   # header ExportOffset
    if backup and not os.path.exists(path + ".bak"):
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    open(tmp, "wb").write(out)
    os.replace(tmp, path)
    return True


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        print(__doc__)
        return 2
    suffix, names = args[0], args[1:]
    # The suffix is positional, so a mistyped or unsupported flag lands here and
    # gets appended to every map's Author -- "--help" once went into all 20.
    if suffix.startswith("-"):
        print("refusing to append %r: a suffix starting with '-' is almost\n"
              "certainly a mistyped flag. Quote it as e.g. \"-- 4K by snarf\"\n"
              "if that is really what you want.\n" % suffix)
        print(__doc__)
        return 2
    if not names or names == ["all"]:
        names = []
        for cfg in sorted(glob.glob(os.path.join(ROOT, "maps", "*", "config.json"))):
            names.append(os.path.basename(os.path.dirname(cfg)))
    changed = 0
    for name in names:
        cfg = os.path.join(ROOT, "maps", name, "config.json")
        out_map = json.load(open(cfg))["map_name"] if os.path.exists(cfg) else name
        path = os.path.join(INSTALL, "Maps", out_map + ".ut2")
        if not os.path.exists(path):
            print("  %-26s not built" % (out_map + ".ut2"))
            continue
        if patch(path, suffix, dry_run=dry):
            changed += 1
    print("%d map(s) %s" % (changed, "would change" if dry else "updated"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
