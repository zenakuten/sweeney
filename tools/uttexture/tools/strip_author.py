"""Remove a suffix from a built map's Author -- the undo for set_author.py.

    python3 tools/strip_author.py ", 4K by snarf" --dry-run

Same append-at-EOF export rewrite as set_author.py (see its docstring for why
the export table has to move); this one truncates the string instead of growing
it. Written after "--help" was taken as the positional suffix and appended to
all 20 maps; set_author.py now refuses a suffix starting with "-".
"""
import sys, os, glob
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'uttexture'))
from importlib.machinery import SourceFileLoader
sa = SourceFileLoader('sa', os.path.join(HERE, 'set_author.py')).load_module()
from uttexture import ue2

def strip_author(pkg, export, suffix):
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
            r.index()
        if kind == ue2.PT_BOOL:
            ue2._tag_size(r, code); continue
        size = ue2._tag_size(r, code)
        if is_array:
            r.index()
        value_start = r.p
        r.p += size
        if pkg.names[idx] != "Author" or is_array:
            continue
        vr = ue2.Reader(data, value_start)
        length = vr.index()
        if length <= 0:
            return None
        old = data[vr.p:vr.p + length - 1]
        sb = suffix.encode("latin-1")
        if not old.endswith(sb):
            return None
        new = old[:-len(sb)]
        value = sa.put_index(len(new) + 1) + new + b"\0"
        new_code, size_bytes = sa.put_size(len(value))
        tag = sa.put_index(idx) + bytes([(info & 0x8F) | (new_code << 4)]) + size_bytes
        return (data[:tag_start] + tag + value + data[value_start + size:],
                old.decode("latin-1"), new.decode("latin-1"))

sa.rewrite_author = strip_author
suffix = sys.argv[1]
dry = "--dry-run" in sys.argv
n = 0
from sweeney import install_root
for path in sorted(glob.glob(os.path.join(install_root(), 'Maps', '*.ut2'))):
    if sa.patch(path, suffix, dry_run=dry, backup=False):
        n += 1
print("%d map(s) %s" % (n, "would change" if dry else "changed"))
