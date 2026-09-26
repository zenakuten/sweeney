"""Export a map to .t3d and rewrite it against the upscaled package.

Four jobs, and the second is the one that silently ruins the result if missed:

1. Repoint every material that has an upscaled counterpart -- brush polygons and
   the Skins() overrides already on actors.

2. Rescale the brush UVs. UE2 states a polygon's texture coordinates in *texels*
   (texel = (Vertex - Origin) dot TextureU), so a 4x larger texture covers 4x
   more world space at the same TextureU and every surface reads as zoomed in.
   Multiplying TextureU/TextureV by the same factor the texture grew restores
   the original tiling exactly. Origin needs no adjustment: UE2 brush polys
   carry no Pan, it is folded into Origin, which is a world point on the
   surface. Static meshes need nothing -- their UVs are normalised.

3. Override static mesh materials with Skins(n), the only way to reach materials
   baked into a mesh (AActor::GetSkin, consulted first by UStaticMesh::GetSkin;
   a None entry falls through to the mesh). Only slots whose section actually
   draws are touched.

4. Drop the level scaffolding belonging to the *old* map, and relink the rest.
   Left alone, Brush=Model'<Map>.ModelNNN' resolves against the original
   package -- which is loaded, because the meshes come from it -- and the
   importer takes that model instead of the inline one, throwing away the
   rescaled UVs with no error at all.

   Assets embedded in the map package (static meshes, the level shot) are the
   one exception, and only while the rebuild keeps the source map's name: then
   <MapPkg>.<Name> resolves to the new map itself and must be left alone. Under
   a new name the same reference quietly points back at the original map, so it
   is relinked to MyLevel and reported -- those objects have to be imported into
   the new map by hand, since a .t3d carries references, never the assets.
"""

import os, re, subprocess, sys

DROP_PROPERTIES = ("Level", "Region", "PhysicsVolume", "StaticMeshInstance",
                   "ColLocation", "ColLocation2", "Summary")
DROP_PREFIXES = ("PathList(",)

ASSET_CLASSES = {
    "StaticMesh", "Texture", "Material", "Shader", "Combiner", "FinalBlend",
    "MaterialSequence", "TexPanner", "TexScaler", "TexRotator", "TexOscillator",
    "TexEnvMap", "Cubemap", "ColorModifier", "ConstantColor", "FadeColor",
    "OpacityModifier", "MaterialSwitch", "TexCoordSource", "VertexColor",
    "Sound", "Palette",
}


def num(v):
    """UnrealEd's canonical fixed-width float, e.g. +00512.000000."""
    return "%+013.6f" % v


def find_export(work):
    """The exported t3d, whatever the commandlet decided to call it.

    It names the file after the ULevel object and the extension as passed, and
    both vary between installs -- myLevel.t3d here, MyLevel.T3D there -- so
    match on extension rather than assuming either.
    """
    import glob
    hits = [f for f in glob.glob(os.path.join(work, "*"))
            if f.lower().endswith(".t3d")]
    return max(hits, key=os.path.getmtime) if hits else None


def export(project, log=print):
    """ucc batchexport the map to .t3d. Returns the path."""
    out = find_export(project.work)
    if out:
        return out
    system = os.path.join(project.install, "System")
    # The commandlet wants a path relative to System with backslashes, and a
    # Z:\ Windows path for the destination.
    relative = os.path.relpath(project.map_file(), system).replace("/", "\\")
    windows = "Z:" + os.path.abspath(project.work).replace("/", "\\")
    r = subprocess.run(["./UCC.exe", "batchexport", relative, "Level", "T3D", windows],
                       cwd=system, capture_output=True, text=True, timeout=1800)
    out = find_export(project.work)
    if not out:
        sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
        raise SystemExit("batchexport produced no t3d")
    log("exported %s" % os.path.relpath(out, project.dir))
    return out


def rewrite(project, manifest, src, dst, embed=False, sky_slot=None, log=print):
    package = "MyLevel" if embed else project.package
    map_pkg = project.map
    # Detail/DetailScale/SurfaceType cannot be set on an imported Texture by any
    # #exec, so pkg.py wraps the textures that need them in a Shader that holds
    # them instead. Those surfaces have to reference the Shader, not the texture
    # underneath it, or the wrapper is built and never used.
    wrappers = project.load("wrappers.json") or {}
    plain = {r["name"]: "%s.%s.%s" % (package, r["group"], r["name"])
             for r in manifest["textures"]}
    # A texture the shared store owns is NOT in the map package, so a terrain
    # layer naming it there references nothing and the terrain draws as
    # invisible. The plain texture, never the Shader wrapper: a terrain layer
    # is drawn by the terrain renderer, which a wrapper does not serve.
    plain.update(project.load("content_plain.json") or {})
    # A map Shader rebuilt into the package: the surfaces that drew through it
    # point at the rebuilt copy, and rescale by its diffuse texture's factor
    # because their UVs are in that texture's texels.
    rebuilt = project.load("composites.json") or {}
    # Terrain textures the package now carries; the TerrainInfo must name them
    # there, or the terrain still depends on the source map.
    terrain = project.load("terrain.json") or {}
    # A generated material is a subobject of the generated CLASS, so its real
    # path carries the package name twice
    # (`Shader DM1on1BackspaceTex.DM1on1BackspaceTex.bas08go_SH`). Naming it in
    # full does NOT help: UObject::ResolveName walks intermediate components as
    # UPackage, and a UClass does not match, so the full path cannot resolve at
    # all. The short form is what works -- a saved DM-1on1-Backspace4K has its
    # materials at <Map>.DM1on1BackspaceTex.bas08go_SH, reached from
    # `MyLevel.bas08go_SH`.
    materials = package

    new = {}
    for orig, rec in rebuilt.items():
        # The class travels with the path: a ColorModifier rebuilt here will
        # not resolve behind a Shader' prefix.
        new[orig] = ("%s.%s" % (materials, rec["name"]), rec.get("scale", 1),
                     rec.get("class", "Shader"))
    # Composites that stay in their own package: a mesh slot naming one has to
    # be given the full original path, since nothing in this package answers to
    # that name.
    for leaf, (cls, path) in (project.load("external_materials.json") or {}).items():
        new.setdefault(leaf, (path, 1, cls))
    for r in manifest["textures"]:
        leaf = r["name"]
        if leaf in wrappers:
            new[leaf] = ("%s.%s" % (materials, wrappers[leaf]), r["scale"], "Shader")
        else:
            new[leaf] = ("%s.%s.%s" % (package, r["group"], leaf), r["scale"], "Texture")
    # LAST, so it wins: a texture the shared store owns is not in the map
    # package at all, and the loop above would otherwise point the map at a
    # <Map>Tex path that holds nothing.
    # Shared 4K content packages win over anything the map package holds: the
    # map package no longer imports these at all.
    for leaf, (path, scale, cls) in (project.load("content_refs.json") or {}).items():
        new[leaf] = (path, scale, cls)
    meshes = {m: slots for m, slots in (project.load("survey.json") or {}).get("meshes", {}).items()}

    stats = dict(polys=0, scaled=0, repointed=0, skins=0, dropped=0,
                 models=0, relinked=0, imports=0, left=0)
    imports = set()
    carried = set(project.load("carried.json") or [])
    level_shot = project.config.get("level_shot") if project.renamed else None
    # The map's own screenshot object -- usually a MaterialSequence the package
    # cannot rebuild -- is often ALSO painted on a BSP surface (a monitor). That
    # surface keeps a Texture=<SourceMap>.<Object> reference, which puts the
    # source map in the import table and so in the net package map: a client
    # without the original map cannot join. Point it at the rebuilt level shot.
    shot_leaf = None
    if level_shot:
        for raw in open(src, encoding="latin-1"):
            m = re.match(r"\s*Screenshot=\w+'([\w\-.]+)'", raw)
            if m:
                shot_leaf = m.group(1).rsplit(".", 1)[-1]
                break
    out, poly_scale = [], 1
    actor_lines, actor_mesh = None, None

    def flush():
        nonlocal actor_lines, actor_mesh
        if actor_lines is None:
            return
        slots = meshes.get(actor_mesh or "", {})
        have = {int(m.group(1)) for m in
                (re.match(r"\s*Skins\((\d+)\)=", l) for l in actor_lines) if m}
        add = []
        for i in sorted(slots, key=int):
            leaf = slots[i]
            if int(i) in have or leaf not in new:
                continue
            add.append("    Skins(%s)=%s'%s'" % (i, new[leaf][2], new[leaf][0]))
        if sky_slot and actor_mesh == sky_slot[0] and sky_slot[1] not in have:
            add.append("    Skins(%d)=Shader'%s.SkyDome_SH'" % (sky_slot[1], materials))
        if add:
            stats["skins"] += len(add)
            actor_lines[-1:-1] = add
        out.extend(actor_lines)
        actor_lines, actor_mesh = None, None

    # A TerrainInfo cannot survive this round trip. The .t3d carries the actor's
    # properties but not the sector data the engine rebuilds terrain from, so
    # UnrealEd dies in UTerrainSector::GenerateTriangles the first time it draws
    # the level. Verified with a control import of the STOCK map's own export,
    # with nothing rewritten -- identical crash, so it is the format's limit and
    # not anything this tool does. drop_terrain removes the actor, which loses
    # the terrain but lets the map open.
    drop_terrain = project.config.get("drop_terrain", False)
    in_terrain = False
    terrain_seen = 0

    for raw in open(src, encoding="latin-1"):
        line = raw.rstrip("\r\n")
        stripped = line.strip()

        if stripped.startswith("Begin Actor Class=TerrainInfo"):
            terrain_seen += 1
            in_terrain = drop_terrain
        if in_terrain:
            if stripped == "End Actor":
                in_terrain = False
            continue

        # ANY actor can draw a static mesh, not just StaticMeshActor: a Mover
        # with DrawType=DT_StaticMesh is how lifts and doors are built, and
        # DM-1on1-Backspace's lift imported with no material because only
        # StaticMeshActor was being given Skins(). Every actor is buffered and
        # flush() injects only into the ones that turned out to name a mesh.
        if stripped.startswith("Begin Actor Class="):
            flush()
            actor_lines, actor_mesh = [line], None
            continue

        prop = stripped.split("=", 1)[0]
        if prop in DROP_PROPERTIES or stripped.startswith(DROP_PREFIXES):
            stats["dropped"] += 1
            continue

        def relink(m):
            cls, path = m.group(1), m.group(2)
            if cls in ASSET_CLASSES:
                if not project.renamed:
                    return m.group(0)
                # Only repoint what the package actually carried. Anything else
                # -- a mesh whose group the ASE import cannot reproduce, a
                # composite that cannot be rebuilt, an export that failed --
                # keeps its reference to the source map, which still resolves
                # because that map is installed. Repointing it regardless is
                # how an export failure becomes a broken map.
                if path not in carried:
                    stats["left"] += 1
                    return m.group(0)
                imports.add("%s'%s'" % (cls, path))
                stats["imports"] += 1
                return "%s'%s.%s'" % (cls, package, path)
            stats["models" if cls == "Model" else "relinked"] += 1
            return "%s'MyLevel.%s'" % (cls, path)

        line = re.sub(r"(\w+)'%s\.([\w\-.]+)'" % re.escape(map_pkg), relink, line)

        if "Layers(" in line and "Texture=Texture'" in line:
            # The layer's own picture is upscaled like any other texture; its
            # AlphaMap beside it on the same line is data and is handled below.
            def _layer(m):
                # The PLAIN texture, never the Shader wrapper: a terrain layer
                # is drawn by the terrain renderer, and the wrapper exists only
                # to carry Detail/SurfaceType, which a layer does not use.
                leaf = m.group(1).rsplit(".", 1)[-1]
                if leaf not in plain:
                    return m.group(0)
                stats["repointed"] += 1
                return "Texture=Texture'%s'" % plain[leaf]
            line = re.sub(r"Texture=Texture'([\w\-.]+)'", _layer, line, count=1)

        if terrain and ("TerrainMap=" in line or "AlphaMap=" in line
                        or "DensityMap=" in line):
            for leaf, path in terrain.items():
                line = re.sub(r"(TerrainMap|AlphaMap|DensityMap)=Texture'[\w\-.]*%s'"
                              % re.escape(leaf),
                              lambda m, p=path: "%s=Texture'%s'" % (m.group(1),
                                                                    p if not embed
                                                                    else p.replace(
                                                                        project.package,
                                                                        "MyLevel", 1)),
                              line)

        # The level shot is usually a MaterialSequence over two frames, which
        # the package cannot rebuild. config.json's "level_shot" names a plain
        # texture to use instead -- a renamed map wants its own shot anyway.
        if level_shot and stripped.startswith("Screenshot="):
            line = re.sub(r"=\w+'[^']*'", "=Texture'%s.LevelShot.%s'"
                          % (package, level_shot), line)
            stats["imports"] += 1

        if stripped.startswith("Begin Polygon"):
            stats["polys"] += 1
            poly_scale = 1
            t = re.search(r"Texture=(\S+)", line)
            if t:
                leaf = t.group(1).rsplit(".", 1)[-1]
                if leaf in new:
                    path, poly_scale = new[leaf][0], new[leaf][1]
                    line = line.replace("Texture=" + t.group(1), "Texture=" + path)
                    stats["repointed"] += 1
                elif shot_leaf and leaf == shot_leaf:
                    line = line.replace("Texture=" + t.group(1),
                                        "Texture=%s.LevelShot.%s" % (package, level_shot))
                    stats["repointed"] += 1
        elif poly_scale != 1 and re.match(r"\s*Texture[UV]\s", line):
            indent = line[:len(line) - len(line.lstrip())]
            key = stripped.split()[0]
            comps = [float(c) for c in stripped.split(None, 1)[1].split(",")]
            line = "%s%s %s" % (indent, key.ljust(8),
                                ",".join(num(c * poly_scale) for c in comps))
            stats["scaled"] += 1
        elif stripped.startswith("End Polygon"):
            poly_scale = 1

        m = re.match(r"(\s*Skins\(\d+\)=)(\w+)'([\w\-.]+)'", line)
        if m:
            leaf = m.group(3).rsplit(".", 1)[-1]
            if leaf in new:
                # The class travels with the path: a Shader wrapper behind a
                # Texture' prefix will not resolve.
                line = "%s%s'%s'" % (m.group(1), new[leaf][2], new[leaf][0])
                stats["repointed"] += 1

        if actor_lines is not None:
            m = re.match(r"\s*StaticMesh=StaticMesh'(?:[\w\-]+\.)*([\w\-]+)'", line)
            if m:
                actor_mesh = m.group(1)
            actor_lines.append(line)
            if stripped == "End Actor":
                flush()
        else:
            out.append(line)
    flush()

    if terrain_seen:
        if drop_terrain:
            log("  %d TerrainInfo actor(s) DROPPED -- the terrain is gone from the"
                " rebuild" % terrain_seen)
        else:
            log("  %d TerrainInfo actor(s) kept. The heightmap and alpha maps are"
                % terrain_seen)
            log("    carried by the package (G16 BMP / RGBA8 TGA, MIPS=off), but the")
            log("    source map's TerrainSector object is NOT in a .t3d -- the editor")
            log("    has to rebuild the sectors, and that is where it GPFs in")
            log("    UTerrainSector::GenerateTriangles if it cannot. If import still")
            log("    crashes, set \"drop_terrain\": true to remove the actor.")
    open(dst, "wb").write("\r\n".join(out).encode("latin-1", "replace") + b"\r\n")
    log("%s -> %s" % (os.path.basename(src), os.path.basename(dst)))
    log("  %d polygons, %d repointed to %s, %d UV axes rescaled"
        % (stats["polys"], stats["repointed"], package, stats["scaled"]))
    log("  %d Skins() added, %d models + %d refs relinked, %d stale props dropped"
        % (stats["skins"], stats["models"], stats["relinked"], stats["dropped"]))
    if imports:
        log("  %d refs to assets embedded in %s repointed at %s:"
            % (stats["imports"], map_pkg, package))
        for ref in sorted(imports):
            log("      %s" % ref)
        if level_shot:
            log("      Screenshot -> Texture'%s.LevelShot.%s'" % (package, level_shot))
    if stats["left"]:
        log("  %d refs left pointing at %s (not carried by the package)"
            % (stats["left"], map_pkg))
    # A reference to the source map is not a cosmetic leftover: AddLinker walks
    # the import table, so the client is asked for the ORIGINAL map and the join
    # fails with DownloadNotAllowed. Name every one that survived.
    if project.renamed:
        leaks = sorted({m.group(0) for line in out for m in
                        re.finditer(r"%s\.[\w\-.]+" % re.escape(map_pkg), line)})
        if leaks:
            log("  WARNING: %d reference(s) still name %s -- the rebuilt map will"
                % (len(leaks), map_pkg))
            log("    not load for a client that does not have the original:")
            for ref in leaks[:20]:
                log("      %s" % ref)
    return stats
