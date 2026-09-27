"""Carrying a static mesh into another package verbatim.

The mesh used to make a round trip out to an ASE and back so its material slots
could be repointed. That trip is lossy in ways that only show up in game: an ASE
carries triangles, so a collision hull built from quads comes back
re-tessellated, and an OPEN hull -- one whose edges are not each shared by two
faces -- has no well-defined inside, so re-tessellating it changes which side
the engine calls solid. DM-1on1-Roughinery's traeger_512 became four invisible
walls for exactly that reason, in a map with twenty years of play behind it.

Copying the export keeps the bytes, so the geometry cannot change. What has to
be fixed up is only the numbering: names, class references and object references
are package-relative and are remapped into the destination.
"""

import os
import struct

from . import ue2
from .pkgwrite import put_index

PT_OBJECT, PT_NAME, PT_ARRAY = 5, 6, 9   # UE2 EPropertyType; Bool 3, Struct 10

# carry_static_mesh's answer for a mesh whose serialised layout does not walk.
UNWALKABLE = "unwalkable"

_SIZE_CODES = {1: 0, 2: 1, 4: 2, 12: 3, 16: 4}


def put_size(size):
    """(code, bytes) for a property's size field, matching ue2._tag_size."""
    if size in _SIZE_CODES:
        return _SIZE_CODES[size], b""
    if size <= 0xFF:
        return 5, bytes([size])
    if size <= 0xFFFF:
        return 6, struct.pack("<H", size)
    return 7, struct.pack("<i", size)


def names_of(pkg):
    """A plain list of name strings, for either a ue2.Package or a RawPackage.

    RawPackage keeps (name, flags) because it has to write the flags back.
    """
    n = pkg.names
    if n and isinstance(n[0], tuple):
        return [s for s, _f in n]
    return n


def _parse_tagged(names, data, pos):
    """([(name_idx, info, struct_extra, array_extra, value)], pos after None).

    struct_extra precedes the size field and array_extra follows it, which is
    the order the reader takes them -- emitting them together corrupts the tag.
    """
    out = []
    r = ue2.Reader(data, pos)
    while True:
        idx = r.index()
        if not (0 <= idx < len(names)) or names[idx] == "None":
            return out, r.p
        info = r.u8()
        kind, code, is_array = info & 0x0F, (info >> 4) & 0x07, info & 0x80
        sextra = aextra = b""
        if kind == ue2.PT_STRUCT:
            sp = r.p
            r.index()
            sextra = bytes(data[sp:r.p])
        if kind == ue2.PT_BOOL:
            # The value rides in the info byte, but the size field is still
            # written and must still be consumed -- and re-emitted as it was.
            sp = r.p
            ue2._tag_size(r, code)
            sextra += bytes(data[sp:r.p])
            out.append((idx, info, sextra, b"", b""))
            continue
        size = ue2._tag_size(r, code)
        if is_array:
            sp = r.p
            r.index()
            aextra = bytes(data[sp:r.p])
        out.append((idx, info, sextra, aextra, bytes(data[r.p:r.p + size])))
        r.p += size


def _emit_tagged(records, none_idx):
    """Serialise records back, recomputing each size field, and terminate."""
    out = b""
    for idx, info, sextra, aextra, value in records:
        if info & 0x0F == ue2.PT_BOOL:
            out += put_index(idx) + bytes([info]) + sextra
            continue
        code, size_bytes = put_size(len(value))
        out += (put_index(idx) + bytes([(info & 0x8F) | (code << 4)])
                + sextra + size_bytes + aextra + value)
    return out + put_index(none_idx)


def property_list_start(data, base, flags):
    """Offset of an export's tagged property list, relative to its payload."""
    r = ue2.Reader(data, base)
    if flags & ue2.RF_HAS_STACK:
        node = r.index()
        r.index()
        r.skip(8)
        r.skip(4)
        if node:
            r.index()
    return r.p - base


def material_refs(pkg, export):
    """[(slot, ref)] for a mesh's material slots, in order."""
    return material_refs_in(names_of(pkg), pkg.data, export["offset"],
                            export["flags"])


def material_refs_raw(raw, index):
    """The same, for an export of a RawPackage, read out of its payload."""
    e = raw.exports[index]
    return material_refs_in(names_of(raw), raw.payload[index], 0, e["flags"])


def material_refs_in(names, data, base, flags):
    start = base + property_list_start(data, base, flags)
    recs, _end = _parse_tagged(names, data, start)
    out = []
    for idx, info, _sx, _ax, value in recs:
        if names[idx] != "Materials":
            continue
        r = ue2.Reader(value, 0)
        count = r.index()
        pos = r.p
        for slot in range(count):
            elems, pos = _parse_tagged(names, value, pos)
            for ei, einfo, _a, _b, ev in elems:
                if names[ei] == "Material" and (einfo & 0x0F) == PT_OBJECT:
                    out.append((slot, ue2.Reader(ev, 0).index()))
    return out


def reemit_properties(pkg, export):
    """(original, re-emitted) property-list bytes -- a self-test for the codec."""
    names = names_of(pkg)
    plist = property_list_start(pkg.data, export["offset"], export["flags"])
    start = export["offset"] + plist
    recs, end = _parse_tagged(names, pkg.data, start)
    original = bytes(pkg.data[start:end])
    return original, _emit_tagged(recs, names.index("None"))


class Rewriter:
    """Streams an export's payload out again with names and references remapped.

    A package's name indices and object references are package-relative, so the
    bytes of an export cannot simply be copied: every one of them has to be
    looked up in the source and written back as the destination numbers it. A
    compact index is variable length, so this is a copy, not a patch -- the
    payload is read field by field and rebuilt.
    """

    def __init__(self, data, pos, end, map_name, map_ref):
        self.d, self.p, self.end = data, pos, end
        self.out = bytearray()
        self.map_name, self.map_ref = map_name, map_ref
        self.fixups = []

    def raw(self, n):
        self.out += self.d[self.p:self.p + n]
        self.p += n

    def _read(self):
        r = ue2.Reader(self.d, self.p)
        v = r.index()
        self.p = r.p
        return v

    def idx(self):
        """A count or an offset: copied as it stands."""
        v = self._read()
        self.out += put_index(v)
        return v

    def name(self):
        v = self._read()
        self.out += put_index(self.map_name(v))
        return v

    def ref(self):
        v = self._read()
        self.out += put_index(self.map_ref(v))
        return v

    def put_ref(self, new):
        """An index read from the source and replaced outright."""
        self._read()
        self.out += put_index(new)

    def u8(self):
        v = self.d[self.p]
        self.raw(1)
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.raw(4)
        return v

    def lazy_array(self):
        """A TLazyArray: [INT SeekPos][count][elements], SeekPos ABSOLUTE.

        TLazyArray stores the file offset of the end of its data so a reader can
        seek past it (Core/Inc/UnTemplate.h:1085) and the loader DOES seek there,
        so a stale SeekPos sends the stream somewhere else entirely -- the symptom
        is a "Bad export index" out of UStaticMesh::Serialize. The element size
        never has to be known: the source's own SeekPos says where the data ends.

        Records a fixup instead of a value, because the right number is not known
        until the payload is placed in the destination file.
        """
        old_end = struct.unpack_from("<i", self.d, self.p)[0]
        field = len(self.out)
        self.out += b"\0\0\0\0"
        self.p += 4
        self.idx()                              # element count, copied
        self.raw(max(0, old_end - self.p))      # the elements themselves
        self.fixups.append((field, len(self.out)))
        return field

    def rest(self):
        self.raw(self.end - self.p)

    def properties(self, names):
        """The tagged property list, remapped. Nothing is written if there is none."""
        peek = ue2.Reader(self.d, self.p).index()
        if not 0 <= peek < len(names):
            return                      # no property list here at all
        body, after = _rw_tagged(names, self.d, self.p, self.map_name,
                                 self.map_ref)
        self.out += body
        self.p = after

    def stack(self, flags):
        if flags & ue2.RF_HAS_STACK:
            node = self.ref()
            self.ref()
            self.raw(8)
            self.raw(4)
            if node:
                self.idx()


def _rw_tagged(names, data, pos, map_name, map_ref):
    """(re-emitted property list, position after it), names and refs remapped.

    An Object value is a reference and an Array of structs holds more of them --
    UStaticMesh.Materials is an array of FStaticMeshMaterial, so the material a
    slot points at is nested two levels down and is exactly what has to change.
    """
    recs, end = _parse_tagged(names, data, pos)
    out = []
    for idx, info, sextra, aextra, value in recs:
        kind = info & 0x0F
        if kind == PT_OBJECT and value:
            value = put_index(map_ref(ue2.Reader(value, 0).index()))
        elif kind == PT_NAME and value:
            value = put_index(map_name(ue2.Reader(value, 0).index()))
        elif kind == PT_ARRAY and value:
            r = ue2.Reader(value, 0)
            count = r.index()
            at = r.p
            new = put_index(count)
            for _ in range(count):
                body, at = _rw_tagged(names, value, at, map_name, map_ref)
                new += body
            value = new
        elif kind == ue2.PT_STRUCT:
            # The struct's TYPE name is a name index too, and it is written
            # before the size field, so it travels in sextra.
            sextra = put_index(map_name(ue2.Reader(sextra, 0).index()))
        out.append((map_name(idx), info, sextra, aextra, value))
    return _emit_tagged(out, map_name(names.index("None"))), end


def rewrite_polys(pkg, index, map_name, map_ref):
    """A UPolys payload, remapped. FPoly holds two references and a name.

    FPoly (Engine/Src/UnModel.cpp:53): NumVertices, Base, Normal, TextureU,
    TextureV, Vertex[n], PolyFlags, Actor, Material, ItemName, iLink,
    iBrushPoly, LightMapScale. Actor and Material are object references and
    ItemName is an FName -- a collision hull carried without remapping them
    reads as a different mesh's properties entirely, because the very first
    name index it hits resolves to something else.
    """
    export = pkg.exports[index]
    names = names_of(pkg)
    rw = Rewriter(pkg.data, export["offset"],
                  export["offset"] + export["size"], map_name, map_ref)
    rw.stack(export["flags"])
    rw.properties(names)
    count = rw.i32()
    rw.i32()                                   # DbMax
    for _ in range(count):
        n = rw.idx()                           # NumVertices
        rw.raw(12 + 12 + 24)                   # Base, Normal, TextureU/V
        rw.raw(12 * n)                         # Vertex[n]
        rw.raw(4)                              # PolyFlags
        rw.ref()                               # Actor
        rw.ref()                               # Material
        rw.name()                              # ItemName
        rw.idx()                               # iLink
        rw.idx()                               # iBrushPoly
        rw.raw(4)                              # LightMapScale
    rw.rest()
    return bytes(rw.out), rw.fixups


def rewrite_model(pkg, index, map_name, map_ref, polys_ref=None):
    """A UModel payload, remapped, with its Polys reference replaced.

    The walk is UModel::Serialize's own order (Engine/Src/UnModel.cpp:162) --
    see ue2._walk_to_polys_ref on why scanning for something that looks like
    the Polys reference is not good enough.
    """
    export = pkg.exports[index]
    names = names_of(pkg)
    rw = Rewriter(pkg.data, export["offset"],
                  export["offset"] + export["size"], map_name, map_ref)
    rw.stack(export["flags"])
    rw.properties(names)
    rw.raw(12 + 12 + 1 + 16)                   # UPrimitive: FBox + FSphere
    rw.raw(12 * rw.idx())                      # Vectors
    rw.raw(12 * rw.idx())                      # Points
    for _ in range(rw.idx()):                  # Nodes
        rw.raw(16 + 8)
        rw.u8()
        for _ in range(7):
            rw.idx()
        rw.raw(16)
        rw.raw(3)
        rw.raw(8)
        rw.raw(12)
    for _ in range(rw.idx()):                  # Surfs
        rw.ref()                               # Material
        rw.raw(4)                              # PolyFlags
        for _ in range(4):
            rw.idx()                           # pBase, vNormal, vTextureU/V
        rw.idx()                               # iBrushPoly
        rw.ref()                               # Actor
        rw.raw(16 + 4)                         # Plane, LightMapScale
    for _ in range(rw.idx()):                  # Verts
        rw.idx()
        rw.idx()
    rw.i32()                                   # NumSharedSides
    for _ in range(rw.i32()):                  # Zones
        rw.ref()                               # ZoneActor
        rw.raw(8 + 8 + 4)
    if polys_ref is None:
        rw.ref()
    else:
        rw.put_ref(polys_ref)
    # Bounds, LeafHulls, Leaves, Lights, RootOutside, Linked
    # (Engine/Src/UnModel.cpp). Lights is a TArray<AActor*> -- empty on a
    # collision model, but it is references, so it is walked rather than assumed.
    rw.raw(25 * rw.idx())                      # Bounds: FBox
    rw.raw(4 * rw.idx())                       # LeafHulls: INT
    for _ in range(rw.idx()):                  # Leaves: FLeaf
        rw.idx(); rw.idx(); rw.idx()
        rw.raw(8)                              # VisibleZones (QWORD)
    for _ in range(rw.idx()):                  # Lights
        rw.ref()
    rw.raw(8)                                  # RootOutside, Linked
    rw.rest()
    return bytes(rw.out), rw.fixups


def walk_is_sane(pkg, export):
    """True when the StaticMesh walk lands where it should.

    The walk is version-specific and a handful of meshes do not follow it --
    DM-1on1-Lea's `idomacrate` and two of DM-Elucidation's. An identity round
    trip cannot catch that, because a wrong walk still copies every byte, so it
    is checked against the data instead: the CollisionModel reference has to be
    NULL or a Model that actually bounds this mesh, and the kDOP counts and the
    RawTriangles seek offset have to fit inside the payload. A mesh that fails is
    left alone rather than written from a walk that is somewhere else.
    """
    end = export["offset"] + export["size"]
    try:
        r = _walk_to_collision_reader(pkg, export)
        ref = r.index()
        if ref != 0 and ue2.static_mesh_collision(pkg, export) is None:
            return False
        nodes = r.index()
        if not 0 <= nodes or r.p + 32 * nodes > end:
            return False
        r.p += 32 * nodes
        tris = r.index()
        if not 0 <= tris or r.p + 8 * tris > end:
            return False
        r.p += 8 * tris
        seek = struct.unpack_from("<i", pkg.data, r.p)[0]
        return r.p + 4 <= seek <= end
    except Exception:
        return False


def _walk_to_collision_reader(pkg, export):
    r = ue2._walk_to_collision_ref(pkg, export)
    if r is None:
        raise ValueError("no walk")
    return r


def rewrite_static_mesh(pkg, index, map_name, map_ref, model_ref=None):
    """A UStaticMesh payload, remapped, with its CollisionModel replaced."""
    export = pkg.exports[index]
    names = names_of(pkg)
    rw = Rewriter(pkg.data, export["offset"],
                  export["offset"] + export["size"], map_name, map_ref)
    rw.stack(export["flags"])
    rw.properties(names)
    rw.raw(41)                                 # UPrimitive: FBox + FSphere
    rw.raw(14 * rw.idx())                      # Sections
    rw.raw(25)                                 # BoundingBox
    rw.raw(24 * rw.idx()); rw.raw(4)           # VertexStream
    rw.raw(4 * rw.idx());  rw.raw(4)           # ColorStream
    rw.raw(4 * rw.idx());  rw.raw(4)           # AlphaStream
    for _ in range(rw.idx()):                  # UVStreams
        rw.raw(8 * rw.idx()); rw.raw(8)
    rw.raw(2 * rw.idx());  rw.raw(4)           # IndexBuffer
    rw.raw(2 * rw.idx());  rw.raw(4)           # WireframeIndexBuffer
    if model_ref is None:
        rw.ref()
    else:
        rw.put_ref(model_ref)
    # kDOPTree: Nodes (FkDOPNode, 6 floats + UBOOL + 2 WORDs = 32) then
    # Triangles (4 WORDs = 8). Fixed width either way -- FkDOPNode's union
    # serialises two WORDs whichever branch it takes (Engine/Inc/UnkDOP.h:205).
    rw.raw(32 * rw.idx())
    rw.raw(8 * rw.idx())
    rw.lazy_array()                            # RawTriangles
    rw.raw(4)                                  # InternalVersion
    # The licensee tail, then KPhysicsProps -- a SECOND object reference, right
    # at the end (Engine/Src/UnStaticMesh.cpp:488). Copying it verbatim points
    # it at whatever export happens to have that index in the destination, and
    # the loader says "Bad export index" on a mesh that otherwise carried fine.
    if pkg.licensee == 0x16:
        rw.raw(4)                              # SmoothingThreshold
    elif pkg.licensee > 0x16:
        rw.raw(4 * rw.idx())                   # MaxSmoothingAngles
    if pkg.version >= 100:
        rw.ref()                               # KPhysicsProps
    if pkg.version >= 120:
        rw.raw(4)                              # AuthenticationKey
    rw.rest()
    return bytes(rw.out), rw.fixups


def rewrite_object(pkg, index, map_name, map_ref):
    """A payload whose only package-relative data is its property list.

    UKMeshProps is the one this needs: a static mesh's KPhysicsProps. Its own
    Serialize writes an inertia tensor, a centre of mass and FKAggregateGeom
    (Engine/Inc/KTypes.h:244) -- floats and counts, no names and no references.
    """
    export = pkg.exports[index]
    rw = Rewriter(pkg.data, export["offset"],
                  export["offset"] + export["size"], map_name, map_ref)
    rw.stack(export["flags"])
    rw.properties(names_of(pkg))
    rw.rest()
    return bytes(rw.out), rw.fixups


# Classes whose payload rewrite_object can handle, so a mesh that references one
# can bring it along instead of failing.
PLAIN_CLASSES = ("KMeshProps",)


def class_package(class_name):
    """The package a class lives in.

    UPackage and UClass are Core; every material class is Engine. An import whose
    ClassPackage.ClassName does not resolve gives the linker no class to make the
    object with, and UE2 does not fail loudly: the reference simply comes back
    NULL and the mesh renders in the default texture.
    """
    return "Core" if class_name in ("Package", "Class") else "Engine"


def ensure_import_path(dst, path, class_name="Material"):
    """A reference in dst to Package.Group.Name, creating the chain if needed.

    The middle element is not always a group. A generated texture package's
    materials are subobjects of the generated CLASS, which is named after the
    package -- so <Pkg>.<Pkg>.<Name> is a class outer and <Pkg>.<Group>.<Name>
    is a package one, and an import that calls a class a Package resolves to
    nothing at all.
    """
    parts = path.split(".")
    ref = 0
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            cls = class_name
        elif i > 0 and part == parts[0]:
            cls = "Class"
        else:
            cls = "Package"
        for j, have in enumerate(dst.imports):
            if (dst.names[have["name"]][0].lower() == part.lower()
                    and have["outer"] == ref
                    and dst.names[have["class_name"]][0].lower() == cls.lower()):
                ref = -(j + 1)
                break
        else:
            dst.imports.append({
                "class_package": dst.ensure_name(class_package(cls)),
                "class_name": dst.ensure_name(cls),
                "outer": ref,
                "name": dst.ensure_name(part)})
            ref = -len(dst.imports)
    return ref


def _ensure_import(src, dst, ref):
    """Map an import reference from src into dst, creating the chain if needed."""
    if ref >= 0:
        return ref
    entry = src.imports[-ref - 1]
    outer = _ensure_import(src, dst, entry["outer"])
    cls_name = src.names[entry["class_name"]][0]
    name = src.names[entry["name"]][0]
    for i, have in enumerate(dst.imports):
        if (dst.names[have["name"]][0].lower() == name.lower()
                and dst.names[have["class_name"]][0].lower() == cls_name.lower()
                and have["outer"] == outer):
            return -(i + 1)
    dst.imports.append({"class_package": dst.ensure_name(
                            src.names[entry["class_package"]][0]),
                        "class_name": dst.ensure_name(cls_name),
                        "outer": outer,
                        "name": dst.ensure_name(name)})
    return -len(dst.imports)


def _ensure_outer(src, dst, ref):
    """Map an export's outer -- its group, or the object it belongs to -- into dst."""
    if ref == 0:
        return 0
    if ref < 0:
        return _ensure_import(src, dst, ref)
    parent = src.exports[ref - 1]
    name = src.names[parent["name"]][0]
    cls = _ensure_import(src, dst, parent["class_ref"])
    outer = _ensure_outer(src, dst, parent["outer"])
    for i, have in enumerate(dst.exports):
        if (dst.names[have["name"]][0].lower() == name.lower()
                and have["outer"] == outer and have["class_ref"] == cls):
            return i + 1
    # A group is a UPackage with no data of its own, but its export is NOT
    # zero-length: UObject::Serialize still writes the tagged property list,
    # which for an empty object is the single "None" index. An export whose
    # SerialSize is 0 has no SerialOffset field written after it, so a group
    # written as zero-length shifts every export entry after it and the engine
    # reads the table as garbage -- it asserts in ULinkerLoad::CreateExport on
    # an export with no name, which is the misread size field, not the group.
    payload = put_index(dst.ensure_name("None"))
    dst.exports.append({"class_ref": cls, "super_ref": 0, "outer": outer,
                        "name": dst.ensure_name(name),
                        "flags": parent["flags"], "size": len(payload),
                        "offset": 0})
    dst.payload.append(payload)
    return len(dst.exports)


def _reserve(src, dst, idx, rename=None):
    """Create the destination export for a source export, its payload to follow.

    The slot has to exist before the payloads are built, because the objects
    point at each other: a StaticMesh names its CollisionModel, and that Model's
    OUTER is the mesh. Building the mesh last means _ensure_outer cannot find it
    and invents a one-byte stub of the same name instead -- the package then
    holds two of every hulled mesh, and which one an actor binds to is anyone's
    guess.
    """
    e = src.exports[idx]
    cls = _ensure_import(src, dst, e["class_ref"])
    outer = _ensure_outer(src, dst, e["outer"])
    name = dst.ensure_name(rename or src.names[e["name"]][0],
                           src.names[e["name"]][1])
    # An object already here is the one being replaced, not a second copy. That
    # is what lets a built map be patched in place, and it makes running the
    # carry twice a no-op rather than a duplicate.
    #
    # Identity is name, group and class: an object already here under that name
    # is the one being replaced, not a second copy. That is what lets a built map
    # be patched in place, and it makes running the carry twice a no-op.
    #
    # Which is why a collision Model, its Polys and a KPhysicsProps are RENAMED
    # after the mesh they belong to. Their own names are sequential and mean
    # nothing (Model302, Polys14, KMeshProps7), and at package root -- where
    # UnrealEd often puts them, though it also uses the mesh's group or the mesh
    # itself -- a source map's Model302 collides with a completely unrelated
    # Model302 in the destination. Matching that reused DM-Rankin4K's own LEVEL
    # model as a mesh's collision hull and overwrote 24 MB of BSP with a 1 KB
    # box. Nothing references these by name, so renaming them is free, and it
    # makes them findable again on a re-run.
    for i, have in enumerate(dst.exports):
        if (have["name"] == name and have["outer"] == outer
                and have["class_ref"] == cls):
            dst.payload[i] = b""
            # How much room there is at the original offset. The writer reuses
            # the slot only for a replacement of exactly that length; anything
            # else is appended, because writing long would run into whatever
            # follows. Without it the writer sees size == size and leaves the
            # ORIGINAL bytes in place, silently dropping the replacement.
            dst.exports[i]["slot"] = have["size"]
            dst.exports[i]["size"] = 0
            dst.fixups.pop(i, None)
            dst.exports[i]["name"] = name
            return i + 1
    dst.exports.append({"class_ref": cls, "super_ref": _ensure_import(
                            src, dst, e["super_ref"]),
                        "outer": outer, "name": name,
                        "flags": e["flags"], "size": 0, "offset": 0})
    dst.payload.append(b"")
    return len(dst.exports)          # 1-based reference


def _fill(dst, ref, payload, fixups=None):
    """Give a reserved export its payload."""
    i = ref - 1
    dst.payload[i] = payload
    dst.exports[i]["size"] = len(payload)
    if fixups:
        dst.fixups[i] = list(fixups)
    return ref


def _append(src, dst, idx, payload, fixups=None):
    e = src.exports[idx]
    dst.exports.append({"class_ref": _ensure_import(src, dst, e["class_ref"]),
                        "super_ref": _ensure_import(src, dst, e["super_ref"]),
                        "outer": _ensure_outer(src, dst, e["outer"]),
                        "name": dst.ensure_name(src.names[e["name"]][0]),
                        "flags": e["flags"], "size": len(payload), "offset": 0})
    dst.payload.append(payload)
    if fixups:
        dst.fixups[len(dst.exports) - 1] = list(fixups)
    return len(dst.exports)          # 1-based reference


def name_mapper(src, dst):
    """A source name index -> destination name index, creating names as needed.

    The source's flags travel with the name: they decide whether the engine
    keeps the name at all -- see RawPackage.ensure_name.
    """
    names = names_of(src)
    flags = [f for _n, f in src.names] if (src.names and
                                           isinstance(src.names[0], tuple)) else None
    cache = {}

    def mapper(idx):
        if idx not in cache:
            cache[idx] = dst.ensure_name(names[idx],
                                         flags[idx] if flags else None)
        return cache[idx]
    return mapper


def ref_mapper(src, src_raw, dst, resolve=None, carried=None, adopt=None,
               dropped=None):
    """A source object reference -> destination reference.

    `carried` maps source export indices (1-based references) to the references
    they were given in dst, for the objects this carry has already copied.
    `resolve` gets first refusal on anything with a path, which is how a
    material is repointed at the rebuilt one.
    """
    carried = carried if carried is not None else {}
    dropped = [] if dropped is None else dropped
    cache = {}

    def mapper(ref):
        if ref == 0:
            return 0
        if ref in carried:
            return carried[ref]
        if ref in cache:
            return cache[ref]
        if ref < 0:
            path = src.import_path(ref)
        else:
            path = src.export_path(ref - 1)
        out = resolve(path, ref) if (resolve and path) else None
        if out is None:
            if ref < 0:
                out = _ensure_import(src_raw, dst, ref)
            elif adopt is not None and src.class_of(
                    src.exports[ref - 1]) in PLAIN_CLASSES:
                out = adopt(ref - 1)
            else:
                # An export of the SOURCE map with nowhere to go -- a composite
                # the rebuild could not remake, usually. It is written as NULL,
                # never carried across: a reference to the source map puts that
                # map in the net package map, matched by GUID, so every client is
                # told it needs that exact build of it. NULL is also what the ASE
                # route produced for these, so nothing regresses.
                dropped.append(path)
                out = 0
        cache[ref] = out
        return out
    return mapper


def carry_static_mesh(src_raw, src_ue2, name, dst_raw, resolve_material=None,
                      log=print, dropped=None):
    """Copy one StaticMesh, with its CollisionModel and Polys, into dst_raw.

    `resolve_material(path, ref)` maps a source material -- by its full dotted
    path -- to a reference in dst, or returns None to leave it where it is.

    Returns (dst_reference, materials_remapped), or (None, 0) if not found.
    """
    mesh_idx = None
    for i, e in enumerate(src_ue2.exports):
        if e["name"] == name and src_ue2.class_of(e) == "StaticMesh":
            mesh_idx = i
            break
    if mesh_idx is None:
        return None, 0
    mesh = src_ue2.exports[mesh_idx]
    if not walk_is_sane(src_ue2, mesh):
        return UNWALKABLE, 0

    map_name = name_mapper(src_raw, dst_raw)   # src_raw keeps the name flags
    counted = []
    adopted = {}
    dropped = [] if dropped is None else dropped

    def resolve(path, ref):
        out = resolve_material(path, ref) if resolve_material else None
        if out is not None:
            counted.append(path)
        return out

    def adopt(idx):
        """Bring along a plain object the mesh points at -- its KPhysicsProps."""
        if idx not in adopted:
            ref = _reserve(src_raw, dst_raw, idx, rename=name + "_KProps")
            adopted[idx] = _fill(dst_raw, ref,
                                 *rewrite_object(src_ue2, idx, map_name,
                                                 map_ref))
            log("    + %s %s" % (src_ue2.class_of(src_ue2.exports[idx]),
                                 src_ue2.exports[idx]["name"]))
        return adopted[idx]

    mesh_ref = _reserve(src_raw, dst_raw, mesh_idx)
    map_ref = ref_mapper(src_ue2, src_raw, dst_raw, resolve, adopt=adopt,
                         carried={mesh_idx + 1: mesh_ref}, dropped=dropped)

    model_ref = None
    model_idx = ue2.static_mesh_collision(src_ue2, mesh)
    if model_idx is not None:
        model = src_ue2.exports[model_idx]
        polys_idx = ue2.model_polys_index(src_ue2, model)
        model_ref = _reserve(src_raw, dst_raw, model_idx, rename=name + "_CM")
        polys_ref = None
        if polys_idx is not None:
            polys_ref = _reserve(src_raw, dst_raw, polys_idx,
                                 rename=name + "_CMPolys")
            _fill(dst_raw, polys_ref,
                  *rewrite_polys(src_ue2, polys_idx, map_name, map_ref))
        _fill(dst_raw, model_ref,
              *rewrite_model(src_ue2, model_idx, map_name, map_ref,
                             polys_ref=polys_ref))

    _fill(dst_raw, mesh_ref,
          *rewrite_static_mesh(src_ue2, mesh_idx, map_name, map_ref,
                               model_ref=model_ref))
    return mesh_ref, len(counted)


# ---------------------------------------------------------------------------
# Pipeline level: carry a source map's meshes into the package the rebuild has
# just compiled.


MATERIAL_CLASSES = ("Texture", "Shader", "Combiner", "FinalBlend", "Cubemap",
                    "TexOscillator", "TexPanner", "TexRotator", "TexScaler",
                    "TexEnvMap", "ColorModifier", "Material", "MaterialSequence",
                    "ConstantColor", "FadeColor", "TexCoordSource",
                    "OpacityModifier", "VertexColor", "ProjectorMaterial")


def resolution_table(project):
    """leaf name (lowercased) -> ("import", path, class) | ("export", name).

    Where every material this rebuild produced actually ended up: a wrapper or
    plain texture in the package being built, or an object in one of the shared
    <Source>4K content packages.
    """
    table = {}
    for r in (project.load("manifest.json") or {}).get("textures", []):
        table[r["name"].lower()] = ("export", r["name"])
    for leaf, rec in (project.load("composites.json") or {}).items():
        table[leaf.lower()] = ("export", rec["name"].rsplit(".", 1)[-1])
    for leaf, shader in (project.load("wrappers.json") or {}).items():
        table[leaf.lower()] = ("export", shader)
    # Shared content wins over a local copy: the package being built does not
    # import the leaf at all when the content store already holds it.
    for leaf, ref in (project.load("content_plain.json") or {}).items():
        table[leaf.lower()] = ("import", ref, "Texture")
    for leaf, ref in (project.load("content_refs.json") or {}).items():
        table[leaf.lower()] = ("import", ref[0], ref[2])
    for leaf, (cls, path) in (project.load("external_materials.json") or {}).items():
        table[leaf.lower()] = ("import", path, cls)
    return table


def _import_path(raw, ref):
    """Dotted path of a RawPackage import reference."""
    parts = []
    while ref < 0:
        entry = raw.imports[-ref - 1]
        parts.append(raw.names[entry["name"]][0])
        ref = entry["outer"]
    return ".".join(reversed(parts))


def find_export_ref(dst, name):
    """A 1-based reference to a material export of dst called `name`, or None."""
    lowered = name.lower()
    for i, e in enumerate(dst.exports):
        if dst.names[e["name"]][0].lower() != lowered:
            continue
        cls = e["class_ref"]
        if cls < 0:
            cls_name = dst.names[dst.imports[-cls - 1]["name"]][0]
            if cls_name in MATERIAL_CLASSES:
                return i + 1
    return None


def make_resolver(project, dst, src_pkg, log=print):
    """A resolve_material for carry_static_mesh, plus a list to collect misses."""
    table = resolution_table(project)
    missing = []
    cache = {}

    def resolve(path, ref):
        if path in cache:
            return cache[path]
        leaf = path.rsplit(".", 1)[-1]
        entry = table.get(leaf.lower())
        out = None
        if entry and entry[0] == "export":
            out = find_export_ref(dst, entry[1])
            if out is None:
                missing.append("%s -> %s (not in the built package)"
                               % (path, entry[1]))
        elif entry:
            out = ensure_import_path(dst, entry[1], class_name=entry[2])
        # Anything else is left to ref_mapper: an import of a package the
        # rebuild did not touch is correct as it stands and the chain is carried
        # across, and an unresolvable export of the source map is fatal there.
        cache[path] = out
        return out

    resolve.missing = missing
    return resolve


def carry_map_meshes(project, package_path, log=print, new_guid=False):
    """Copy every StaticMesh out of the source map into an already-built package.

    Runs after `ucc make`, on the .u it produced. The meshes are NOT compiled in
    -- an ASE cannot carry a collision hull faithfully -- so the package is not
    reproducible from its .uc alone, and this step is part of the build.

    It also works on a BUILT MAP, which is how a map that has already shipped is
    fixed: the 4K rebuilds are imported with PACKAGE=MyLevel, so the .ut2 holds
    the meshes as its own exports and rebuilding <Map>Tex does nothing for it. An
    export of the same name, group and class is replaced rather than added, so
    the map's embedded meshes are overwritten in place -- no re-import, no
    re-light, nothing else in the map touched.

    `new_guid` is required in that case. A client matches a package BY GUID
    (Engine/Src/UnPenLev.cpp), so a map whose contents changed under the same
    GUID is a map every client keeps using the stale cached copy of.
    """
    from .pkgwrite import RawPackage

    map_file = project.map_file()
    src_ue2 = ue2.Package(map_file)
    src_raw = RawPackage(map_file)
    dst = RawPackage(package_path)

    names = [e["name"] for e in src_ue2.exports
             if src_ue2.class_of(e) == "StaticMesh"]
    if not names:
        log("no static meshes embedded in %s" % os.path.basename(map_file))
        return []

    resolve = make_resolver(project, dst, src_ue2, log=log)
    carried, hulls, unwalkable, dropped = [], 0, [], []
    for name in names:
        before = len(dst.exports)
        ref, remapped = carry_static_mesh(src_raw, src_ue2, name, dst,
                                          resolve_material=resolve, log=log,
                                          dropped=dropped)
        if ref is UNWALKABLE:
            unwalkable.append(name)
            log("  %-28s SKIPPED -- serialised layout does not walk" % name)
            continue
        if ref is None:
            log("  %-28s NOT FOUND" % name)
            continue
        added = len(dst.exports) - before
        slots = len(material_refs_raw(dst, ref - 1))
        hull = ue2.static_mesh_collision(src_ue2, src_ue2.export_named(name))
        if hull is not None:
            hulls += 1
        log("  %-28s %2d slots, %d remapped%s" % (
            name, slots, remapped, ", hull" if hull is not None else ""))
        carried.append(name)

    if resolve.missing:
        raise SystemExit("mesh slots could not be resolved:\n  "
                         + "\n  ".join(resolve.missing))

    if dropped:
        log("%d reference(s) written as NULL -- embedded in the source map with no"
            " rebuilt object:" % len(dropped))
        for path in sorted(set(dropped)):
            log("  %s" % path)
    if unwalkable:
        log("%d mesh(es) NOT carried, their layout does not walk: %s"
            % (len(unwalkable), ", ".join(unwalkable)))
        log("  they keep whatever %s already had for them"
            % os.path.basename(package_path))

    # What the carried slots still point at outside this package. A slot left on
    # the ORIGINAL package is not broken -- it is a texture this rebuild never
    # upscaled, usually one no surface uses -- but it is a dependency, so say so.
    outside = {}
    for name in carried:
        idx = max(i for i, e in enumerate(dst.exports)
                  if names_of(dst)[e["name"]] == name)
        for _slot, ref in material_refs_raw(dst, idx):
            if ref >= 0:
                continue
            path = _import_path(dst, ref)
            pkg_name = path.split(".")[0]
            if pkg_name.lower() != os.path.basename(package_path).split(".")[0].lower():
                outside.setdefault(pkg_name, set()).add(path)
    left = {k: v for k, v in outside.items() if not k.endswith("4K")}
    if left:
        log("mesh slots still bound to original-resolution packages (%d):"
            % sum(len(v) for v in left.values()))
        for pkg_name in sorted(left):
            log("  %-28s %s" % (pkg_name, ", ".join(
                sorted(p.rsplit(".", 1)[-1] for p in left[pkg_name]))))

    if new_guid:
        import uuid
        dst.guid = uuid.uuid4().bytes
        log("new package GUID %s -- clients must re-download this file"
            % dst.guid.hex())

    # Written aside and moved into place: the source is still mmapped, and
    # truncating a mapping under it is how a half-written package happens.
    dst.write(package_path + ".new")
    os.replace(package_path + ".new", package_path)
    log("%d mesh(es) carried verbatim into %s, %d with a collision hull"
        % (len(carried), os.path.basename(package_path), hulls))
    return carried
