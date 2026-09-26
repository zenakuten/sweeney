"""Carry an existing map's terrain into the rebuilt package.

A map's terrain does not survive a plain .t3d round trip: the built .ut2 keeps a
separate `TerrainSector` export that `batchexport ... Level T3D` never writes, so
the import has a TerrainInfo with no sector data behind it and UnrealEd dies in
UTerrainSector::GenerateTriangles. Proved with a control import of the STOCK
map's own export -- identical crash, nothing rewritten.

ut3converter shows the shape of the answer: terrain it *generates* works, because
it puts a G16 heightmap and RGBA8 alpha maps in the package it builds and points
a fully specified TerrainInfo at them, leaving the editor to construct the
sectors itself. This does the same for a map that already exists -- reads the
heightmap and alpha maps straight out of the source package and re-imports them
the way the engine expects.

Two details are load-bearing, both from ut3converter/convert/terrain.py:

* The heightmap must be TEXF_G16, and the only importer that yields one is the
  16-bit BMP path (Editor/Src/UnEdFact.cpp:2191).
* Both must be imported `MIPS=off`. Terrain textures are read as data -- the
  heightmap IS the geometry and the alpha map decides per-vertex layer blending
  -- so a mip chain is meaningless and re-encoding them is destructive. Carrying
  layer01 as DXT=1 (compressed, no usable alpha) is what crashed this map first.
"""

import os, re, struct, sys

GROUP = "Terrain"
TERRAIN_REF = re.compile(r"(?:TerrainMap|AlphaMap)=Texture'([\w\-.]+)'")
# Layers(0)=(Texture=Texture'ArboreaTerrain.ground.Sand01AR',AlphaMap=...)
LAYER_REF = re.compile(r"Layers\(\d+\)=\(Texture=Texture'([\w\-.]+)'")

TEXF_RGBA8, TEXF_G16 = 5, 10


def referenced(project):
    """Leaf names of the heightmap and alpha maps the exported .t3d names."""
    from .t3d import find_export
    src = find_export(project.work)
    if not src:
        return []
    seen, out = set(), []
    for path in TERRAIN_REF.findall(open(src, encoding="latin-1").read()):
        leaf = path.rsplit(".", 1)[-1]
        if leaf not in seen:
            seen.add(leaf)
            out.append(leaf)
    return out


def layer_textures(project):
    """{leaf: full path} for the textures a terrain's layers paint with.

    These are ordinary pictures -- ArboreaTerrain's sand, moss and bark on
    DM-Corrugation -- and they are the visible ground, so they want upscaling
    like any wall. They are invisible to the survey because a terrain layer is
    not a BSP surface and not a mesh slot, so nothing "draws" them.

    Distinct from the heightmap and the per-layer AlphaMap, which are data and
    must be carried verbatim.
    """
    from .t3d import find_export
    src = find_export(project.work)
    if not src:
        return {}
    out = {}
    for path in LAYER_REF.findall(open(src, encoding="latin-1").read()):
        out.setdefault(path.rsplit(".", 1)[-1], path)
    return out


def _write_tga32(path, width, height, bgra):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                         width, height, 32, 0)
    stride = width * 4
    with open(path, "wb") as f:
        f.write(header)
        for y in range(height - 1, -1, -1):      # bottom-up, as the importer wants
            f.write(bgra[y * stride:(y + 1) * stride])


def build(project, pkg_dir, log=print):
    """Write the terrain textures into the package. Returns [#exec lines]."""
    leaves = referenced(project)
    if not leaves:
        return [], {}
    from .sweeney import ut3converter
    _conv = ut3converter()
    if not _conv:
        raise SystemExit("terrain needs ut3converter; set tools.ut3converter "
                         "in ~/.sweeney/config.json")
    sys.path.insert(0, _conv)
    from ut2.bmp import write_bmp16
    from .ue2 import Package, texture_mip0

    pkg = Package(project.map_file())
    out_dir = os.path.join(pkg_dir, GROUP)
    os.makedirs(out_dir, exist_ok=True)
    lines, repoint = [], {}
    for leaf in leaves:
        export = pkg.export_named(leaf, "Texture")
        if export is None:
            log("  WARNING: terrain texture %s is not in %s" % (leaf, project.map))
            continue
        w, h, fmt, data = texture_mip0(pkg, export)
        if fmt == TEXF_G16 and len(data) == w * h * 2:
            values = struct.unpack("<%dH" % (w * h), data)
            write_bmp16(os.path.join(out_dir, leaf + ".bmp"), w, h, values)
            lines.append("#exec TEXTURE IMPORT NAME=%s GROUP=%s FILE=%s\\%s.bmp MIPS=off"
                         % (leaf, GROUP, GROUP, leaf))
            log("  %-14s %dx%d G16 heightmap -> 16-bit BMP" % (leaf, w, h))
        elif fmt == TEXF_RGBA8 and len(data) == w * h * 4:
            # The package stores BGRA already; the TGA writer wants the same.
            _write_tga32(os.path.join(out_dir, leaf + ".tga"), w, h, data)
            lines.append("#exec TEXTURE IMPORT NAME=%s GROUP=%s FILE=%s\\%s.tga"
                         " ALPHA=1 MIPS=off" % (leaf, GROUP, GROUP, leaf))
            log("  %-14s %dx%d RGBA8 alpha map -> TGA" % (leaf, w, h))
        else:
            log("  WARNING: %s is format %d with %d bytes -- left on %s"
                % (leaf, fmt, len(data), project.map))
            continue
        repoint[leaf] = "%s.%s.%s" % (project.package, GROUP, leaf)
    return lines, repoint
