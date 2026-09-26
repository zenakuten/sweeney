"""Minimal reader for UE2 (UT2004) packages -- .ut2, .utx, .u.

Enough to answer "what does this map reference, and what does it contain":
the name, import and export tables, with paths resolved. That is all the
upscaler needs, and it is what makes it general -- the texture list comes from
the map rather than from a hand-written list.

The import table is authoritative for external references. Anything a BSP
surface or an embedded static mesh uses has to appear there, because UE2 has no
other way to name an object in another package.
"""

import mmap, struct

TAG = 0x9E2A83C1


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u8(self):
        v = self.d[self.p]; self.p += 1; return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]; self.p += 4; return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v

    def index(self):
        """UE2 compact index: sign in bit 7, continue in bit 6, then 7 bits."""
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

    def skip(self, n):
        self.p += n
        return None

    def u16(self):
        v = struct.unpack_from("<H", self.d, self.p)[0]
        self.p += 2
        return v

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


class Package:
    def __init__(self, path):
        self.path = path
        # Mapped, not read: these packages run to hundreds of MB and a long run
        # opens many of them. Reading each one whole pushed the machine into
        # memory pressure and the build was killed. mmap indexes and slices the
        # same way, and struct.unpack_from works on it directly, so nothing
        # downstream changes.
        self._fh = open(path, "rb")
        self.data = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        tag, self.version, self.licensee = struct.unpack_from("<IHH", self.data, 0)
        if tag != TAG:
            raise ValueError("%s is not a UE2 package" % path)
        (self.flags, name_count, name_off, export_count, export_off,
         import_count, import_off) = struct.unpack_from("<IIIIIII", self.data, 8)

        r = Reader(self.data, name_off)
        self.names = []
        for _ in range(name_count):
            n = r.string(); r.u32()
            self.names.append(n)

        r = Reader(self.data, import_off)
        self.imports = []
        for _ in range(import_count):
            cls_pkg = r.index(); cls = r.index(); outer = r.i32(); obj = r.index()
            self.imports.append({"class": self.names[cls],
                                 "name": self.names[obj], "outer": outer})

        r = Reader(self.data, export_off)
        self.exports = []
        for _ in range(export_count):
            cls = r.index(); super_ = r.index(); outer = r.i32(); obj = r.index()
            flags = r.u32(); size = r.index()
            offset = r.index() if size > 0 else 0
            self.exports.append({"class_ref": cls, "outer": outer,
                                 "name": self.names[obj], "flags": flags,
                                 "size": size, "offset": offset})

    # -- reference resolution ------------------------------------------------

    def class_of(self, export):
        ref = export["class_ref"]
        if ref < 0:
            return self.imports[-ref - 1]["name"]
        if ref > 0:
            return self.exports[ref - 1]["name"]
        return "Class"

    def import_path(self, ref):
        """Full dotted path of an import, e.g. HumanoidArchitecture.Walls.wal17HA."""
        parts = []
        while ref < 0:
            entry = self.imports[-ref - 1]
            parts.append(entry["name"])
            ref = entry["outer"]
        return ".".join(reversed(parts))

    def export_path(self, index):
        """Dotted path of an export within this package (groups included)."""
        parts = []
        ref = index + 1
        while ref > 0:
            entry = self.exports[ref - 1]
            parts.append(entry["name"])
            ref = entry["outer"]
        return ".".join(reversed(parts))

    def imports_of_class(self, *classes):
        out = []
        for i, entry in enumerate(self.imports):
            if entry["class"] in classes:
                out.append((entry["name"], self.import_path(-(i + 1))))
        return out

    def exports_of_class(self, *classes):
        out = []
        for i, entry in enumerate(self.exports):
            if self.class_of(entry) in classes:
                out.append((entry["name"], self.export_path(i), entry))
        return out

    # -- the level's BSP -----------------------------------------------------

    def level_model(self):
        """The biggest Model export -- the level's own, not a brush's."""
        models = [(i, e) for i, e in enumerate(self.exports)
                  if self.class_of(e) == "Model"]
        if not models:
            return None
        return max(models, key=lambda ie: ie[1]["size"])[1]

    def surfaces(self):
        """[{material, flags, lightmap_scale}] for every BSP surface.

        Walks UModel::Serialize far enough to reach Surfs: the tagged-property
        terminator, UPrimitive's bounds, then the Vectors, Points and Nodes
        arrays. FBspSurf itself is Engine/Src/UnModel.cpp:14.
        """
        export = self.level_model()
        if export is None:
            return []
        r = Reader(self.data, export["offset"])
        start = r.p
        first = r.index()
        if not (0 <= first < len(self.names)) or self.names[first] != "None":
            r.p = start                      # no property list at all
        r.p += 12 + 12 + 1 + 16              # FBox (+IsValid) then FSphere

        count = r.index(); r.p += 12 * count         # Vectors
        count = r.index(); r.p += 12 * count         # Points

        for _ in range(r.index()):                   # Nodes
            r.p += 16 + 8                            # plane, zone mask
            r.u8()                                   # flags
            for _ in range(7):
                r.index()                            # vert pool .. render bound
            r.p += 16                                # exclusive sphere bound
            r.u8(); r.u8(); r.u8()                   # zones, vertex count
            r.i32(); r.i32()                         # leaves
            r.p += 12                                # section, first vertex, lightmap

        out = []
        for _ in range(r.index()):                   # Surfs
            material = r.index(); flags = r.u32()
            r.index(); r.index(); r.index(); r.index()   # base, normal, u, v
            r.index()                                    # iBrushPoly
            r.index()                                    # actor
            plane = struct.unpack_from("<4f", self.data, r.p); r.p += 16
            # Keep the file offset: LightMapScale is a fixed-size float, so it
            # can be rewritten in place in a built .ut2 without moving anything.
            offset = r.p
            scale = struct.unpack_from("<f", self.data, r.p)[0]; r.p += 4
            out.append({"material": self.ref_path(material),
                        "flags": flags, "lightmap_scale": scale,
                        "plane": plane, "scale_offset": offset})
        return out

    def properties(self, export):
        """Tagged properties of an export, by name.

        An object with RF_HasStack -- every Actor does -- serialises an
        FStateFrame first: Node and StateNode (object indices), ProbeMask and
        LatentAction, then Offset when Node is set. Reading the properties
        without skipping it returns nothing at all, which is why a LevelInfo
        looked like it had no Screenshot, Title or Author.
        """
        r = Reader(self.data, export["offset"])
        if export["flags"] & RF_HAS_STACK:
            node = r.index()
            r.index()                      # StateNode
            r.skip(8)                      # ProbeMask (QWORD)
            r.skip(4)                      # LatentAction (INT)
            if node != 0:
                r.index()                  # Offset into the node's bytecode
        return _read_properties(self, r)

    def export_named(self, name, cls=None):
        for e in self.exports:
            if e["name"] == name and (cls is None or self.class_of(e) == cls):
                return e
        return None

    def ref_path(self, ref):
        """Dotted path for an object reference, import or export."""
        if ref < 0:
            return self.import_path(ref)
        if ref > 0:
            return "%s.%s" % (self.name, self.export_path(ref - 1))
        return None

    @property
    def name(self):
        import os
        return os.path.splitext(os.path.basename(self.path))[0]


# -- tagged properties -------------------------------------------------------
#
# UObject::SerializeTaggedProperties. Every property is a name, an info byte,
# then its value; the list ends at the name "None". The info byte packs the
# type in bits 0-3, a size code in 4-6, and an array flag in bit 7 -- and for a
# BoolProperty that flag IS the value, with no bytes following, which is the
# detail that makes a naive reader lose sync and return nonsense.

_SIZES = {0: 1, 1: 2, 2: 4, 3: 12, 4: 16}

PT_BOOL, PT_STRUCT = 3, 10
RF_HAS_STACK = 0x02000000


def _tag_size(r, code):
    if code in _SIZES:
        return _SIZES[code]
    if code == 5:
        return r.u8()
    if code == 6:
        return r.u16()
    return r.i32()


class _P:
    """Raw property record: keeps the bytes so callers decode what they need."""

    def __init__(self, kind, struct_name, index, raw):
        self.kind, self.struct_name, self.index, self.raw = kind, struct_name, index, raw

    def floats(self, n):
        return struct.unpack_from("<%df" % n, self.raw, 0)

    def i32(self):
        return struct.unpack_from("<i", self.raw, 0)[0]

    def u8(self):
        return self.raw[0]


def _read_properties(pkg, r):
    """{name: [_P]} up to the None terminator, leaving r after it."""
    out = {}
    while True:
        idx = r.index()
        if not (0 <= idx < len(pkg.names)):
            break
        name = pkg.names[idx]
        if name == "None":
            break
        info = r.u8()
        kind, code, is_array = info & 0x0F, (info >> 4) & 0x07, info & 0x80
        struct_name = None
        if kind == PT_STRUCT:
            s = r.index()
            struct_name = pkg.names[s] if 0 <= s < len(pkg.names) else None
        if kind == PT_BOOL:
            # The value rides in the info byte's high bit, but the SIZE field is
            # still written and must still be consumed -- skipping it desyncs
            # the stream and silently loses every property after the bool.
            # FStaticMeshMaterial is EnableCollision (bool) then Material, and
            # the Material was disappearing because of this.
            _tag_size(r, code)
            out.setdefault(name, []).append(_P(kind, None, 0, bytes([1 if is_array else 0])))
            continue
        size = _tag_size(r, code)
        index = r.index() if is_array else 0
        raw = bytes(r.d[r.p:r.p + size])
        r.p += size
        out.setdefault(name, []).append(_P(kind, struct_name, index, raw))
    return out


def texture_mip0(pkg, export):
    """(width, height, format, bytes) of a texture's top mip.

    UTexture serialises its tagged properties, then Mips as a TArray<FMipmap>.
    Each FMipmap is a TLazyArray<BYTE> -- an absolute skip offset, then the
    usual count and bytes -- followed by USize, VSize (INT) and UBits, VBits
    (BYTE). Only the first mip is read; that is the full-resolution image.

    This exists because umodel cannot export a G16 heightmap or reliably an
    RGBA8 terrain alpha map, and both have to be read as DATA rather than as a
    picture: the heightmap IS the terrain's shape.
    """
    r = Reader(pkg.data, export["offset"])
    props = _read_properties(pkg, r)
    width = props["USize"][0].i32() if "USize" in props else 0
    height = props["VSize"][0].i32() if "VSize" in props else 0
    fmt = props["Format"][0].u8() if "Format" in props else 0
    mips = r.index()
    if mips <= 0:
        return width, height, fmt, b""
    r.i32()                                   # TLazyArray skip offset
    count = r.index()
    data = bytes(pkg.data[r.p:r.p + count])
    return width, height, fmt, data


# -- static mesh collision ---------------------------------------------------
#
# UStaticMesh::Serialize (Engine/Src/UnStaticMesh.cpp) writes, after the tagged
# properties and for package version >= 112:
#
#   Sections              TArray<FStaticMeshSection>   14 bytes each at v>=112
#                         (IsStrip INT, then FirstIndex/MinVertexIndex/
#                          MaxVertexIndex/NumTriangles/NumPrimitives as WORDs)
#   BoundingBox           FBox: two FVectors and a BYTE = 25
#   VertexStream          TArray<FStaticMeshVertex> (Position+Normal = 24) + INT
#   ColorStream           TArray<FColor> (4) + INT
#   AlphaStream           TArray<FColor> (4) + INT
#   UVStreams             TArray<FStaticMeshUVStream>, each
#                           TArray<FStaticMeshUV> (8) + INT CoordinateIndex + INT
#   IndexBuffer           TArray<_WORD> (2) + INT
#   WireframeIndexBuffer  TArray<_WORD> (2) + INT
#   CollisionModel        UModel* -- the object we are after
#
# The CollisionModel is the simplified hull the editor built from an MCD node at
# import. A mesh rebuilt from an ASE without one gets NULL, and the engine falls
# back to per-poly collision on the render triangles -- which is what made
# DM-Rankin's planks impassable.

def _skip_array(r, stride):
    # NOT `r.p += r.index() * stride`: Python evaluates the left operand of +=
    # first, so the bytes r.index() consumes reading the count are thrown away
    # and the stream desyncs.
    count = r.index()
    r.p += count * stride



def primitive_bounds(pkg, export):
    """The FBox a UPrimitive subclass serialises before its own payload."""
    r = Reader(pkg.data, export["offset"])
    if export["flags"] & RF_HAS_STACK:
        r.index(); r.index(); r.skip(12)
    _read_properties(pkg, r)
    lo = struct.unpack_from("<fff", pkg.data, r.p)
    hi = struct.unpack_from("<fff", pkg.data, r.p + 12)
    return lo, hi


def static_mesh_collision(pkg, export):
    """Export index of a StaticMesh's CollisionModel, or None."""
    r = Reader(pkg.data, export["offset"])
    if export["flags"] & 0x02000000:          # RF_HasStack
        r.index(); r.index(); r.skip(12)
    _read_properties(pkg, r)
    try:
        # UStaticMesh extends UPrimitive, whose Serialize writes BoundingBox
        # (FBox, 25) and BoundingSphere (FSphere, 16) before any of this.
        r.skip(41)
        _skip_array(r, 14)                    # Sections
        r.skip(25)                            # BoundingBox
        _skip_array(r, 24); r.skip(4)         # VertexStream
        _skip_array(r, 4);  r.skip(4)         # ColorStream
        _skip_array(r, 4);  r.skip(4)         # AlphaStream
        for _ in range(r.index()):            # UVStreams
            _skip_array(r, 8); r.skip(8)
        _skip_array(r, 2);  r.skip(4)         # IndexBuffer
        _skip_array(r, 2);  r.skip(4)         # WireframeIndexBuffer
        ref = r.index()
    except Exception:
        return None
    if not (ref > 0 and ref - 1 < len(pkg.exports)
            and pkg.class_of(pkg.exports[ref - 1]) == "Model"):
        return None
    # "It parsed as a Model" is not enough. A mesh with NO CollisionModel makes
    # this walk read whatever follows, and any value that happens to index a
    # Model export passes -- DM-1on1-Lea's idomacrate resolved to Model80, which
    # belongs to Brush77. Carrying that into a rebuild gives the mesh a hull made
    # of unrelated brush geometry, hundreds of units from the mesh: an invisible
    # wall in mid-air, while the original is fine because the engine sees a NULL
    # CollisionModel and uses the kDOP tree (UnStaticMeshCollision.cpp:77).
    #
    # A real collision model bounds the mesh it belongs to, so require the two
    # boxes to overlap.
    try:
        mesh_box = primitive_bounds(pkg, export)
        model_box = primitive_bounds(pkg, pkg.exports[ref - 1])
    except Exception:
        return ref - 1
    for i in range(3):
        if model_box[0][i] > mesh_box[1][i] or model_box[1][i] < mesh_box[0][i]:
            return None
    return ref - 1


def read_polys(pkg, index, normals=False):
    """[[FVector, ...], ...] -- the polygons of a UPolys export.

    With normals=True each entry is (Normal, [FVector, ...]) instead. The
    stored Normal is authoritative -- FPoly::Normal is what bspBuild turns into
    the node plane -- and it does NOT always agree with the vertex winding.

    UPolys::Serialize (Engine/Inc/UnObj.h:482) writes INT DbNum, INT DbMax and
    then that many FPoly. FPoly (Engine/Src/UnModel.cpp:53) is:
        NumVertices (compact index)
        Base, Normal, TextureU, TextureV   (FVector each)
        Vertex[NumVertices]                (FVector each)
        PolyFlags (DWORD)
        Actor, Material, ItemName          (object/name indices)
        iLink, iBrushPoly                  (compact indices)
        LightMapScale (FLOAT, version >= 106)
    """
    export = pkg.exports[index]
    r = Reader(pkg.data, export["offset"])
    if export["flags"] & 0x02000000:
        r.index(); r.index(); r.skip(12)
    _read_properties(pkg, r)
    count = r.i32(); r.i32()                   # DbNum, DbMax
    if not 0 <= count < 65536:
        raise ValueError("implausible poly count %d" % count)
    end = export["offset"] + export["size"]
    out = []
    for _ in range(count):
        n = r.index()
        if not 3 <= n <= 64 or r.p + (4 + n) * 12 > end:
            raise ValueError("implausible vertex count %d" % n)
        r.skip(12)                             # Base
        normal = struct.unpack_from("<fff", pkg.data, r.p); r.skip(12)
        r.skip(24)                             # TextureU, TextureV
        verts = [struct.unpack_from("<fff", pkg.data, r.p + i * 12)
                 for i in range(n)]
        r.skip(n * 12)
        r.skip(4)                              # PolyFlags
        r.index(); r.index(); r.index()        # Actor, Material, ItemName
        r.index(); r.index()                   # iLink, iBrushPoly
        r.skip(4)                              # LightMapScale
        out.append((normal, verts) if normals else verts)
    return out


def model_polys_index(pkg, export):
    """Export index of a UModel's Polys, or None.

    UModel::Serialize (Engine/Src/UnModel.cpp:162) writes Vectors, Points,
    Nodes, Surfs, Verts, NumSharedSides, NumZones, the Zones, and only then the
    Polys reference. Walking that exactly matters: scanning the model's bytes
    for something that merely looks like a Polys reference picks up a NEIGHBOUR
    -- DM-Corrugation's StaticMesh44 and StaticMesh45 were handed StaticMesh47's
    and StaticMesh48's hulls that way, and the rebuilt meshes then collided
    against geometry that belonged somewhere else.
    """
    r = Reader(pkg.data, export["offset"])
    if export["flags"] & RF_HAS_STACK:
        node = r.index(); r.index(); r.skip(8); r.skip(4)
        if node:
            r.index()
    start = r.p
    first = r.index()
    if not (0 <= first < len(pkg.names)) or pkg.names[first] != "None":
        r.p = start                            # no property list at all
    r.p += 12 + 12 + 1 + 16                    # UPrimitive: FBox + FSphere
    n = r.index(); r.p += 12 * n               # Vectors
    n = r.index(); r.p += 12 * n               # Points
    for _ in range(r.index()):                 # Nodes
        r.p += 16 + 8
        r.u8()
        for _ in range(7):
            r.index()
        r.p += 16
        r.u8(); r.u8(); r.u8()
        r.i32(); r.i32()
        r.p += 12
    for _ in range(r.index()):                 # Surfs
        r.index(); r.u32()
        for _ in range(4):
            r.index()
        r.index(); r.index()
        r.p += 16 + 4
    for _ in range(r.index()):                 # Verts
        r.index(); r.index()
    r.i32()                                    # NumSharedSides
    for _ in range(r.i32()):                   # Zones
        r.index(); r.p += 8 + 8 + 4
    ref = r.index()                            # Polys
    if 0 < ref <= len(pkg.exports) and pkg.class_of(pkg.exports[ref - 1]) == "Polys":
        return ref - 1
    return None


def static_mesh_bounds(pkg, export):
    """(min, max) of a UPrimitive's BoundingBox, read straight after its props."""
    r = Reader(pkg.data, export["offset"])
    if export["flags"] & 0x02000000:
        r.index(); r.index(); r.skip(12)
    _read_properties(pkg, r)
    lo = struct.unpack_from("<fff", pkg.data, r.p)
    hi = struct.unpack_from("<fff", pkg.data, r.p + 12)
    return lo, hi


def collision_polys(pkg, mesh_export, normals=False):
    """The collision hull polygons of a StaticMesh, or None.

    The CollisionModel reference comes from UStaticMesh::Serialize and its Polys
    from model_polys_index -- both exact walks. An earlier version scanned the
    Model's bytes for anything that parsed as a Polys reference and accepted the
    first candidate that fitted the mesh's bounding box; that is not selective
    enough on a map where neighbouring meshes overlap in space, and it silently
    handed three of DM-Corrugation's meshes the wrong hull.

    With normals=True each entry is (Normal, [FVector, ...]).
    """
    model = static_mesh_collision(pkg, mesh_export)
    if model is None:
        return None
    index = model_polys_index(pkg, pkg.exports[model])
    if index is None:
        return None
    try:
        polys = read_polys(pkg, index, normals=normals)
    except Exception:
        return None
    return polys or None
