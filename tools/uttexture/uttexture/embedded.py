"""Assets embedded in the source map package, for a rebuild shipping under a
new name.

These are the objects a map keeps inside its own .ut2 -- static meshes, a UV2
overlay, the level shot -- referenced as <MapPkg>.<Name>. While the rebuild
keeps the source map's name those references resolve to the rebuild itself and
nothing needs doing. Under any other name they resolve against the *original*
map instead, which both silently pulls in the old map as a dependency and, for
anything the rebuild has upscaled, quietly serves the old texture.

So they are carried into <Map>Tex alongside the upscaled textures, and t3d
repoints the references there. Textures come out through umodel: UCC's BMP
exporter handles only P8/RGBA8/G16 and, faced with a DXT texture, reports
success and writes a 0-byte file.
"""

import os, re, subprocess

ASSET_REF = re.compile(r"(StaticMesh|Texture|Material|Shader|Combiner|FinalBlend|"
                       r"MaterialSequence|TexPanner|TexScaler|TexRotator|Cubemap|"
                       r"Sound|Palette)'%s\.([\w\-.]+)'")


def scan(project):
    """{class: [(group, name)]} for every map-embedded asset the .t3d references."""
    from .t3d import find_export
    src = find_export(project.work)
    found = {}
    if not src:
        return found
    pattern = re.compile(ASSET_REF.pattern % re.escape(project.map))
    for line in open(src, encoding="latin-1"):
        for cls, path in pattern.findall(line):
            group, _, name = path.rpartition(".")
            found.setdefault(cls, set()).add((group, name))
    return {cls: sorted(v) for cls, v in found.items()}


TERRAIN_REF = re.compile(r"(?:TerrainMap|AlphaMap)=Texture'([\w\-.]+)'")


def terrain_textures(project):
    """Leaf names a TerrainInfo depends on -- heightmap and layer alpha maps.

    These must never be rebuilt. A terrain heightmap is TEXF_G16 and a layer's
    AlphaMap is TEXF_RGBA8, both typically tiny (16x16 on DM-1on1-Aerowalk), and
    the terrain code reads them as data rather than drawing them: the heightmap
    IS the geometry and the alpha map decides per-vertex layer blending.

    Carrying one through `#exec TEXTURE IMPORT` re-encodes it -- layer01 went in
    as DXT=1, a compressed format with no usable alpha -- and UT2004 then dies in
    UTerrainSector::GenerateTriangles the moment the editor draws the terrain.
    Upscaling one would be worse: it would change the terrain's resolution and
    so its shape.
    """
    from .t3d import find_export
    src = find_export(project.work)
    if not src:
        return set()
    return {m.rsplit(".", 1)[-1]
            for m in TERRAIN_REF.findall(open(src, encoding="latin-1").read())}


def export_textures(project, entries, log=print):
    """umodel the given (group, name) textures out of the map. Returns [(group, name, png)]."""
    from .survey import UMODEL, UMODEL_CMD, windows_path
    out = os.path.join(project.work, "embedded")
    os.makedirs(out, exist_ok=True)
    want = [(g, n) for g, n in entries
            if not os.path.exists(os.path.join(out, project.map, "Texture", n + ".png"))]
    if want:
        args = UMODEL_CMD + [ "-export", "-png",
                "-path=%s" % windows_path(project.install),
                "-out=%s" % windows_path(out)]
        args += ["-obj=%s" % n for _g, n in want] + [project.map]
        subprocess.run(args, capture_output=True, text=True, timeout=1800)
    made = []
    for group, name in entries:
        png = os.path.join(out, project.map, "Texture", name + ".png")
        if os.path.exists(png):
            made.append((group, name, png))
        else:
            log("  WARNING: could not export %s.%s" % (group, name))
    return made
