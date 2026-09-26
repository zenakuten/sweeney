"""Write the texture package source: TGAs plus the generated .uc.

The TGAs go in uncompressed and the DXT compression is left to
`#exec TEXTURE IMPORT ... DXT=n`, which runs the engine's own nvDXT compressor
and builds the mip chain with UTexture::CreateMips -- the same path the stock
textures took. A pre-compressed .dds also imports, but then the whole mip chain
has to be built and packed by hand, because CreateMips is a no-op on an
already-DXT surface.

Rows go out bottom-up. UT2004's TGA importer flips scanlines unconditionally
and never reads the descriptor byte (Editor/Src/UnEdFact.cpp:2498), so a
top-down file imports upside down -- which is invisible on a seamless texture
and unmistakable on anything with a top and a bottom.
"""

import os, subprocess, sys

from . import composite as composite_mod, detail as detail_mod, embedded, mesh
from . import terrain as terrain_mod
from .images import write_tga
from .materials import MaterialSet

# The skydome material chain, when a map's config names one. Order is dependency
# order and must stay that way: ImportProperties walks defaultproperties line by
# line, so a block naming an object defined below it resolves to None.
SKY_CHAIN = [
    ("TexPanner", "SkyPanA",    [("PanDirection", "(Yaw=0,Pitch=0,Roll=0)"),
                                 ("PanRate", "%(rate_a)s"), ("Material", "@tex")]),
    ("TexPanner", "SkyPanB",    [("PanDirection", "(Yaw=0,Pitch=0,Roll=0)"),
                                 ("PanRate", "%(rate_b)s"), ("Material", "@tex")]),
    ("TexPanner", "SkyPanMask", [("PanDirection", "(Yaw=%(mask_yaw)s,Pitch=0,Roll=0)"),
                                 ("PanRate", "%(rate_mask)s"), ("Material", "@tex")]),
    ("TexScaler", "SkyScaleA",  [("UScale", "%(scale_a)s"), ("VScale", "%(scale_a)s"),
                                 ("Material", "@SkyPanA")]),
    ("TexScaler", "SkyScaleB",  [("UScale", "%(scale_b)s"), ("VScale", "%(scale_b)s"),
                                 ("Material", "@SkyPanB")]),
    ("Combiner",  "SkyClouds",  [("CombineOperation", "CO_AlphaBlend_With_Mask"),
                                 ("AlphaOperation", "AO_Use_Mask"),
                                 ("Material1", "@SkyScaleA"), ("Material2", "@SkyScaleB"),
                                 ("Mask", "@SkyPanMask")]),
    # No ZWrite here: it is a FinalBlend property, not a Shader one, so UCC
    # warns "Unknown property in defaults" and drops it. Nothing is lost --
    # OB_Translucent already sets ZWrite=0 (D3D9MaterialState.cpp:1558).
    ("Shader",    "SkyDome",    [("Diffuse", "@SkyClouds"), ("Opacity", "@SkyClouds"),
                                 ("OutputBlending", "OB_Translucent"),
                                 ("ModulateStaticLighting2X", "True")]),
]

# Engine/Material.uc:19. The name, never the index: UCC silently discards an
# integer assigned to an enum in defaultproperties, so SurfaceType=3 would leave
# it EST_Default with no error.
SURFACE_TYPES = [
    "EST_Default", "EST_Rock", "EST_Dirt", "EST_Metal", "EST_Wood", "EST_Plant",
    "EST_Flesh", "EST_Ice", "EST_Snow", "EST_Water", "EST_Glass",
] + ["EST_Custom%02d" % i for i in range(18)]


def wrap_materials(ms, package, manifest, detail_divisor=1, with_detail=True,
                   detail_exclude=()):
    """A Shader per texture carrying Detail/DetailScale/SurfaceType.

    `#exec TEXTURE IMPORT` cannot set those three, and no #exec can reach them
    afterwards: POKE lives in UObject::StaticExec (Core/Src/UnObj.cpp:2999),
    which UEditorEngine::SafeExec never falls through to -- it returns 0
    (Editor/Src/UnEdSrv.cpp:387), and its OBJ handler takes only LOAD and
    IMPORT. Both `#exec POKE` and `#exec OBJ POKE` are therefore silent no-ops,
    which is how a package builds clean while every one of these stays at its
    default: no close-range detail grain, and default footstep sounds.

    A Shader can hold them, and the renderer prefers a Shader's own Detail over
    the diffuse texture's (D3D9Drv/Src/D3D9MaterialState.cpp:1141). SurfaceType
    lives on UMaterial, so it rides along. Returns {texture leaf: shader name}.
    """
    wrappers, details = {}, set()
    by_group = {r["name"]: r["group"] for r in manifest["textures"]}
    for rec in manifest["textures"]:
        # A detail layer suits most surfaces and actively hurts some -- a
        # texture whose own artwork already reads as fine detail gets muddied
        # by a second layer of it. detail_exclude drops it per texture while
        # leaving SurfaceType, so the surface keeps its footstep sounds.
        detail = (with_detail and rec["detail"] and rec["detail_source"] != "None"
                  and rec["name"] not in detail_exclude)
        if not detail and not rec["surface_type"]:
            continue
        name = rec["name"]
        path = "Texture'%s.%s.%s'" % (package, rec["group"], name)
        props = [("Diffuse", path)]
        # A Shader does NOT honour its diffuse texture's bAlphaTexture or
        # bMasked -- transparency comes from the Shader's own Opacity slot, and
        # with Opacity=None it draws the texture opaque
        # (D3D9MaterialState.cpp:1520). Wrapping a transparent texture without
        # this turns a cut-out into a solid rectangle: DM-1on1-Backspace's moon
        # came back as an opaque quad against the sky. Opacity pointing at the
        # texture itself supplies the alpha, and OB_Masked gives the 127 alpha
        # test that bMasked means (:1538).
        if rec.get("alpha_texture") or rec["masked"]:
            props.append(("Opacity", path))
            if rec["masked"]:
                props.append(("OutputBlending", "OB_Masked"))
        if detail:
            # The detail texture stays in its own package at its own size, so
            # DetailScale carries over unchanged: detail UVs are the surface's
            # normalised UVs times DetailScale, and rescaling TextureU/V with
            # the texture left those untouched.
            leaf = rec["detail_source"].split("'")[1].rsplit(".", 1)[-1]
            # A detail texture that is ALSO drawn as ordinary artwork is
            # imported under its own group, not DetailTextures -- importing it
            # twice would define the same object twice. The wrapper has to name
            # it where it really is, or the reference dangles and the package
            # will not compile (DM-Under's detail1).
            own = by_group.get(leaf)
            if own:
                props.append(("Detail", "Texture'%s.%s.%s'" % (package, own, leaf)))
            else:
                details.add(leaf)
                props.append(("Detail", "Texture'%s.%s.%s'"
                              % (package, detail_mod.GROUP, leaf)))
            # Dividing DetailScale enlarges the detail tile in world space,
            # which is the only way to make the grain repeat less often. It is
            # paired with a proportionally larger detail texture, so the grain
            # keeps its apparent size and simply repeats later.
            ds = float(rec["detail_scale"]) / detail_divisor
            props.append(("DetailScale", ("%g" % ds)))
        if rec["surface_type"]:
            props.append(("SurfaceType", SURFACE_TYPES[rec["surface_type"]]))
        wrappers[name] = ms.add("Shader", name, props)
    return wrappers, sorted(details)


SKY_DEFAULTS = {"rate_a": "-0.007500", "rate_b": "-0.001000",
                "rate_mask": "-0.002500", "mask_yaw": "8192",
                "scale_a": "0.500000", "scale_b": "2.000000"}


def sky_materials(ms, package, sky):
    """The sky dome's shader path, or None. Adds to the shared MaterialSet."""
    if not sky:
        return None
    params = dict(SKY_DEFAULTS)
    params.update({k: v for k, v in sky.items() if k != "texture"})
    tex = "Texture'%s.%s'" % (package, sky["texture"])
    made = {}
    for kind, base, props in SKY_CHAIN:
        resolved = []
        for key, value in props:
            if value == "@tex":
                value = tex
            elif value.startswith("@"):
                value = ms.path(made[value[1:]])
            else:
                value = value % params
            resolved.append((key, value))
        made[base] = ms.add(kind, base, resolved)
    return ms.path(made["SkyDome"])


def to_tga(png, tga, width, height, sharpen="", injection=None):
    cmd = ["magick", png]
    if sharpen:
        cmd += ["-channel", "RGB", "-unsharp", sharpen, "+channel"]
    cmd += ["-depth", "8", "RGBA:-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    if len(raw) != width * height * 4:
        raise SystemExit("%s: %d bytes, expected %d" % (png, len(raw), width * height * 4))
    if injection:
        import numpy as np
        from .inject import inject
        scan, grain, amount = injection
        arr = np.frombuffer(raw, np.uint8).reshape(height, width, 4).astype(np.float32)
        rgb, _ = inject(arr[:, :, :3], scan, 4, grain, amount)
        arr[:, :, :3] = np.clip(rgb, 0, 255)          # alpha untouched
        raw = arr.astype(np.uint8).tobytes()
    bgra = bytearray(raw)
    bgra[0::4], bgra[2::4] = raw[2::4], raw[0::4]     # RGBA -> BGRA
    write_tga(tga, width, height, bytes(bgra))


def build(project, manifest, out_root=None, log=print):
    from .project import SCANS
    package = project.package
    root = out_root or project.install
    pkg_dir = os.path.join(root, package)
    tex_dir, cls_dir = os.path.join(pkg_dir, "Textures"), os.path.join(pkg_dir, "Classes")
    os.makedirs(tex_dir, exist_ok=True)
    os.makedirs(cls_dir, exist_ok=True)

    # With a shared content store the map package keeps only what is its own:
    # map-embedded textures, its level shot, its meshes and sounds. Everything
    # that came out of a content package lives in <Source>4K and is referenced
    # there, so importing it here again would ship a second copy.
    if project.config.get("shared_content"):
        from . import content as content_mod
        owned = content_mod.shared_leaves(project.install, manifest)
        if owned:
            manifest = dict(manifest,
                            textures=[r for r in manifest["textures"]
                                      if r["name"] not in owned])
            log("shared content covers %d texture(s); %d stay in %s"
                % (len(owned), len(manifest["textures"]), package))

    sharpen = project.config.get("sharpen", "")
    inject_cfg = project.config.get("inject", {})
    imports, total = [], 0
    ms = MaterialSet(package)
    for rec in manifest["textures"]:
        name = rec["name"]
        png = os.path.join(project.up, name + ".png")
        if not os.path.exists(png):
            raise SystemExit("missing upscaled texture: " + png)
        injection = None
        if name in inject_cfg:
            spec = inject_cfg[name]
            injection = (os.path.join(SCANS, spec["scan"]),
                         spec.get("grain", 2.0), spec.get("amount", 8.0))
        tga = os.path.join(tex_dir, name + ".tga")
        to_tga(png, tga, rec["out_width"], rec["out_height"], sharpen, injection)
        total += os.path.getsize(tga)

        opts = ["NAME=%s" % name, "GROUP=%s" % rec["group"],
                "FILE=Textures\\%s.tga" % name,
                # bAlphaTexture, NOT "does it have an alpha channel" -- see
                # manifest.py. DXT= below is what preserves the channel.
                "ALPHA=%d" % (1 if rec.get("alpha_texture") else 0),
                "DXT=%d" % rec["dxt"], "LODSET=%d" % rec["lodset"],
                "NORMALLOD=%d" % rec["normal_lod"]]
        if rec["masked"]:
            opts.append("MASKED=1")
        if not rec["wrap_u"]:
            opts.append("UCLAMPMODE=Clamp")
        if not rec["wrap_v"]:
            opts.append("VCLAMPMODE=Clamp")
        imports.append("#exec TEXTURE IMPORT " + " ".join(opts))


    # Assets the source map kept inside its own .ut2 travel with the rebuild
    # when it ships under a new name -- see uttexture/embedded.py. They are appended
    # AFTER the upscaled imports on purpose: an ASE binds its *BITMAP against
    # textures already loaded in the package.
    meshes, sounds, carried, unbuildable = [], [], [], []
    mesh_entries = []
    carried_refs, uncarried = [], []
    carried_details, carried_shown = [], []
    if project.renamed:
        found = embedded.scan(project)
        # A map-embedded texture that the survey already picked up is in the
        # manifest and imported above, with its group and flags. Carrying it
        # again here would define the same object a second time, at the package
        # root and without them.
        in_manifest = {r["name"] for r in manifest["textures"]}
        terrain = set(terrain_mod.referenced(project))
        wanted = [(g, n) for g, n in found.get("Texture", [])
                  if n not in in_manifest and n not in terrain]
        if project.config.get("level_shot"):
            # It must be a TEXTURE. A map's level shot is very often a
            # MaterialSequence cycling several screenshots (DM-Structured's
            # "Preview" cycles Shot00062..65), and umodel cannot export one --
            # the shot silently goes missing and the map shows no preview.
            shot = project.config["level_shot"]
            from .ue2 import Package as _Pkg
            source = _Pkg(project.map_file())
            cls = [source.class_of(e) for e in source.exports
                   if e["name"] == shot]
            if cls and cls[0] != "Texture":
                log("level_shot %s is a %s, not a Texture -- the map will have"
                    " no preview. Name one of its frames instead."
                    % (shot, cls[0]))
            wanted.append(("LevelShot", shot))
        for group, name, png in embedded.export_textures(project, wanted, log=log):
            from .extract import probe
            width, height, alpha, _mean = probe(png)
            tga = os.path.join(tex_dir, name + ".tga")
            to_tga(png, tga, width, height)
            total += os.path.getsize(tga)
            opts = ["NAME=%s" % name, "FILE=Textures\\%s.tga" % name,
                    "ALPHA=%d" % (1 if alpha else 0), "DXT=%d" % (5 if alpha else 1)]
            if group:
                opts.insert(1, "GROUP=%s" % group)
            imports.append("#exec TEXTURE IMPORT " + " ".join(opts))
            carried.append("%s.%s" % (group, name) if group else name)
            carried_refs.append("%s.%s" % (group, name) if group else name)
        # A map's own meshes and sounds have to travel with it: see mesh.py on
        # why a reference left on the source map is a broken server, not just a
        # wasted download. Every mesh is carried, group and all -- the generic
        # factory exec's PACKAGE= creates groups, so <Pkg>.<Group>.<Name> is
        # reproducible and the references need only their package name swapped.
        # The build itself waits until the wrappers and rebuilt composites
        # exist: an ASE binds *BITMAP by OBJECT NAME, so a slot whose material
        # this package renamed (bas08go -> bas08go_SH) has to be written under
        # the new name or the slot imports with no material at all.
        if found.get("StaticMesh") and project.config.get("carry_meshes", True):
            mesh_entries = found["StaticMesh"]
        if found.get("Sound"):
            sounds = mesh.build_sounds(project, pkg_dir, found["Sound"], log=log)
            for group, name in sounds:
                carried_refs.append("%s.%s" % (group, name) if group else name)

        # A level shot the config replaces is handled by t3d, not left dangling.
        shot_group = "LevelShot" if project.config.get("level_shot") else None
        unbuildable = ["%s'%s.%s'" % (cls, g, n) if g else "%s'%s'" % (cls, n)
                       for cls, entries in sorted(found.items())
                       if cls not in ("Texture", "StaticMesh", "Sound")
                       for g, n in entries if g != shot_group]

    mode = project.config.get("detail_mode", "copy")
    # How much bigger the synthesised grain tile is, and so how much of the
    # repeat goes away. It is independent of the texture upscale: a larger
    # factor fills more area with invented content, which trades the repeat
    # against how far the grain drifts from the original.
    factor = 1
    if mode in ("synth", "upscale"):
        factor = int(project.config.get("detail_factor")
                     or project.config.get("scale", 4))
    # "none" drops the detail layer entirely. Shaders are still emitted for
    # anything carrying a SurfaceType, since footstep and impact sounds have
    # nothing to do with the grain.
    # Rebuild the map's own Shaders before anything else adds to the set, so a
    # surface drawn through one can be repointed here instead of staying on the
    # source map. Slots that named a texture this package upscaled follow it.
    survey = project.load("survey.json") or {}
    # Every composite the map draws through, its own and the content packages'
    # alike. An external one is copied into this package over upscaled textures;
    # the original package is not touched, this map just stops using it.
    own = {n: e.get("path", "") for n, e in survey.get("composites", {}).items()
           if e.get("path")}
    rebuilt = {}
    if own:
        # Follow composite-to-composite references first: one named only by
        # another composite is invisible to the survey, and leaving it behind
        # keeps the rebuilt package depending on the SOURCE MAP.
        own = composite_mod.closure(project, own, log=log)
        defs = composite_mod.scan(project, own, log=log)
        by_leaf = {r["name"]: r for r in manifest["textures"]}
        repoint = {n: "%s.%s.%s" % (package, r["group"], n)
                   for n, r in by_leaf.items()}
        # A composite slot that names a texture the shared store owns has to
        # follow it there -- the manifest above was filtered to map-local
        # textures, so without this the slot keeps the ORIGINAL retail texture
        # and the composite renders at source resolution. The PLAIN texture,
        # not the Shader wrapper: a wrapper would add a detail layer and a
        # SurfaceType the original composite never had.
        if project.config.get("shared_content"):
            from . import content as content_mod
            repoint.update(content_mod.texture_map(
                content_mod.load_index(project.install)))
        names = composite_mod.rebuild(ms, package, defs, repoint,
                                      refs=composite_mod.reference_map(
                                          project,
                                          {p.split(".")[0] for p in own.values()}),
                                      log=log)
        for orig, (kind, new_name) in names.items():
            # The surfaces' UVs are in texels of the shader's diffuse, so they
            # rescale by that texture's factor exactly as a plain one would.
            diff = composite_mod.OBJ_REF.match(
                defs[orig][1].get(composite_mod.primary_slot(kind), "None"))
            leaf = diff.group(2).rsplit(".", 1)[-1] if diff else None
            rebuilt[orig] = {"name": new_name, "class": kind,
                             "scale": by_leaf.get(leaf, {}).get("scale", 1)}
        log("%d map Shaders rebuilt into %s" % (len(names), package))
        # Whatever is left is a class umodel would not dump, so scan() never
        # saw it. Those are read straight out of the package instead -- a
        # map-owned one is a dependency on the source map, which must not ship.
        leftover = {n for n in own if n not in rebuilt}
        native, colours, native_diffuse = composite_mod.rebuild_native(
            project, ms, package, leftover, repoint,
            refs=composite_mod.reference_map(
                project, {p.split(".")[0] for p in own.values()}), log=log)
        for orig, (kind, new_name) in native.items():
            # Same rule as a rebuilt Shader: the surfaces' UVs are in the
            # texels of the texture underneath, so they follow ITS factor. With
            # scale 1 the texture renders 4x zoomed -- DM-1on1-Backspace's red
            # and orange light panels looked like the wrong texture entirely.
            leaf = native_diffuse.get(orig)
            rebuilt[orig] = {"name": new_name, "class": kind,
                             "scale": by_leaf.get(leaf, {}).get("scale", 1)}
        if native:
            log("%d composite(s) rebuilt from the package itself: %s"
                % (len(native), ", ".join("%s (%s)" % (n, k)
                                          for n, (k, _v) in sorted(native.items()))))
        # Flat colours become ordinary textures in a group, not materials.
        for orig, rgba in sorted(colours.items()):
            tga = os.path.join(tex_dir, orig + ".tga")
            write_tga(tga, 8, 8, bytes([rgba[2], rgba[1], rgba[0], rgba[3]]) * 64)
            total += os.path.getsize(tga)
            imports.append("#exec TEXTURE IMPORT NAME=%s GROUP=Colours "
                           "FILE=Textures\\%s.tga ALPHA=0 DXT=1" % (orig, orig))
            rebuilt[orig] = {"name": "Colours.%s" % orig, "class": "Texture",
                             "scale": 1}
        if colours:
            log("%d flat ColorModifier(s) became plain textures: %s"
                % (len(colours), ", ".join("%s rgb(%d,%d,%d)" % (n, c[0], c[1], c[2])
                                           for n, c in sorted(colours.items()))))
        project.store("composites.json", rebuilt)
        # A composite this package cannot rebuild still lives in its own
        # package, and a mesh slot that named one has nothing to bind to here:
        # an ASE binds *BITMAP by object name and that object is not in this
        # package. Record where it really is so t3d can write an explicit
        # Skins() for it -- DM-1on1-Backspace's tower grates bind
        # FinalBlend'Plutonic_BP2_textures.grate.CatwalkGrateNate' on slot 0
        # and imported with the default texture without this.
        external = {}
        full = composite_mod.reference_map(
            project, {p.split(".")[0] for p in own.values()})
        for name, (cls, _props) in sorted(defs.items()):
            if name in rebuilt:
                continue
            path = full.get(name) or own.get(name)
            if path:
                external[name] = [cls, path]
        project.store("external_materials.json", external)
        if external:
            log("%d composite(s) stay in their own package; mesh slots get an"
                " explicit Skins(): %s"
                % (len(external), ", ".join("%s'%s'" % (c, p)
                                            for c, p in external.values())))

    wrappers, detail_leaves = wrap_materials(ms, package, manifest,
                                             detail_divisor=factor,
                                             with_detail=(mode != "none"),
                                             detail_exclude=set(
                                                 project.config.get("detail_exclude", [])))
    # Now the meshes. An ASE binds *MAP_DIFFUSE/*BITMAP against an already
    # loaded material BY OBJECT NAME (Editor/Src/UnStaticMesh.cpp:680), so each
    # slot has to name what this package actually contains: a Shader wrapper
    # (bas08go -> bas08go_SH), a rebuilt composite
    # (cp_Mechdecofloor2_shad -> cp_Mechdecofloor2_shad_SH), a flat colour
    # turned texture, or the plain texture under its own name. A slot left
    # pointing at a name the package does not have imports with NO material and
    # the mesh renders in the default texture -- DM-1on1-Backspace's lift and
    # its tower grates did exactly that.
    slot_names = {}
    # A plain texture keeps its own name, so it maps to itself; anything this
    # package renamed maps to the new name. A slot missing from this map has no
    # object in the package at all and needs a Skins() override instead.
    for r in manifest["textures"]:
        slot_names[r["name"]] = r["name"]
    for leaf, shader in wrappers.items():
        slot_names[leaf] = shader
    for orig, rec in rebuilt.items():
        slot_names[orig] = rec["name"].rsplit(".", 1)[-1]
    # A slot whose material now lives in a shared content package binds by the
    # name it has THERE -- and that package has to be loaded while this one
    # compiles, or the ASE importer has nothing to match against.
    shared_refs = project.load("content_refs.json") or {}
    shared_loads = set()
    if shared_refs and mesh_entries:
        wanted = {leaf for _g, name in mesh_entries
                  for leaf in ((project.load("survey.json") or {})
                               .get("meshes", {}).get(name, {}) or {}).values()}
        for leaf in sorted(wanted):
            ref = shared_refs.get(leaf)
            if not ref:
                continue
            slot_names[leaf] = ref[0].rsplit(".", 1)[-1]
            shared_loads.add(ref[0].split(".")[0])
    if mesh_entries:
        meshes = mesh.build(project, pkg_dir, mesh_entries, slot_names, log=log)
        for group, name, _tris in meshes:
            carried_refs.append("%s.%s" % (group, name) if group else name)
        made = {name for _g, name, _t in meshes}
        for group, name in mesh_entries:
            if name not in made:
                uncarried.append("StaticMesh'%s'"
                                 % ("%s.%s" % (group, name) if group else name))
    project.store("carried.json", sorted(set(carried_refs)))

    # A detail texture can also be drawn on a surface in its own right, in which
    # case it is already in the manifest above and importing it again would
    # define the same object twice.
    already = {r["name"] for r in manifest["textures"]}
    detail_pngs = detail_mod.export(
        project, [d for d in detail_leaves if d not in already], log=log)
    strength = float(project.config.get("detail_strength", 1.0))
    if detail_pngs and mode == "upscale":
        # Paired with DetailScale/factor, exactly as synth is: the tile grows in
        # world space so the grain keeps its size and repeats less often, and
        # the upscaler fills the extra area with its own stylisation rather than
        # with spectrum-matched noise.
        dm = project.config.get("detail_model") or project.config["model"]
        dmd = project.config.get("detail_models_dir") or project.config["models_dir"]
        log("upscaling detail textures %dx with %s, DetailScale/%d:"
            % (factor, dm, factor))
        detail_pngs = detail_mod.upscale(project, detail_pngs, factor, dm, dmd,
                                         strength=strength, log=log)
    elif detail_pngs and mode == "synth":
        log("synthesising detail textures %dx, DetailScale/%d so the grain keeps"
            % (factor, factor))
        log("  its size and repeats %dx less often per axis:" % factor)
        detail_pngs = detail_mod.synthesize(project, detail_pngs, factor,
                                            strength=strength, log=log)
    for leaf, png in sorted(detail_pngs.items()):
        from .extract import probe
        width, height, alpha, _mean = probe(png)
        tga = os.path.join(tex_dir, leaf + ".tga")
        to_tga(png, tga, width, height)
        total += os.path.getsize(tga)
        # Uncompressed: these are a few tens of KB, and re-compressing grain
        # that was already DXT once would add a second generation of loss to
        # the one thing whose whole job is high-frequency.
        imports.append("#exec TEXTURE IMPORT NAME=%s GROUP=%s FILE=Textures\\%s.tga"
                       " ALPHA=%d LODSET=0"
                       % (leaf, detail_mod.GROUP, leaf, 1 if alpha else 0))
        carried_details.append(leaf)
        carried_shown.append("%s (%dx%d)" % (leaf, width, height))
    project.store("wrappers.json", wrappers)
    sky_path = sky_materials(ms, package, project.config.get("sky"))
    decl, sky = (ms.declaration(), ms.emit()) if len(ms) else (None, [])
    body = ["// Generated by uttexture -- do not edit by hand.",
            "// %s: %d textures upscaled %dx." % (manifest["map"],
                                                  len(manifest["textures"]),
                                                  manifest["scale"]),
            "// Build with: ucc make   (EditPackages=%s)" % package,
            "class %s extends Object" % package, "    abstract;", ""]
    terrain_lines, terrain_repoint = terrain_mod.build(project, pkg_dir, log=log)
    if terrain_lines:
        project.store("terrain.json", terrain_repoint)
        body += ["",
                 "// Terrain read straight out of the source package and re-imported",
                 "// the way the engine expects: G16 heightmap via 16-bit BMP, RGBA8",
                 "// alpha maps, both MIPS=off. These are data, not pictures."]
        body += terrain_lines
    body += imports
    if shared_loads:
        body += ["",
                 "// Shared 4K content the meshes below bind against: an ASE",
                 "// matches *BITMAP by object name among LOADED materials, so",
                 "// these have to be loaded while this package compiles."]
        body += ["#exec OBJ LOAD FILE=..\\Textures\\%s.utx PACKAGE=%s" % (p, p)
                 for p in sorted(shared_loads)]
    if meshes:
        body += ["", "// Static meshes the source map embedded in its own .ut2.",
                 "// Not STATICMESH IMPORT -- that exec takes LightWave .lwo only;",
                 "// ASE is UStaticMeshFactory, through the generic factory exec."]
        # PACKAGE=<Pkg>.<Group> reproduces the source map's group: the exec's
        # CreatePackage -> ResolveName(..., Create=1) splits the name on dots.
        body += [mesh.MESH_EXEC % (("%s.%s" % (g, n) if g else n) + ".ase", n,
                                   ("%s.%s" % (package, g)) if g else package)
                 for g, n, _tris in meshes]
    if sounds:
        body += ["", "// Sounds the source map embedded in its own .ut2."]
        body += [mesh.AUDIO_EXEC % (n + ".wav", n) + (" GROUP=%s" % g if g else "")
                 for g, n in sounds]
    body += [""] + ([decl, ""] if decl else []) + ["defaultproperties", "{"]
    body += sky
    body += ["}", ""]
    uc = os.path.join(cls_dir, package + ".uc")
    open(uc, "wb").write("\r\n".join(body).encode("latin-1", "replace"))

    log("%d textures, %.1f MB of TGA -> %s%s" % (
        len(manifest["textures"]), total / 1e6, tex_dir,
        ("  [unsharp %s]" % sharpen) if sharpen else ""))
    if inject_cfg:
        log("scan detail injected into: %s" % ", ".join(sorted(inject_cfg)))
    if sky_path:
        log("sky material: %s" % sky_path)
    if wrappers:
        log("%d Shader wrappers carry Detail/SurfaceType (no #exec can set those"
            % len(wrappers))
        log("  on a Texture); t3d points the surfaces at them")
    if carried_shown:
        log("detail textures copied in: %s" % ", ".join(carried_shown))
    if carried:
        log("carried from %s: %s" % (project.map, ", ".join(carried)))
    if uncarried:
        log("%d map-embedded meshes NOT carried (export failed); their references"
            % len(uncarried))
        log("  stay on %s, so the rebuild is NOT self-contained:" % project.map)
        for ref in uncarried[:6]:
            log("    %s" % ref)
        if len(uncarried) > 6:
            log("    ... and %d more" % (len(uncarried) - 6))
    if unbuildable:
        log("NOT carried -- composites the package cannot rebuild, still point at %s:"
            % project.map)
        for ref in unbuildable:
            log("  %s" % ref)
    log("")
    log("INI:     System/UT2004.ini EditPackages must list %s" % package)
    log("         and NOT the packages already finished -- UCC loads every one")
    log("         it is given, even the ones it skips compiling.")
    log("BUILD:   cd %s/System && rm -f %s.u && ./UCC.exe make" % (root, package))
    # mv, not cp: Paths= searches ../System/*.u BEFORE ../Textures/*.utx
    # (UT2004.ini [Core.System]), so leaving the .u behind shadows the .utx --
    # you end up testing one file and shipping the other.
    log("         mv %s.u ../Textures/%s.utx" % (package, package))
    # UEditorEngine::Init (Editor/Src/UnEditor.cpp:106) LoadPackage()s every
    # EditPackages entry BY NAME at startup, and ../Textures/*.utx is on the
    # Paths, so leaving this one listed makes the editor load the package under
    # its own name before the import. The generated materials are subobjects of
    # the generated CLASS, and a class is unique by name in memory, so they stay
    # attached to that pre-loaded copy and never appear under MyLevel -- every
    # surface then imports with a NULL material while the textures, which have
    # ordinary group outers, come in fine.
    log("THEN:    REMOVE EditPackages=%s from System/UT2004.ini" % package)
    log("         BEFORE starting the editor, or the import silently loses")
    log("         every material (NULL brush references, wrong textures).")
    log("IMPORT:  File > New, then in the editor console:")
    log("           OBJ LOAD FILE=..\\Textures\\%s.utx PACKAGE=MyLevel" % package)
    log("         (embed only, and BEFORE the import -- check the Texture")
    log("          Browser shows package MyLevel populated, or the import fails)")
    log("         File > Import the .t3d (not File > Open), then Build")
    log("         Geometry, Lighting and Paths, and save as %s" % project.out_map)
    log("AFTER:   grep -c '^Game=' System/CacheRecords.ucl   -> must be 12")
    log("         rm -f Maps/Auto[0-9].ut2                   -> editor autosaves")
    return uc, sky_path
