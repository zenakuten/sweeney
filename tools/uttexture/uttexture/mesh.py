"""Bring a map's own static meshes and sounds into the rebuilt package.

Assets embedded in the map package are referenced as <MapPkg>.<Name>, so under
a new name they have to travel with the rebuild: a .t3d carries references,
never assets. Leaving them pointing at the source map is not an option online.
UPackageMap::AddLinker (Core/Src/UnCoreNet.cpp:206) recurses the map's import
table, so the source map joins the net package map and every client is told it
needs it -- matched BY GUID (Engine/Src/UnPenLev.cpp:304). A client without that
exact build downloads it, or, if the server forbids downloads or the redirect
lacks it, is dropped with DownloadNotAllowed. For a stock map that is merely
wasteful; for a community map it is a broken server.

Two exporters are needed, because neither alone is enough:

* `UCC.exe batchexport <Map>.ut2 StaticMesh T3D` gives position, UV and the
  SMOOTHING MASK per triangle, but only names the material, and it dies on the
  first mesh it may not export. UStaticMeshExporterT3D::ExportText
  (Editor/Src/UnStaticMesh.cpp:1322) returns early writing NOTHING when
  RESTRICTEXPORT is on and ValidateAuthenticationKey() fails -- an Epic mesh
  copied into a community map. appSaveStringToFile then fails on the empty
  string (Core/Src/UnMisc.cpp) and UBatchExportCommandlet turns that into a
  fatal appErrorf, so ONE restricted mesh costs every mesh after it. The way
  past it is an empty file at that path: ExportToFile's NoReplaceIdentical
  check (Core/Src/UExporter.cpp:149) compares the empty buffer against the
  empty file, calls them identical and moves on. export_t3d() discovers the
  restricted meshes by retrying.

* umodel exports every mesh regardless, and its .pskx carries the one thing the
  T3D does not: the MATERIAL SLOT INDEX per face, plus the true slot count in
  MATT0000 and the per-slot material in the .props.txt beside it. It does not
  carry smoothing -- every face comes out in group 1.

The slot layout is why this matters. It is NOT "the distinct textures, in order
of first use": DM-1on1-Lea's PF_Ancient_Deco_Pillar declares five slots and uses
only slot 4, PF_Ancient_Pillarholder declares five and uses 0, 1, 3 and 4. The
actors' Skins(n) overrides index the ORIGINAL slots, and the ASE importer
silently rewrites any index past the end to 0 (Editor/Src/UnStaticMesh.cpp:1019),
so a rebuilt mesh has to declare the same number of slots in the same order or
the whole mesh takes slot 0's material. Nor can the slots be recovered from the
T3D's triangle order, which is not grouped by material.

So: geometry and smoothing from the T3D, slot index per face from the .pskx
matched on geometry, slot list from the .props.txt. A restricted mesh falls back
to the .pskx alone and loses its smoothing groups.

ASE specifics, all of them load-bearing (Editor/Src/UnStaticMesh.cpp):

* Positions are multiplied by FVector(-1,1,1) on import (:1013), so X is written
  negated and the two cancel. Face order is untouched, so winding survives.
* V is flipped on import (`V = 1.0 - ST.Y`, :1048), so V is written flipped.
* `*BITMAP` is stripped to its filename minus the last 5 characters and matched
  against the object name of an ALREADY LOADED texture, so the mesh imports must
  come after the texture imports in the generated .uc.
* A material is committed to the list only when `*UVW_V_TILING` is parsed
  (:706), so every *MAP_DIFFUSE block needs BITMAP, U tiling and V tiling in
  that order. Repeated *MAP_DIFFUSE blocks are what per-face *MESH_MTLID indexes.

`#exec STATICMESH IMPORT` is NOT the way to load one: that handler only accepts
LightWave .lwo (UnEdSrvExecImporters.cpp:426). ASE is registered by
UStaticMeshFactory (:415), reached through the generic factory exec. That exec's
PACKAGE= goes through CreatePackage -> ResolveName(..., Create=1), which splits
on dots, so PACKAGE=<Pkg>.<Group> reproduces the source map's group and the
references need only their package name swapped.

umodel's .pskx is in a mirrored space: Y is negated against the map's, and the
face winding is reversed to match. Negating Y and reversing the corners puts a
face back in the map's own coordinates.
"""

import os, re, struct, subprocess, sys

MESH_EXEC = "#exec NEW STANDALONE StaticMeshFactory FILE=Meshes\\%s NAME=%s PACKAGE=%s"
AUDIO_EXEC = "#exec AUDIO IMPORT FILE=Sounds\\%s NAME=%s"


# ---------------------------------------------------------------- exporting

def export_t3d(project, log=print):
    """batchexport every StaticMesh, working around restricted meshes.

    Returns {leaf: path}; a path may be an empty file, meaning the engine
    refused to export that mesh and only the .pskx is available for it.
    """
    out = os.path.join(project.work, "meshes")
    os.makedirs(out, exist_ok=True)
    system = os.path.join(project.install, "System")
    relative = os.path.relpath(project.map_file(), system).replace("/", "\\")
    windows = "Z:" + os.path.abspath(out).replace("/", "\\")
    blocked = []
    for _attempt in range(64):
        if [f for f in os.listdir(out) if f.lower().endswith(".t3d")] and not blocked:
            # A previous run already finished; keep it.
            if os.path.exists(os.path.join(out, ".complete")):
                break
        r = subprocess.run(["./UCC.exe", "batchexport", relative, "StaticMesh",
                            "T3D", windows],
                           cwd=system, capture_output=True, text=True, timeout=1800)
        # Core.int quotes the name in ExportOpen, but the build in this install
        # prints it bare -- accept either.
        stuck = re.search(r"couldn't open file '?(.+?)'?\s*$",
                          r.stdout or "", re.M)
        if not stuck:
            open(os.path.join(out, ".complete"), "w").close()
            break
        path = os.path.join(out, stuck.group(1).replace("\\", "/").rsplit("/", 1)[-1])
        if path in blocked:
            log("  WARNING: batchexport keeps stopping at %s" % os.path.basename(path))
            break
        blocked.append(path)
        open(path, "w").close()
    if blocked:
        log("  %d mesh(es) the engine will not export (RESTRICTEXPORT): %s"
            % (len(blocked), ", ".join(os.path.basename(p)[:-4] for p in blocked)))
    made = {}
    for f in os.listdir(out):
        if f.lower().endswith(".t3d"):
            made[f[:-4].split(".")[-1]] = os.path.join(out, f)
    return made


def export_umodel(project, log=print):
    """umodel the map's static meshes and sounds out. Returns (meshes, sounds).

    meshes is {leaf: (pskx, props)}, sounds is {leaf: wav}.
    """
    from .survey import UMODEL, UMODEL_CMD, windows_path
    out = os.path.join(project.work, "umodel")
    marker = os.path.join(out, ".complete")
    if not os.path.exists(marker):
        os.makedirs(out, exist_ok=True)
        subprocess.run(UMODEL_CMD + [ "-export", "-nomesh", "-noanim", "-novert",
                        "-sounds",
                        "-path=%s" % windows_path(project.install),
                        "-out=%s" % windows_path(out), project.map],
                       capture_output=True, text=True, timeout=1800)
        open(marker, "w").close()
    meshes, sounds = {}, {}
    root = os.path.join(out, project.map)
    mesh_dir = os.path.join(root, "StaticMesh")
    if os.path.isdir(mesh_dir):
        for f in os.listdir(mesh_dir):
            if f.endswith(".pskx") or f.endswith(".psk"):
                leaf = f.rsplit(".", 1)[0]
                props = os.path.join(mesh_dir, leaf + ".props.txt")
                meshes[leaf] = (os.path.join(mesh_dir, f),
                                props if os.path.exists(props) else None)
    sound_dir = os.path.join(root, "Sound")
    if os.path.isdir(sound_dir):
        for f in os.listdir(sound_dir):
            if f.lower().endswith(".wav"):
                sounds[f[:-4]] = os.path.join(sound_dir, f)
    return meshes, sounds


# ---------------------------------------------------------------- parsing

def parse_props(path):
    """[material leaf or None] per slot, from umodel's .props.txt."""
    if not path:
        return []
    def leaf(value):
        # Class'Group.Name' -> Name; umodel writes None for an empty slot.
        m = re.match(r"\w+'([^']+)'", value)
        return m.group(1).rsplit(".", 1)[-1] if m else None

    slots = []
    for line in open(path, encoding="latin-1"):
        s = line.strip()
        m = re.match(r"Materials\[\d+\] = \{ Material=(.+?),", s)
        if m:                                    # the one-line form
            slots.append(leaf(m.group(1)))
            continue
        m = re.match(r"Material = (.+)$", s)
        if m:
            slots.append(leaf(m.group(1).strip()))
    if not slots:
        m = re.search(r"Materials\[(\d+)\]", open(path, encoding="latin-1").read())
        if m:
            slots = [None] * int(m.group(1))
    return slots


def _chunks(path):
    d = open(path, "rb").read()
    o, out = 0, {}
    while o + 32 <= len(d):
        name = d[o:o + 20].split(b"\0")[0].decode("latin-1")
        _flags, size, count = struct.unpack("<iii", d[o + 20:o + 32])
        out[name] = (d[o + 32:o + 32 + size * count], size, count)
        o += 32 + size * count
    return out


def parse_pskx(path):
    """(positions, uvs, faces, nslots) in the MAP's coordinates.

    faces are (a, b, c, slot, smoothing) into the corner lists. Y is negated and
    the corners reversed to undo umodel's mirror; the corners stay unwelded so
    they match the shape the T3D path produces.
    """
    c = _chunks(path)
    b, sz, ct = c.get("PNTS0000", (b"", 12, 0))
    pts = [struct.unpack_from("<fff", b, i * sz) for i in range(ct)]
    b, sz, ct = c.get("VTXW0000", (b"", 16, 0))
    wedges = [struct.unpack_from("<HHff", b, i * sz) for i in range(ct)]
    b, sz, ct = c.get("FACE0000", (b"", 12, 0))
    raw = [struct.unpack_from("<HHHBBI", b, i * sz) for i in range(ct)]
    nslots = c.get("MATT0000", (b"", 88, 0))[2]
    positions, uvs, faces = [], [], []
    for w0, w1, w2, slot, _aux, smoothing in raw:
        base = len(positions)
        for w in (w2, w1, w0):                   # reversed: undo the mirror
            pi, _pad, u, v = wedges[w]
            x, y, z = pts[pi]
            positions.append((x, -y, z))
            uvs.append((u, v))
        faces.append((base, base + 1, base + 2, slot, smoothing))
    return positions, uvs, faces, nslots


def parse_t3d(path):
    """(positions, uvs, faces, textures) from an exported mesh .t3d.

    faces carry the triangle's TEXTURE index, not a slot -- the T3D does not
    record slots. Corners are kept unwelded: the export already splits vertices
    by UV, and welding them would merge corners the original kept apart.
    """
    positions, uvs, faces, textures = [], [], [], []
    texture, smoothing, corners = None, 1, []
    for line in open(path, encoding="latin-1"):
        s = line.strip()
        if s == "Begin Triangle":
            texture, smoothing, corners = None, 1, []
        elif s.startswith("Texture "):
            texture = s.split(None, 1)[1].strip()
        elif s.startswith("SmoothingMask "):
            smoothing = int(float(s.split()[1]))
        elif s.startswith("Vertex "):
            f = s.split()
            corners.append(([float(v) for v in f[2:5]], [float(v) for v in f[5:7]]))
        elif s == "End Triangle" and len(corners) == 3:
            leaf = (texture or "None").rsplit(".", 1)[-1]
            if leaf not in textures:
                textures.append(leaf)
            base = len(positions)
            for pos, uv in corners:
                positions.append(tuple(pos))
                uvs.append(tuple(uv))
            faces.append((base, base + 1, base + 2, textures.index(leaf), smoothing))
    return positions, uvs, faces, textures


# ---------------------------------------------------------------- merging

def _key(positions, face):
    return tuple(sorted(tuple(round(c, 2) for c in positions[i]) for i in face[:3]))


def merge(t3d, pskx, slots, log=print):
    """Geometry and smoothing from the T3D, slot index per face from the .pskx.

    Faces are matched on their corner positions, which the two exporters agree
    on once the mirror is undone. A face the .pskx has no match for falls back
    to the first slot carrying its texture, which is right whenever a texture
    appears on only one slot.
    """
    positions, uvs, faces, textures = t3d
    _pp, _pu, pfaces, _n = pskx
    by_key = {}
    for f in pfaces:
        by_key.setdefault(_key(_pp, f), []).append(f[3])
    out, guessed = [], 0
    for a, b, c, texture, smoothing in faces:
        k = _key(positions, (a, b, c))
        if by_key.get(k):
            slot = by_key[k].pop(0)
        else:
            leaf = textures[texture] if texture < len(textures) else None
            slot = slots.index(leaf) if leaf in slots else 0
            guessed += 1
        out.append((a, b, c, slot, smoothing))
    return out, guessed


# ---------------------------------------------------------------- writing


def hull_cannot_close(entries):
    """True when a hull has too few faces to enclose any volume.

    Deliberately a face count and not an edge-manifold test: hull polys come
    out of bspBuild, which leaves T-junctions, so plenty of perfectly good
    closed hulls have edges that are not shared by exactly two faces.
    """
    return bool(entries) and len(entries) < 4


def drop_open_hull(entries, name=""):
    """Discard a hull that cannot enclose a volume, leaving per-poly collision.

    An open hull has no well-defined inside, and the engine answers "is this
    point solid" and "does this sweep hit" differently for it -- which is
    invisible offline, where all movement is swept MoveActor calls, and breaks
    network clients, which also PLACE the pawn through ClientAdjustPosition ->
    SetLocation. DM-Corrugation's pipeend hull is a single quad and did exactly
    that.

    Closing it by extrusion was tried first and is worse: giving the quad a back
    face and four sides invents surfaces the original never had, and players
    walk up onto them or bump into them -- invisible, because nothing renders
    there. Dropping the hull instead falls back to the mesh's own triangles,
    which are well-defined for both tests and are what the player can see.
    """
    if not hull_cannot_close(entries):
        return entries
    sys.stderr.write("  %s: hull has %d poly(s) and cannot enclose a volume -- "
                     "dropped, mesh keeps per-polygon collision\n"
                     % (name or "mesh", len(entries)))
    return []


def write_ase(path, name, positions, uvs, faces, slots, collision=None):
    """One *MAP_DIFFUSE per ORIGINAL slot, in order, so Skins(n) still lands.

    `collision` is the source mesh's CollisionModel polygons as
    (Normal, [FVector, ...]) pairs. Without them the rebuilt mesh has NO
    CollisionModel and the engine falls back to per-poly collision on the render
    triangles -- which is what made DM-Rankin's planks impassable. A node whose
    *NODE_NAME starts with MCDCX is read as collision geometry
    (Editor/Src/UnStaticMesh.cpp:731) and becomes the hull.
    """
    out = ["*3DSMAX_ASCIIEXPORT\t200",
           "*COMMENT \"Converted from UT2004 by uttexture\"",
           "*SCENE {", "\t*SCENE_FILENAME \"%s\"" % name, "}",
           "*MATERIAL_LIST {", "\t*MATERIAL_COUNT 1", "\t*MATERIAL 0 {",
           "\t\t*MATERIAL_NAME \"%s\"" % name,
           "\t\t*MATERIAL_CLASS \"Standard\""]
    for i, texture in enumerate(slots or ["None"]):
        out += ["\t\t*MAP_DIFFUSE {",
                "\t\t\t*MAP_NAME \"%s_%d\"" % (name, i),
                "\t\t\t*MAP_CLASS \"Bitmap\"",
                "\t\t\t*BITMAP \"C:\\%s.dds\"" % (texture or "None"),
                "\t\t\t*UVW_U_TILING 1.0000",
                "\t\t\t*UVW_V_TILING 1.0000",
                "\t\t}"]
    out += ["\t}", "}",
            "*GEOMOBJECT {", "\t*NODE_NAME \"%s\"" % name, "\t*MESH {",
            "\t\t*TIMEVALUE 0",
            "\t\t*MESH_NUMVERTEX %d" % len(positions),
            "\t\t*MESH_NUMFACES %d" % len(faces),
            "\t\t*MESH_VERTEX_LIST {"]
    for i, (x, y, z) in enumerate(positions):
        out.append("\t\t\t*MESH_VERTEX    %d\t%.4f\t%.4f\t%.4f" % (i, -x, y, z))
    out += ["\t\t}", "\t\t*MESH_FACE_LIST {"]
    for i, (a, b, c, slot, smoothing) in enumerate(faces):
        out.append("\t\t\t*MESH_FACE %d:    A: %d B: %d C: %d"
                   " AB:    1 BC:    1 CA:    1\t*MESH_SMOOTHING %d\t*MESH_MTLID %d"
                   % (i, a, b, c, smoothing or 1, slot))
    out += ["\t\t}",
            "\t\t*MESH_NUMTVERTEX %d" % len(uvs),
            "\t\t*MESH_TVERTLIST {"]
    for i, (u, v) in enumerate(uvs):
        out.append("\t\t\t*MESH_TVERT %d\t%.5f\t%.5f\t0.0000" % (i, u, 1.0 - v))
    out += ["\t\t}",
            "\t\t*MESH_NUMTVFACES %d" % len(faces),
            "\t\t*MESH_TFACELIST {"]
    for i, (a, b, c, _s, _sm) in enumerate(faces):
        out.append("\t\t\t*MESH_TFACE %d\t%d\t%d\t%d" % (i, a, b, c))
    out += ["\t\t}", "\t}", "\t*MATERIAL_REF 0", "}"]
    if collision:
        # Triangulated as a fan and written REVERSED: the importer rebuilds each
        # poly from the face indices back to front (:976), so writing c,b,a
        # restores the hull's original winding -- which decides inside from
        # outside once the editor runs bspBuild over it.
        #
        # The winding alone is NOT the answer. bspBuild plane-splits on
        # FPoly::Normal, which is stored in the package and does not have to
        # agree with the vertex order; the importer throws the stored value away
        # and re-derives it from the winding (`Poly->CalcNormal(1)`, :982).
        # DM-Corrugation's StaticMesh0 has all 13 hull polys wound against their
        # normal, so a faithful winding gave it an INSIDE-OUT hull -- an
        # invisible solid you walk into, with nothing wrong on screen or in the
        # Karma view. Orient each fan by the stored normal instead.
        # An OPEN hull -- one whose edges are not each shared by two polys --
        # has no well-defined inside, and the engine answers "is this point
        # solid" and "does this sweep hit" differently for it. That costs
        # nothing offline, where movement is all swept MoveActor calls, but a
        # network client also PLACES its pawn (ClientAdjustPosition ->
        # SetLocation), and a placement test that disagrees with the sweep
        # leaves the client unable to accept corrections: it falls further
        # behind every tick and the player warps. DM-Corrugation's pipeend hull
        # is a single quad, and splitting it into the two coplanar triangles ASE
        # can carry was enough to trigger exactly that.
        #
        # Give such a hull thickness instead, so it encloses a real volume that
        # both tests agree on. The front face keeps its position and normal, so
        # what a player walks into does not move.
        collision = drop_open_hull(collision, name)
        verts, tris = [], []
        for entry in collision:
            # read_polys(normals=True) yields (Normal, [vertex, ...]); without
            # normals it yields the vertex list, whose first element is also a
            # tuple -- so test the SECOND element, not the first.
            paired = len(entry) == 2 and isinstance(entry[1], list)
            normal, poly = entry if paired else (None, entry)
            if len(poly) < 3:
                continue
            base = len(verts)
            verts.extend(poly)
            flip = False
            if normal is not None:
                a, b, c = poly[0], poly[1], poly[2]
                u = [b[i] - a[i] for i in range(3)]
                v = [c[i] - a[i] for i in range(3)]
                wound = (u[1] * v[2] - u[2] * v[1],
                         u[2] * v[0] - u[0] * v[2],
                         u[0] * v[1] - u[1] * v[0])
                flip = sum(wound[i] * normal[i] for i in range(3)) < 0
            for i in range(1, len(poly) - 1):
                tris.append((base, base + i, base + i + 1) if flip
                            else (base + i + 1, base + i, base))
        out += ["*GEOMOBJECT {", "\t*NODE_NAME \"MCDCX01\"", "\t*MESH {",
                "\t\t*TIMEVALUE 0",
                "\t\t*MESH_NUMVERTEX %d" % len(verts),
                "\t\t*MESH_NUMFACES %d" % len(tris),
                "\t\t*MESH_VERTEX_LIST {"]
        for i, (x, y, z) in enumerate(verts):
            out.append("\t\t\t*MESH_VERTEX    %d\t%.4f\t%.4f\t%.4f" % (i, -x, y, z))
        out += ["\t\t}", "\t\t*MESH_FACE_LIST {"]
        for i, (a, b, c) in enumerate(tris):
            out.append("\t\t\t*MESH_FACE %d:    A: %d B: %d C: %d"
                       " AB:    1 BC:    1 CA:    1\t*MESH_SMOOTHING 1\t*MESH_MTLID 0"
                       % (i, a, b, c))
        out += ["\t\t}", "\t}", "}"]
    out += [""]
    open(path, "wb").write("\r\n".join(out).encode("latin-1", "replace"))
    return path


# ---------------------------------------------------------------- driving

def build(project, pkg_dir, entries, slot_names=None, log=print):
    """Write an ASE per mesh. entries is [(group, name)]; returns [(group, name, tris)].

    `slot_names` renames a slot's material to whatever the rebuilt package calls
    it, since the ASE importer binds *BITMAP by object name.
    """
    meshes_dir = os.path.join(pkg_dir, "Meshes")
    os.makedirs(meshes_dir, exist_ok=True)
    t3ds = export_t3d(project, log=log)
    umeshes, _sounds = export_umodel(project, log=log)
    # The source package, for the collision hulls the ASE round trip cannot
    # otherwise carry.
    from . import ue2
    source = ue2.Package(project.map_file())
    hulls = {}
    exact = 0
    line_hull = []
    for export in source.exports:
        if source.class_of(export) != "StaticMesh":
            continue
        # UStaticMesh::UseSimpleBoxCollision defaults to TRUE
        # (Engine/Src/UnStaticMesh.cpp:52) and decides whether an extent trace
        # hits the CollisionModel hull or the render triangles
        # (Engine/Src/UnStaticMeshCollision.cpp:77). A mesh the author set to
        # FALSE is collided per-polygon, so its hull must NOT be carried: the
        # rebuilt mesh cannot be given the flag back from a .uc, and a hull the
        # engine then honours seals every gap the exact geometry left open.
        # DM-Corrugation's round room (StaticMesh39) and its ceiling beams
        # (StaticMesh33) became invisible walls exactly this way.
        props = source.properties(export)
        simple = props.get("UseSimpleBoxCollision")
        if simple is not None and not simple[0].raw[0]:
            exact += 1
            continue
        try:
            polys = ue2.collision_polys(source, export, normals=True)
        except Exception:
            polys = None
        if polys:
            hulls[export["name"]] = polys
            # UseSimpleLineCollision is the same switch for ZERO-extent traces
            # -- weapon fire -- and defaults the other way, to FALSE
            # (Engine/Src/UnStaticMesh.cpp:51). A mesh that serialises it is
            # therefore asking for TRUE: its shots stop at the simplified hull,
            # not at the exact triangles. The flag cannot be set from a .uc, so
            # the rebuilt mesh reverts to the default and shots start passing
            # through whatever the hull used to seal -- a grate, a bridge deck.
            # Nothing to fix automatically; say so, because it is silent.
            line = props.get("UseSimpleLineCollision")
            if line is not None and line[0].raw[0]:
                line_hull.append(export["name"])
    if hulls:
        log("  %d of the map's meshes carry a collision hull" % len(hulls))
    if exact:
        log("  %d mesh(es) set UseSimpleBoxCollision=False -- hull dropped so the"
            " rebuild keeps per-polygon collision" % exact)
    if line_hull:
        log("  NOTE: %d mesh(es) set UseSimpleLineCollision=True and keep a hull"
            " -- weapon traces hit the hull in the source and the exact"
            " triangles in the rebuild: %s"
            % (len(line_hull), ", ".join(sorted(line_hull))))
    made = []
    for group, name in entries:
        pskx_path, props_path = umeshes.get(name, (None, None))
        if not pskx_path:
            log("  WARNING: %s not exported by umodel, left on the source map" % name)
            continue
        slots = parse_props(props_path)
        pskx = parse_pskx(pskx_path)
        if not slots:
            slots = [None] * max(pskx[3], 1)
        t3d_path = t3ds.get(name)
        note = ""
        if t3d_path and os.path.getsize(t3d_path) > 0:
            t3d = parse_t3d(t3d_path)
            faces, guessed = merge(t3d, pskx, slots, log=log)
            positions, uvs = t3d[0], t3d[1]
            if guessed:
                note = ", %d face(s) slotted by texture" % guessed
        else:
            # Restricted mesh: the .pskx is all there is, and it has no
            # smoothing groups, so the mesh comes back fully smoothed.
            positions, uvs, faces, _n = pskx
            note = ", pskx only (smoothing groups lost)"
        used = max((f[3] for f in faces), default=0) + 1
        if used > len(slots):
            slots = slots + [None] * (used - len(slots))
        bound = [(slot_names or {}).get(m, m) for m in slots]
        missing = sum(1 for m in slots
                      if m and slot_names is not None and m not in slot_names)
        hull = hulls.get(name)
        if not hull:
            note += ", NO collision hull (per-poly collision)"
        stem = ("%s.%s" % (group, name)) if group else name
        write_ase(os.path.join(meshes_dir, stem + ".ase"),
                  name, positions, uvs, faces, bound, collision=hull)
        if missing:
            note += ", %d slot(s) not in this package" % missing
        made.append((group, name, len(faces)))
        log("  %-38s %4d triangles, %d slots%s"
            % (stem, len(faces), len(slots), note))
    return made


def build_sounds(project, pkg_dir, entries, log=print):
    """Copy the map's embedded sounds in. entries is [(group, name)]."""
    import shutil
    _meshes, wavs = export_umodel(project, log=log)
    out = os.path.join(pkg_dir, "Sounds")
    os.makedirs(out, exist_ok=True)
    made = []
    for group, name in entries:
        src = wavs.get(name)
        if not src:
            log("  WARNING: sound %s not exported, left on the source map" % name)
            continue
        shutil.copyfile(src, os.path.join(out, name + ".wav"))
        made.append((group, name))
    return made


# ------------------------------------------------- meshes in other packages

def package_slots(path, only=None):
    """{mesh name: {slot index: (full material path, class)}} from a package.

    A static mesh in a RETAIL package binds its own skins, so a map drawing one
    shows the original textures however much of the map is rebuilt -- and that
    is most mesh actors on most maps (DM-DE-Ironic has 935, of which 2 are its
    own). Giving those actors a Skins() override pointing at the 4K texture is
    what brings them up, and it needs the mesh's slot list.

    Read from the package rather than exported: UStaticMesh.Materials is an
    ordinary tagged array of FStaticMeshMaterial (EnableCollision, Material),
    so no umodel run and no disk are needed for what is only a name per slot.
    """
    from . import ue2
    pkg = ue2.Package(path)
    out = {}
    for export in pkg.exports:
        if pkg.class_of(export) != "StaticMesh":
            continue
        if only is not None and export["name"] not in only:
            continue
        try:
            props = pkg.properties(export)
        except Exception:
            continue
        if "Materials" not in props:
            continue
        r = ue2.Reader(props["Materials"][0].raw)
        slots = {}
        try:
            for i in range(r.index()):
                entry = ue2._read_properties(pkg, r)
                ref = entry.get("Material")
                if not ref:
                    continue
                idx = ue2.Reader(ref[0].raw).index()
                full = pkg.ref_path(idx)
                if full:
                    cls = (pkg.imports[-idx - 1]["class"] if idx < 0
                           else pkg.class_of(pkg.exports[idx - 1]))
                    slots[str(i)] = (full, cls)
        except Exception:
            continue
        if slots:
            out[export["name"]] = slots
    return out


_PKG_INDEX = {}


def _find_package(install, name):
    """Path of a content package, matched without regard to case."""
    if not _PKG_INDEX:
        for folder in ("StaticMeshes", "Textures", "Animations", "Maps", "Sounds"):
            d = os.path.join(install, folder)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                stem, _dot, ext = f.rpartition(".")
                if stem and ext.lower() in ("usx", "utx", "ukx", "ut2", "uax"):
                    _PKG_INDEX.setdefault(stem.lower(), os.path.join(d, f))
    return _PKG_INDEX.get(name.lower())


def external_slots(install, map_name, t3d_path, log=print):
    """{mesh leaf: {slot: material leaf}} for meshes in OTHER packages."""
    import collections
    want = collections.defaultdict(set)
    pattern = re.compile(r"StaticMesh=StaticMesh'([\w\-]+)\.(?:[\w\-]+\.)*([\w\-]+)'")
    for line in open(t3d_path, encoding="latin-1"):
        m = pattern.search(line)
        if m and m.group(1).lower() != map_name.lower():
            want[m.group(1)].add(m.group(2))
    out, refs = {}, {}
    for package, names in sorted(want.items()):
        # Case-insensitively: UE package names are case-insensitive and the
        # maps do not match the filenames -- 2k4ChargerMeshes on disk is
        # 2K4chargerMESHES.usx.
        path = _find_package(install, package)
        if not path:
            log("  external meshes: %s not found on disk" % package)
            continue
        found = package_slots(path, only=names)
        for mesh_name, slots in found.items():
            out[mesh_name] = {}
            for slot, (full, cls) in slots.items():
                leaf = full.rsplit(".", 1)[-1]
                out[mesh_name][slot] = leaf
                # The map never imports these -- the MESH package does -- so
                # the survey cannot see them unless they are handed over with
                # their full path and class.
                refs.setdefault(leaf, (full, cls))
        if len(found) < len(names):
            log("  %s: %d of %d meshes had no material list"
                % (package, len(names) - len(found), len(names)))
    return out, refs
