"""Rewriting UE2 packages: a fuller parse than the reader keeps, plus a writer.

`ue2.Package` is built for reading and throws away what a writer needs -- name
flags, an import's class reference, an export's super and name INDEX (it keeps
the resolved string). This carries all of it, so a package can be taken apart
and put back together.

Layout, as produced here and as UCC produces it:

    [header][name table][export data ...][import table][export table]

Offsets in the header are absolute, so the tables are written last and the
header is patched once their positions are known.
"""

import mmap
import struct

# RF_LoadForClient | RF_LoadForServer | RF_LoadForEdit | RF_TagExp: what
# the engine itself writes for all but a handful of names.
NAME_FLAGS = 0x00070010

TAG = 0x9E2A83C1


def put_index(value):
    """UE2 compact index: sign in bit 7 of the first byte, continue in bit 6."""
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


def put_string(s):
    """A name, stored as a length-prefixed, NUL-terminated latin-1 run."""
    raw = s.encode("latin-1", "replace") + b"\0"
    return put_index(len(raw)) + raw


class RawPackage:
    def __init__(self, path):
        self._fh = open(path, "rb")
        self.data = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        d = self.data
        tag, self.version, self.licensee = struct.unpack_from("<IHH", d, 0)
        if tag != TAG:
            raise ValueError("%s is not a UE2 package" % path)
        (self.flags, name_count, name_off, export_count, export_off,
         import_count, import_off) = struct.unpack_from("<IIIIIII", d, 8)
        self._name_off, self._import_off, self._export_off = (
            name_off, import_off, export_off)
        self.guid = bytes(d[36:52])
        gen_count = struct.unpack_from("<I", d, 52)[0]
        self.generations = [struct.unpack_from("<II", d, 56 + 8 * i)
                            for i in range(gen_count)]

        r = _R(d, name_off)
        self.names = []
        for _ in range(name_count):
            s = r.string()
            self.names.append((s, r.u32()))

        r = _R(d, import_off)
        self.imports = []
        for _ in range(import_count):
            self.imports.append({"class_package": r.index(), "class_name": r.index(),
                                 "outer": r.i32(), "name": r.index()})

        r = _R(d, export_off)
        self.exports = []
        for _ in range(export_count):
            e = {"class_ref": r.index(), "super_ref": r.index(),
                 "outer": r.i32(), "name": r.index(),
                 "flags": r.u32(), "size": r.index()}
            e["offset"] = r.index() if e["size"] > 0 else 0
            self.exports.append(e)
        # Payload is carried separately so an export can be replaced or added
        # without disturbing the others.
        # {export index: [(position in payload, offset of the end of the data)]}
        # for TLazyArray SeekPos fields, which hold an ABSOLUTE file offset and
        # can only be filled in once the payload's place in the file is known.
        self.fixups = {}
        self.payload = [bytes(d[e["offset"]:e["offset"] + e["size"]])
                        if e["size"] > 0 else b"" for e in self.exports]

    # -- names ---------------------------------------------------------------

    def name_index(self, s):
        for i, (n, _f) in enumerate(self.names):
            if n == s:
                return i
        return None

    def ensure_name(self, s, flags=None):
        """The index of a name, appending it with usable flags if it is new.

        The flags are not decoration. ULinkerLoad reads the name table as
        `NameMap.AddItem((Flags & _ContextFlags) ? FName(Name) : FName(NAME_None))`
        (Core/Src/UnLinker.cpp:359), so a name saved with flags 0 comes back as
        NAME_None -- and an export that then has no name, but does have
        RF_Public, trips the check at the top of CreateExport. A package written
        that way loads for nothing and says only "Export.ObjectName != NAME_None".
        """
        i = self.name_index(s)
        if i is not None:
            return i
        self.names.append((s, NAME_FLAGS if flags is None else flags))
        return len(self.names) - 1

    def export_name(self, i):
        return self.names[self.exports[i]["name"]][0]

    # -- writing -------------------------------------------------------------

    def write(self, path, compact=False):
        """Write the package out. Existing payloads keep their file offsets.

        That is not tidiness, it is required. A UE2 texture serialises each mip
        as a TLazyArray, which stores the ABSOLUTE FILE OFFSET of the end of its
        data so a reader can skip it. Nothing in the export table says so, and
        nothing re-derives it: move a texture's payload by a single byte and
        every one of those offsets is wrong. Adding one unused NAME to a
        generated texture package was enough to make the engine assert in
        ULinkerLoad::CreateExport, because the name table sits in front of the
        payloads and growing it shifted all of them.

        So the layout on a rewrite is [original file, truncated after the
        payloads][new payloads][names][imports][exports], with the header
        pointing at the tables wherever they ended up -- the old name table is
        left in place as dead bytes. `compact` rewrites from scratch instead,
        which is only safe when nothing has changed size; the round-trip test
        uses it to prove the parser.
        """
        if compact:
            return self._write_compact(path)

        # Everything up to the first table that follows the payloads: the
        # header, the old name table and every original payload, all still where
        # they were.
        tail = min(self._import_off, self._export_off)
        payload_end = max([tail] + [e["offset"] + e["size"] for e in self.exports
                                    if e["size"] > 0 and e["offset"] > 0])
        out = bytearray(self.data[:payload_end])

        offsets, sizes = [], []
        for e, blob in zip(self.exports, self.payload):
            size = len(blob) if blob else e["size"]
            sizes.append(size)
            if not blob:
                offsets.append(0)
            elif e["offset"] > 0 and size == e.get("slot", e["size"]):
                # It fits where it already is. "slot" is the room an export had
                # before a caller replaced its payload -- comparing against
                # e["size"], which the caller just updated, would always match
                # and would leave the ORIGINAL bytes in place.
                out[e["offset"]:e["offset"] + size] = blob
                offsets.append(e["offset"])
            else:
                offsets.append(len(out))
                out += blob

        for i, fixups in self.fixups.items():
            for field, end in fixups:
                struct.pack_into("<i", out, offsets[i] + field,
                                 offsets[i] + end)

        name_off = len(out)
        for name, flags in self.names:
            out += put_string(name) + struct.pack("<I", flags)
        import_off = len(out)
        for e in self.imports:
            out += (put_index(e["class_package"]) + put_index(e["class_name"])
                    + struct.pack("<i", e["outer"]) + put_index(e["name"]))
        export_off = len(out)
        for e, off, size in zip(self.exports, offsets, sizes):
            out += (put_index(e["class_ref"]) + put_index(e["super_ref"])
                    + struct.pack("<i", e["outer"]) + put_index(e["name"])
                    + struct.pack("<I", e["flags"]) + put_index(size))
            if size > 0:
                out += put_index(off)
        self._finish_header(out, name_off, import_off, export_off)
        with open(path, "wb") as f:
            f.write(out)
        return len(out)

    def _write_compact(self, path):
        head_len = 56 + 8 * len(self.generations)
        out = bytearray(b"\0" * head_len)

        name_off = len(out)
        for s, flags in self.names:
            out += put_string(s) + struct.pack("<I", flags)

        offsets = []
        for blob in self.payload:
            offsets.append(len(out) if blob else 0)
            out += blob

        import_off = len(out)
        for e in self.imports:
            out += (put_index(e["class_package"]) + put_index(e["class_name"])
                    + struct.pack("<i", e["outer"]) + put_index(e["name"]))

        export_off = len(out)
        for e, off in zip(self.exports, offsets):
            out += (put_index(e["class_ref"]) + put_index(e["super_ref"])
                    + struct.pack("<i", e["outer"]) + put_index(e["name"])
                    + struct.pack("<I", e["flags"]) + put_index(e["size"]))
            if e["size"] > 0:
                out += put_index(off)
        self._finish_header(out, name_off, import_off, export_off)
        with open(path, "wb") as f:
            f.write(out)
        return len(out)

    def _finish_header(self, out, name_off, import_off, export_off):
        # The newest generation records how many exports and names the package
        # has; it is what a client checks a downloaded package against, and the
        # engine trusts it over the summary in places.
        if self.generations:
            self.generations[-1] = (len(self.exports), len(self.names))
        struct.pack_into("<IHHIIIIIII", out, 0, TAG, self.version, self.licensee,
                         self.flags, len(self.names), name_off,
                         len(self.exports), export_off,
                         len(self.imports), import_off)
        out[36:52] = self.guid
        struct.pack_into("<I", out, 52, len(self.generations))
        for i, (ec, nc) in enumerate(self.generations):
            struct.pack_into("<II", out, 56 + 8 * i, ec, nc)


class _R:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def u8(self):
        v = self.d[self.p]; self.p += 1; return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]; self.p += 4; return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v

    def index(self):
        b = self.u8()
        negative, value, shift = b & 0x80, b & 0x3F, 6
        if b & 0x40:
            while True:
                c = self.u8()
                value |= (c & 0x7F) << shift
                shift += 7
                if not c & 0x80:
                    break
        return -value if negative else value

    def string(self):
        n = self.index()
        if n == 0:
            return ""
        if n > 0:
            s = self.d[self.p:self.p + n]; self.p += n
            return s.rstrip(b"\0").decode("latin-1")
        n = -n
        s = self.d[self.p:self.p + n * 2]; self.p += n * 2
        return s.decode("utf-16-le").rstrip("\0")
