"""Work out what a map needs upscaled, from the map itself.

Two sources, because neither alone is complete:

  * the level's BSP surface array names every material the world geometry uses,
    including ones embedded in the map (myLevel textures).
  * the embedded static meshes carry their own materials, which never appear in
    the surface array. Those come from `umodel -dump`, because a UStaticMesh's
    Materials is a native C++ array rather than a tagged property list.

Only materials that actually *draw* are collected: a mesh section with
NumFaces=0 is declared but never rendered, and several maps carry a handful.

What comes back is split by class. Plain Textures can be upscaled and
repointed. Composites -- Shader, Combiner, TexPanner and friends -- cannot be
upscaled directly; they are graphs over other textures, and are reported so the
caller can decide whether to rebuild them (see pkg.sky_chain for the one case
this tool automates).
"""

import os, re, subprocess

COMPOSITE = {"Shader", "Combiner", "FinalBlend", "MaterialSequence", "TexPanner",
             "TexScaler", "TexRotator", "TexOscillator", "TexEnvMap", "Cubemap",
             "ColorModifier", "ConstantColor", "FadeColor", "OpacityModifier",
             "MaterialSwitch", "TexCoordSource", "VertexColor"}

from .sweeney import umodel as _umodel, umodel_cmd as _umodel_cmd
UMODEL = _umodel()
UMODEL_CMD = _umodel_cmd()


def umodel_dump(root, package, objects, out_path):
    """`umodel -dump` for a set of objects, cached on disk.

    The cache is keyed on the OBJECT SET, not just the file's existence. A
    survey that grows -- external mesh slots pulling in textures the map never
    imported -- would otherwise reuse a dump that predates them, and a texture
    with no properties silently falls back to ALPHA=0 DXT=1: alpha destroyed,
    format wrong, windows opaque. glass01 lost bAlphaTexture exactly this way.
    """
    want = "# objects: %s" % ",".join(sorted(objects))
    if os.path.exists(out_path):
        with open(out_path, encoding="latin-1") as fh:
            if fh.readline().rstrip("\n") == want:
                return out_path
    args = UMODEL_CMD + [ "-dump", "-path=%s" % windows_path(root)]
    args += ["-obj=%s" % o for o in objects]
    args.append(package)
    r = subprocess.run(args, capture_output=True, text=True, timeout=1800)
    text = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                     if "fixme" not in l and "wineusb" not in l)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="latin-1").write(want + "\n" + text)
    return out_path


def windows_path(p):
    return "Z:" + os.path.abspath(p).replace("/", "\\")


# umodel prints a struct on ONE line when it fits and expands it over several
# when it does not, so the same field arrives in two shapes:
#
#   Materials[0] = { Material=Texture'trim.dwtrim_base', EnableCollision=true }
#
#   Materials[0] =
#   {
#       Material = Shader'DetailArchitecture.MetlPart_U00G425v2Final'
#       EnableCollision = true
#   }
#
# Reading only the compact form loses every slot of a mesh whose material path
# is long -- the mesh then has NO materials to upscale, nothing is accrued for
# it, and t3d.py emits no Skins(), so the carried mesh keeps an ASE *BITMAP
# naming a material that does not exist in the rebuild and renders with the
# default texture. DM-Elucidation's HolotableWallPart01 (two Movers and a wall
# panel by the 100 health) came out that way.
_LEAF = re.compile(r"\w+'(?:[\w\-]+\.)*([\w\-]+)'$")


def mesh_materials(dump_path):
    """{mesh: {slot: material leaf}} for slots whose section actually draws."""
    out, current, leaves = {}, None, {}
    # The array header (`Materials[2] =`) is printed before its elements
    # (`Materials[0] =`, `Materials[1] =`), so the most recent index seen is
    # always the element being described -- true for both shapes.
    mat_index = sec_index = None
    for line in open(dump_path, encoding="latin-1"):
        text = line.strip()
        head = re.match(r"ClassName: StaticMesh ObjectName: (\S+)", text)
        if head:
            current, leaves = out.setdefault(head.group(1), {}), {}
            mat_index = sec_index = None
            continue
        if current is None:
            continue

        m = re.match(r"Materials\[(\d+)\] = \{ Material=(\S+?), ", text)
        if m:                                       # compact material
            leaf = _LEAF.match(m.group(2))
            if leaf:
                leaves[int(m.group(1))] = leaf.group(1)
            continue
        m = re.match(r"Sections\[(\d+)\] = \{ .*NumFaces=(\d+) \}", text)
        if m:                                       # compact section
            if int(m.group(2)) > 0 and int(m.group(1)) in leaves:
                current[int(m.group(1))] = leaves[int(m.group(1))]
            continue

        m = re.match(r"Materials\[(\d+)\] =$", text)
        if m:
            mat_index = int(m.group(1))
            continue
        m = re.match(r"Sections\[(\d+)\] =$", text)
        if m:
            sec_index = int(m.group(1))
            continue
        m = re.match(r"Material = (\S+)$", text)    # expanded, either array
        if m and mat_index is not None:
            leaf = _LEAF.match(m.group(1))
            if leaf and mat_index not in leaves:
                leaves[mat_index] = leaf.group(1)
            continue
        m = re.match(r"NumFaces = (\d+)$", text)    # expanded section
        if m and sec_index is not None:
            if int(m.group(1)) > 0 and sec_index in leaves:
                current[sec_index] = leaves[sec_index]
            continue
    return out


def t3d_materials(t3d_path):
    """{leaf: count} for every material the exported .t3d names.

    The import table says what a map *can* reference; this says what it
    actually does, and catches two cases the BSP surface array misses entirely:
    brush polygons that produce no visible surface (wal18leftHA, glass03 and
    MaterialBackdrop in Roughinery all have brush polys and zero surfaces), and
    actor-level Skins() overrides, which is how Briteface1 reaches 63 light
    meshes without being any mesh's own material.
    """
    counts = {}
    if not t3d_path or not os.path.exists(t3d_path):
        return counts
    pattern = re.compile(r"(?:Texture=|Skins\(\d+\)=\w+')([\w\-.]+)")
    for line in open(t3d_path, encoding="latin-1"):
        for ref in pattern.findall(line):
            leaf = ref.rstrip("'").rsplit(".", 1)[-1]
            counts[leaf] = counts.get(leaf, 0) + 1
    return counts


def survey(pkg, root, meta_dir, t3d_path=None, keep_unused=False, include=()):
    """-> dict describing everything the map needs."""
    # every reference the map can make, indexed by leaf name
    by_leaf, classes = {}, {}
    for name, path in pkg.imports_of_class(*(["Texture"] + sorted(COMPOSITE))):
        by_leaf.setdefault(name, path)
        classes.setdefault(name, None)
    for i, entry in enumerate(pkg.imports):
        classes[entry["name"]] = entry["class"]
    for name, path, _ in pkg.exports_of_class(*(["Texture"] + sorted(COMPOSITE))):
        full = "%s.%s" % (pkg.name, path)
        by_leaf.setdefault(name, full)
        classes.setdefault(name, None)
    for i, entry in enumerate(pkg.exports):
        cls = pkg.class_of(entry)
        if cls in COMPOSITE or cls == "Texture":
            classes[entry["name"]] = cls

    bsp = {}
    for surf in pkg.surfaces():
        if surf["material"]:
            bsp[surf["material"]] = bsp.get(surf["material"], 0) + 1

    meshes = [n for n, _, _ in pkg.exports_of_class("StaticMesh")]
    mesh_mats = {}
    if meshes:
        dump = umodel_dump(root, pkg.name, meshes,
                           os.path.join(meta_dir, "meshes.txt"))
        mesh_mats = mesh_materials(dump)

    # Meshes in OTHER packages bind their own skins, so without an override a
    # map shows original-resolution textures on almost every mesh actor. Their
    # slot lists join the map's own: t3d injects Skins() from this same dict,
    # and the materials become texture candidates so 4K versions get built.
    from . import mesh as mesh_mod
    external, external_refs = (mesh_mod.external_slots(root, pkg.name, t3d_path)
                               if t3d_path else ({}, {}))
    for name, slots in external.items():
        mesh_mats.setdefault(name, slots)
    for leaf, (full, cls) in external_refs.items():
        by_leaf.setdefault(leaf, full)
        if cls:
            classes.setdefault(leaf, cls)

    from_t3d = t3d_materials(t3d_path)
    mesh_used = {leaf for slots in mesh_mats.values() for leaf in slots.values()}

    # A map that draws through its own Shaders hides its real textures one level
    # down: the survey sees wall1shader on 200 surfaces and never sees the
    # eX_wall_01_d underneath it. Those textures are what needs upscaling, so
    # pull each rebuildable composite apart and count its slots as used.
    all_composites = {}
    for i, entry in enumerate(pkg.exports):
        if pkg.class_of(entry) in COMPOSITE:
            all_composites[entry["name"]] = "%s.%s" % (pkg.name, pkg.export_path(i))
    for i, imp in enumerate(pkg.imports):
        if imp["class"] in COMPOSITE:
            all_composites.setdefault(imp["name"], pkg.import_path(-(i + 1)))
    # A terrain layer's texture is the visible ground but is neither a BSP
    # surface nor a mesh slot, so nothing marks it used.
    layer_used = {}
    if t3d_path:
        from .terrain import layer_textures
        import types as _t
        layer_used = layer_textures(_t.SimpleNamespace(work=os.path.dirname(t3d_path)))
        for leaf, path in layer_used.items():
            by_leaf.setdefault(leaf, path)
            classes.setdefault(leaf, "Texture")

    composite_used = set()
    if all_composites:
        from .composite import (scan as scan_composites, texture_refs,
                                reference_map)
        import types
        stub = types.SimpleNamespace(install=root, map=pkg.name, meta=meta_dir,
                                     map_file=lambda: pkg.path)
        defs = scan_composites(stub, all_composites)
        refs = texture_refs(defs)
        composite_used = set(refs)
        # A texture only an external composite names is absent from the map's
        # name table, so it is not a candidate at all until it is added here.
        resolver = reference_map(stub, {p.split(".")[0]
                                        for p in all_composites.values()})
        for leaf in refs:
            if leaf not in by_leaf and leaf in resolver:
                by_leaf[leaf] = resolver[leaf]
                classes.setdefault(leaf, "Texture")

    # Everything the map can name, annotated with how it is actually reached.
    textures, composites, unused = {}, {}, {}
    for leaf, path in sorted(by_leaf.items()):
        entry = {"path": path,
                 "group": path.split(".")[1] if path.count(".") >= 2 else "",
                 "surfaces": bsp.get(path, 0),
                 "t3d_refs": from_t3d.get(leaf, 0),
                 "mesh": leaf in mesh_used,
                 "composite": leaf in composite_used or leaf in layer_used}
        entry["used"] = bool(entry["surfaces"] or entry["t3d_refs"]
                             or entry["mesh"] or entry["composite"])
        # `include` rescues a specific never-drawn texture without dragging in
        # the rest. The sky texture needs it: a rebuilt sky chain names it
        # directly, but the map only ever reaches it through a composite, so
        # the survey cannot see it being drawn. keep_unused would also pull in
        # the level shots and the detail textures, and a detail texture must
        # NOT land in the main list -- uttexture/detail.py owns those, and having it
        # in both means one of the two definitions silently loses.
        if not entry["used"] and not keep_unused and leaf not in include:
            # Not dropped silently: these are reachable, just not drawn. A map's
            # meshes routinely declare materials on sections with NumFaces=0,
            # and a texture reached only through a composite or another
            # texture's Detail slot shows up here too -- which matters, because
            # rebuilding a sky chain needs exactly those.
            unused[leaf] = entry
            continue
        (composites if classes.get(leaf) in COMPOSITE else textures)[leaf] = entry

    surfaces = pkg.surfaces()
    return {"map": pkg.name, "textures": textures, "composites": composites,
            "unused": unused, "meshes": mesh_mats, "surfaces": len(surfaces),
            "lightmap_scales": sorted({s["lightmap_scale"] for s in surfaces})}
