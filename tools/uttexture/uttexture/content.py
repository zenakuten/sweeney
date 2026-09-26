"""Shared 4K content packages, grown as maps need them.

The per-map approach gives every map its own package holding a copy of every
texture it touches. That duplicates content across maps -- six maps cost 1.9 GB
for 180 distinct textures -- and every new map pays the full price again.

This builds one 4K package per SOURCE package instead: AbaddonArchitecture ->
AbaddonArchitecture4K, holding only the textures some map has actually asked
for. When a later map wants two more textures out of the same source package,
they are added and the package is rebuilt. Maps reference the shared packages,
so the content is paid for once.

Converting whole source packages was the other option and is far more
expensive: the packages behind those same six maps hold 2236 textures against
the 180 the maps use.

Names: <Source>4K, with anything that is not an identifier folded to '_'
(X_AW-Convert -> X_AW_Convert4K). The generated class shares the package's name
and a UnrealScript identifier cannot carry a hyphen.

Reference forms a map must use, which differ by object kind:

* a plain texture is <Pkg>4K.<Group>.<leaf> -- an ordinary group member
* a Shader wrapper or rebuilt composite is <Pkg>4K.<Pkg>4K.<name>, because
  anything emitted through defaultproperties is a subobject of the generated
  CLASS (see the memory on that). Cross-package this resolves: ResolveName
  tries FindObject<UPackage> for an intermediate component and then falls back
  to FindObject<UObject> (Core/Src/UnObj.cpp), which matches the class.

The store is a build tree; the built .utx files all land in Textures/, because
the engine's Paths cannot usefully be given subfolders.
"""

import json, os, re, subprocess

STORE = "UT2004K"
INDEX = "content.json"

# Detail textures live in one package of their own, keyed in the index under a
# name no real package can collide with.
DETAIL_SOURCE = "__detail__"

# Textures that belong to no map -- reached only through a composite.
SHARED_MAP = "_shared"


def package_name(source):
    """<Source>4K as a legal package/class identifier."""
    if source == DETAIL_SOURCE:
        return "DetailTextures4K"
    stem = re.sub(r"[^0-9A-Za-z_]", "_", source)
    if stem[:1].isdigit():
        stem = "P" + stem
    return stem + "4K"


def store_dir(install):
    return os.path.join(install, STORE)


def load_index(install):
    path = os.path.join(store_dir(install), INDEX)
    if os.path.exists(path):
        return json.load(open(path))
    return {"packages": {}}


def save_index(install, index):
    os.makedirs(store_dir(install), exist_ok=True)
    path = os.path.join(store_dir(install), INDEX)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(index, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


# Per-texture fields that decide what the package contains. A map asking for a
# texture it already has under identical settings changes nothing; different
# settings mean the entry is replaced and the package rebuilt.
KEEP = ("name", "group", "source", "width", "height", "out_width", "out_height",
        "scale", "dxt", "lodset", "normal_lod", "masked", "alpha_texture",
        "wrap_u", "wrap_v", "surface_type", "detail", "detail_source",
        "detail_scale", "png")


# Sources that never become shared content: the map's own package (those
# textures are the map's, and travel in its own package), and the engine's,
# which is not ours to fork.
NEVER_SHARED = {"engine", "editor", "core"}


def shared_source(source, map_name):
    return (source.lower() not in NEVER_SHARED
            and source.lower() != map_name.lower())


def accrue(install, project, manifest, log=print):
    """Fold a map's manifest into the shared store. Returns changed packages."""
    index = load_index(install)
    changed, local = set(), []
    for rec in manifest["textures"]:
        source = rec["source"].split(".")[0]
        if not shared_source(source, project.map):
            local.append(rec["name"])
            continue
        entry = index["packages"].setdefault(
            source, {"package": package_name(source), "textures": {}, "maps": []})
        keep = {k: rec[k] for k in KEEP if k in rec}
        # The PNG is written per map; remember which map's copy to read.
        keep["map"] = project.map
        old = entry["textures"].get(rec["name"])
        if old != keep:
            entry["textures"][rec["name"]] = keep
            changed.add(source)
        if project.map not in entry["maps"]:
            entry["maps"].append(project.map)
            entry["maps"].sort()

    # Detail textures are engine grain shared by every map, so they get one
    # package of their own rather than a copy per source package.
    from .detail import GROUP as DETAIL_GROUP
    det = index["packages"].setdefault(
        DETAIL_SOURCE, {"package": "DetailTextures4K", "textures": {},
                        "details": {}, "maps": []})
    want = {r["detail"] for r in manifest["textures"]
            if r.get("detail") and (r.get("detail_source") or "None") != "None"}
    cfg = {k: project.config.get(k) for k in
           ("detail_mode", "detail_factor", "detail_model", "detail_strength")}
    if det.get("config") not in (None, cfg) and want:
        # A different look means every detail texture is regenerated.
        det["details"] = {}
        changed.add(DETAIL_SOURCE)
    det["config"] = cfg
    for leaf in sorted(want):
        if leaf not in det["details"]:
            det["details"][leaf] = {"group": DETAIL_GROUP, "map": project.map}
            changed.add(DETAIL_SOURCE)
    if want and project.map not in det["maps"]:
        det["maps"].append(project.map)
        det["maps"].sort()

    # Composites drawn by this map that belong to a content package: they have
    # to be rebuilt over the upscaled textures, and they belong beside them.
    survey = project.load("survey.json") or {}
    for name, info in sorted(survey.get("composites", {}).items()):
        path = info.get("path") or ""
        source = path.split(".")[0]
        if not path or not shared_source(source, project.map):
            continue
        entry = index["packages"].setdefault(
            source, {"package": package_name(source), "textures": {}, "maps": []})
        comps = entry.setdefault("composites", {})
        if comps.get(name) != path:
            comps[name] = path
            changed.add(source)
        if project.map not in entry["maps"]:
            entry["maps"].append(project.map)
            entry["maps"].sort()

    save_index(install, index)
    if local:
        log("%d texture(s) stay in the map's own package (map-embedded or"
            " engine): %s" % (len(local), ", ".join(sorted(local)[:6])
                              + (" ..." if len(local) > 6 else "")))
    if changed:
        log("%d content package(s) to (re)build: %s"
            % (len(changed), ", ".join(sorted(package_name(s) for s in changed))))
    else:
        log("shared content already covers this map")
    return changed


def plan(install, log=print):
    """What the store holds now, without building anything."""
    index = load_index(install)
    rows = []
    for source, entry in sorted(index["packages"].items()):
        rows.append((entry["package"], len(entry["textures"]), len(entry["maps"])))
    total = sum(r[1] for r in rows)
    for name, n, m in rows:
        log("  %-34s %4d textures  (%d map%s)" % (name, n, m, "" if m == 1 else "s"))
    log("%d packages, %d textures" % (len(rows), total))
    return rows


# ---------------------------------------------------------------- building

def texture_map(index):
    """{leaf: <Pkg>4K.<Group>.<leaf>} across the whole store."""
    out = {}
    for entry in index["packages"].values():
        for leaf, rec in entry.get("textures", {}).items():
            out[leaf] = "%s.%s.%s" % (entry["package"], rec["group"], leaf)
    return out


def make_details(install, entry, index, log=print):
    """Run the detail pipeline for every accrued detail texture."""
    from .project import Project
    from .pkg import to_tga
    from . import detail as detail_mod
    from .extract import probe
    tex_dir = os.path.join(store_dir(install), entry["package"], "Textures")
    os.makedirs(tex_dir, exist_ok=True)
    cfg = entry.get("config") or {}
    factor = int(cfg.get("detail_factor") or 1)
    strength = float(cfg.get("detail_strength") or 1.0)
    mode = cfg.get("detail_mode") or "copy"
    class _Staged(object):
        """The project, but with `up` pointing at the detail staging area.

        detail.upscale writes its result to <project>/up/<leaf>.png, which is
        also where the BASE upscales live. A texture used both as artwork and
        as a detail layer -- DM-Under's detail1 -- would have its 1024px base
        upscale overwritten by the 2048px detail one, and the TGA write then
        fails on the size mismatch. Staging the detail outputs elsewhere keeps
        the two apart.
        """

        def __init__(self, inner, up):
            self._inner, self.up = inner, up

        def __getattr__(self, name):
            return getattr(self._inner, name)

    staging = os.path.join(store_dir(install), entry["package"], "up")
    os.makedirs(staging, exist_ok=True)
    by_map = {}
    for leaf, rec in entry.get("details", {}).items():
        if not os.path.exists(os.path.join(tex_dir, leaf + ".tga")):
            by_map.setdefault(rec["map"], []).append(leaf)
    for map_name, leaves in sorted(by_map.items()):
        project = _Staged(Project(map_name), staging)
        pngs = detail_mod.export(project, sorted(leaves), log=log)
        if pngs and mode == "upscale":
            pngs = detail_mod.upscale(
                project, pngs, factor,
                cfg.get("detail_model") or project.config["model"],
                project.config.get("detail_models_dir")
                or project.config["models_dir"],
                strength=strength, log=log)
        elif pngs and mode == "synth":
            pngs = detail_mod.synthesize(project, pngs, factor,
                                         strength=strength, log=log)
        for leaf, png in sorted(pngs.items()):
            w, h, _a, _m = probe(png)
            to_tga(png, os.path.join(tex_dir, leaf + ".tga"), w, h)


def _detail_path(index, rec):
    """Where a wrapper's detail texture lives in the shared store, or None."""
    src = rec.get("detail_source") or ""
    if not src or src == "None":
        return None
    inner = src.split("'")[1] if "'" in src else src
    leaf = inner.rsplit(".", 1)[-1]
    for source, entry in index["packages"].items():
        if leaf in entry.get("details", {}):
            return "%s.%s.%s" % (entry["package"], entry["details"][leaf]["group"], leaf)
    return None


def build_package(install, source, entry, index, config, log=print):
    """Write the build tree for one shared package. Returns its .uc path."""
    from .pkg import to_tga, SURFACE_TYPES
    from .materials import MaterialSet

    package = entry["package"]
    root = os.path.join(store_dir(install), package)
    tex_dir = os.path.join(root, "Textures")
    cls_dir = os.path.join(root, "Classes")
    os.makedirs(tex_dir, exist_ok=True)
    os.makedirs(cls_dir, exist_ok=True)

    ms = MaterialSet(package)
    imports, wrappers, total = [], {}, 0
    for name in sorted(entry["textures"]):
        rec = entry["textures"][name]
        if rec.get("map") == SHARED_MAP:
            png = os.path.join(store_dir(install), SHARED_MAP, "up", name + ".png")
        else:
            png = os.path.join(install, "uttexture", "maps", rec["map"], "up",
                               name + ".png")
        if not os.path.exists(png):
            log("  MISSING upscaled %s (%s) -- skipped" % (name, rec["map"]))
            continue
        tga = os.path.join(tex_dir, name + ".tga")
        if not os.path.exists(tga) or os.path.getmtime(tga) < os.path.getmtime(png):
            to_tga(png, tga, rec["out_width"], rec["out_height"])
        total += os.path.getsize(tga)
        opts = ["NAME=%s" % name, "GROUP=%s" % rec["group"],
                "FILE=Textures\\%s.tga" % name,
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

        # The wrapper is what carries Detail and SurfaceType; no #exec can set
        # either on a Texture.
        detail = _detail_path(index, rec)
        if not detail and not rec.get("surface_type"):
            continue
        path = "Texture'%s.%s.%s'" % (package, rec["group"], name)
        props = [("Diffuse", path)]
        if rec.get("alpha_texture") or rec.get("masked"):
            props.append(("Opacity", path))
            if rec.get("masked"):
                props.append(("OutputBlending", "OB_Masked"))
        if detail:
            props.append(("Detail", "Texture'%s'" % detail))
            ds = float(rec["detail_scale"]) / float(config.get("detail_factor", 1))
            props.append(("DetailScale", "%g" % ds))
        if rec.get("surface_type"):
            props.append(("SurfaceType", SURFACE_TYPES[rec["surface_type"]]))
        wrappers[name] = ms.add("Shader", name, props)

    # Detail textures this package owns are imported like any other, but they
    # were upscaled through the detail pipeline, not the base one.
    for name in sorted(entry.get("details", {})):
        rec = entry["details"][name]
        tga = os.path.join(tex_dir, name + ".tga")
        if not os.path.exists(tga):
            log("  MISSING detail TGA %s -- skipped" % name)
            continue
        total += os.path.getsize(tga)
        # Uncompressed on purpose: re-compressing grain that was already DXT
        # once would add a second generation of loss to the one thing whose
        # whole job is high frequency.
        imports.append("#exec TEXTURE IMPORT NAME=%s GROUP=%s FILE=Textures\\%s.tga"
                       " ALPHA=0 LODSET=0" % (name, rec["group"], name))

    # Composites this package owns, rebuilt over the upscaled textures. Their
    # slots may name a texture in ANOTHER shared package, so the repoint map
    # spans the whole store.
    rebuilt = {}
    if entry.get("composites"):
        from . import composite as composite_mod
        from .project import Project
        probe_project = Project(entry["maps"][0])
        wanted = composite_mod.closure(probe_project, entry["composites"], log=log)
        entry["composites"] = wanted
        defs = composite_mod.scan(probe_project, wanted, log=log)
        rebuilt = composite_mod.rebuild(
            ms, package, defs, texture_map(index),
            refs=composite_mod.reference_map(
                probe_project,
                {p.split(".")[0] for p in entry["composites"].values()}), log=log)
        native, colours, native_diffuse = composite_mod.rebuild_native(
            probe_project, ms, package,
            {n for n in entry["composites"] if n not in rebuilt},
            texture_map(index), log=log)
        rebuilt.update(native)
        # The texture each composite draws through: the surfaces' UVs are in
        # ITS texels, so they must be rescaled by ITS factor. Without this every
        # surface drawn through a rebuilt composite renders 4x zoomed -- the
        # corrugated walls in DM-DE-Ironic came back with a quarter of their
        # ribs.
        diffuse = dict(native_diffuse)
        for name in rebuilt:
            if name in diffuse:
                continue
            cls = (defs.get(name) or (None, {}))[0]
            raw = (defs.get(name) or (None, {}))[1].get(
                composite_mod.primary_slot(cls), "None")
            m = composite_mod.OBJ_REF.match(raw or "None")
            if m:
                diffuse[name] = m.group(2).rsplit(".", 1)[-1]
        entry["rebuilt_diffuse"] = diffuse
        entry["colours"] = {n: list(c) for n, c in colours.items()}
    # {original name: [class, new name]} -- the class matters, a FinalBlend
    # referenced as Shader' does not resolve.
    entry["rebuilt"] = {n: list(v) for n, v in rebuilt.items()}

    body = ["//", "// %s -- 4K rebuild of %s, generated by uttexture." % (package, source),
            "// Grown as maps need it; see UT2004K/content.json.",
            "// Maps using it: %s" % ", ".join(entry.get("maps", [])), "//",
            "class %s extends Object;" % package, ""]
    body += imports
    decl, defaults = (ms.declaration(), ms.emit()) if len(ms) else (None, [])
    body += [""] + ([decl, ""] if decl else []) + ["defaultproperties", "{"]
    body += defaults + ["}", ""]
    if not imports and not len(ms):
        # Nothing survived -- typically a package whose only entry was a
        # composite that cannot be rebuilt. The map keeps referencing the
        # original, which is retail content and resolves.
        log("  %-34s nothing to build, skipped" % package)
        entry["empty"] = True
        _unlink(install, package)
        return None
    entry.pop("empty", None)
    uc = os.path.join(cls_dir, package + ".uc")
    open(uc, "wb").write("\r\n".join(body).encode("latin-1", "replace"))
    # UMakeCommandlet resolves an EditPackages entry as ..\<Package>\Classes\*.uc,
    # relative to System/, so the build tree has to appear at the install root.
    # The store stays organised under UT2004K/ and is linked into place; Wine
    # follows the symlink.
    _link(install, package, root)
    entry["wrappers"] = wrappers
    log("  %-34s %3d textures, %2d wrappers, %6.1f MB TGA"
        % (package, len(imports), len(wrappers), total / 1e6))
    return uc


def _link(install, package, root):
    """Expose a build tree at the install root for the length of the build."""
    link = os.path.join(install, package)
    if os.path.islink(link):
        os.remove(link)
    elif os.path.exists(link):
        return                       # a real directory -- never touch it
    os.symlink(os.path.relpath(root, install), link)


def _unlink(install, package):
    """Take the build-tree symlink back out; only ever removes a symlink."""
    link = os.path.join(install, package)
    if os.path.islink(link):
        os.remove(link)


def _harvest(install, system, package, log):
    """Move a freshly built .u into Textures/, and drop the symlink. True on ok."""
    u = os.path.join(system, package + ".u")
    if not os.path.exists(u):
        return False
    dest = os.path.join(install, "Textures", package + ".utx")
    os.replace(u, dest)
    _unlink(install, package)
    log("  built %-34s %7.1f MB" % (package, os.path.getsize(dest) / 1e6))
    return True


def compile_packages(install, names, log=print):
    """Put each package through UCC and move the .u into Textures/*.utx."""
    system = os.path.join(install, "System")
    ini = os.path.join(system, "UT2004.ini")
    # DetailTextures4K first: every wrapper elsewhere names a texture in it,
    # and UCC resolves that at compile time by loading the package off the
    # Paths, so it has to be a built .utx already.
    names = sorted(names, key=lambda n: (n != "DetailTextures4K", n))
    built, failed = [], []
    for package in names:
        _set_edit_packages(ini, [package])
        u = os.path.join(system, package + ".u")
        if os.path.exists(u):
            os.remove(u)
        r = subprocess.run(["./UCC.exe", "make"], cwd=system,
                           capture_output=True, text=True, timeout=7200)
        if _harvest(install, system, package, log):
            built.append(package)
        else:
            # The symlink stays while the package is still failing, so a retry
            # has something to compile.
            tail = "\n".join((r.stdout or "").strip().splitlines()[-6:])
            log("  FAILED %s\n%s" % (package, tail))
            failed.append(package)
    # One retry pass: a package can fail purely because another it references
    # had not been built yet.
    if failed:
        log("retrying %d package(s) now the rest are built" % len(failed))
        retry, failed = failed, []
        for package in retry:
            _set_edit_packages(ini, [package])
            u = os.path.join(system, package + ".u")
            if os.path.exists(u):
                os.remove(u)
            subprocess.run(["./UCC.exe", "make"], cwd=system,
                           capture_output=True, text=True, timeout=7200)
            if _harvest(install, system, package, log):
                built.append(package)
            else:
                # Left linked on purpose: the tree is still there to look at.
                log("  FAILED %s (build tree left linked at %s/)"
                    % (package, package))
                failed.append(package)
    _set_edit_packages(ini, [])
    return built, failed


def _managed(install):
    """Every package name this tool builds: map packages and content packages."""
    import glob, json
    names = set()
    for cfg in glob.glob(os.path.join(install, "uttexture", "maps", "*",
                                      "config.json")):
        try:
            names.add(json.load(open(cfg)).get("package"))
        except (OSError, ValueError):
            pass
    try:
        index = json.load(open(os.path.join(install, STORE, INDEX)))
        names |= {e.get("package") for e in index.get("packages", {}).values()}
    except (OSError, ValueError):
        pass
    return {n for n in names if n}


def _set_edit_packages(ini, packages):
    """Leave exactly `packages` as the non-stock EditPackages entries."""
    data = open(ini, "rb").read()
    eol = b"\r\n" if b"\r\n" in data else b"\n"
    # Everything uttexture builds comes out, not just the 4K content packages:
    # a map package is named <Map>Tex and would otherwise accumulate in the ini
    # run after run, slowing editor startup and breaking it outright once the
    # package is deleted (UEditorEngine::Init appErrorfs on a missing entry).
    # abspath first: a relative "UT2004.ini" would otherwise resolve the
    # install root to "" and quietly manage nothing, leaving every package we
    # build accumulating in the list.
    ours = set(_managed(os.path.dirname(os.path.dirname(os.path.abspath(ini)))))
    keep, seen = [], set()
    for line in data.split(eol):
        if line.startswith(b"EditPackages="):
            name = line.split(b"=", 1)[1]
            if name.endswith(b"4K") or name.decode("latin-1", "replace") in ours:
                continue
            if name in seen:
                continue
            seen.add(name)
        keep.append(line)
    out = eol.join(keep)
    if packages:
        anchor = b"EditPackages=AssaultBP" + eol
        add = eol.join(b"EditPackages=" + p.encode() for p in packages) + eol
        out = out.replace(anchor, anchor + add, 1)
    open(ini, "wb").write(out)


# ---------------------------------------------------------------- map wiring

def write_map_refs(install, project, manifest, index, log=print):
    """Tell one map where each shared object now lives.

    Written to the map's meta/ as content_refs.json and merged by t3d.rewrite.
    Three reference forms, because they are three different kinds of object:
    a plain texture is a group member, a Shader wrapper or rebuilt composite is
    a subobject of the generated class, and a flat colour is a texture again.
    """
    refs, by_leaf, by_src = {}, {}, {}
    for source, entry in index["packages"].items():
        for leaf, rec in entry.get("textures", {}).items():
            by_leaf[leaf] = (entry, rec)
            by_src[(source, leaf)] = (entry, rec)
    for source, entry in index["packages"].items():
        pkg = entry["package"]
        for name, (kind, new_name) in (entry.get("rebuilt") or {}).items():
            # Scale follows the texture underneath, exactly as a map-rebuilt
            # composite does -- see rebuilt_diffuse above.
            leaf = (entry.get("rebuilt_diffuse") or {}).get(name)
            scale = by_leaf[leaf][1].get("scale", 1) if leaf in by_leaf else 1
            refs[name] = ["%s.%s.%s" % (pkg, pkg, new_name), scale, kind]
        for name in (entry.get("colours") or {}):
            refs[name] = ["%s.Colours.%s" % (pkg, name), 1, "Texture"]

    for rec in manifest["textures"]:
        leaf = rec["name"]
        # Keyed on the source package: a map-local texture can share its name
        # with one in the store (DM-Koden.Koden.White vs another map's White),
        # and matching on the name alone points the map at the wrong image --
        # see shared_leaves, which drops it from the map package to match.
        key = (rec["source"].split(".")[0], leaf)
        if key not in by_src:
            continue                       # map-owned: its own package keeps it
        entry, stored = by_src[key]
        pkg = entry["package"]
        wrapper = (entry.get("wrappers") or {}).get(leaf)
        if wrapper:
            refs[leaf] = ["%s.%s.%s" % (pkg, pkg, wrapper), rec["scale"], "Shader"]
        else:
            refs[leaf] = ["%s.%s.%s" % (pkg, stored["group"], leaf),
                          rec["scale"], "Texture"]
    project.store("content_refs.json", refs)
    # Terrain layers and anything else that must name the PLAIN texture rather
    # than its Shader wrapper: a terrain layer is drawn by the terrain renderer
    # and a wrapper there renders nothing at all.
    plain = {leaf: "%s.%s.%s" % (entry["package"], stored["group"], leaf)
             for leaf, (entry, stored) in by_leaf.items()}
    project.store("content_plain.json", plain)
    log("  %s: %d shared reference(s)" % (project.map, len(refs)))
    return refs


def shared_leaves(install, manifest):
    """Leaves of `manifest` that the shared store owns, so a map package can
    skip importing them."""
    index = load_index(install)
    owned = set()
    for source, entry in index["packages"].items():
        owned |= {(source, name) for name in entry.get("textures", {})}
    # Keyed on the source package, not the bare name. Names collide across
    # packages -- DM-Koden has its own Koden.White, and once another map put a
    # White into the store, matching on the name alone dropped Koden's from its
    # map package while the .t3d went on referencing MyLevel.Koden.White (90
    # surfaces). Only skip a texture the store holds from the same source.
    return {r["name"] for r in manifest["textures"]
            if (r["source"].split(".")[0], r["name"]) in owned}


# ------------------------------------------------- composite slot textures

def accrue_composite_textures(install, index, log=print):
    """Pull in textures that only a rebuilt composite draws through.

    A composite rebuilt into a content package keeps whatever its slots named.
    If the texture underneath was never accrued -- nothing draws it directly,
    only the composite does -- the rebuilt composite points at the ORIGINAL and
    renders at source resolution. 28 composites across the store were doing
    exactly that.

    The textures are extracted and upscaled into the store's own working area
    rather than a map's, since they belong to no single map.
    """
    from .project import Project
    from . import extract as extract_mod, upscale as upscale_mod, manifest as manifest_mod
    from .survey import COMPOSITE

    known = set(texture_map(index))
    wanted = {}                       # leaf -> full source path
    for entry in index["packages"].values():
        for name, leaf in (entry.get("rebuilt_diffuse") or {}).items():
            if not leaf or leaf in known or leaf in wanted:
                continue
            path = (entry.get("composites") or {}).get(name)
            if not path:
                continue
            # Resolve the slot's own package: the composite's package is where
            # its textures almost always live.
            wanted[leaf] = "%s.%s" % (path.split(".")[0], leaf)
    if not wanted:
        log("every rebuilt composite already draws through an upscaled texture")
        return set()
    log("%d texture(s) reached only through a composite: %s"
        % (len(wanted), ", ".join(sorted(wanted)[:8])
           + (" ..." if len(wanted) > 8 else "")))
    return wanted


class _StoreProject(object):
    """A Project-shaped working area for textures that belong to no map."""

    def __init__(self, install):
        root = os.path.join(store_dir(install), "_shared")
        self.map = SHARED_MAP
        self.dir = root
        self.meta = os.path.join(root, "meta")
        self.raw = os.path.join(root, "raw")
        self.up = os.path.join(root, "up")
        self.work = os.path.join(root, ".work")
        self.out = os.path.join(root, "out")
        for d in (self.meta, self.raw, self.up, self.work, self.out):
            os.makedirs(d, exist_ok=True)
        from .project import DEFAULTS
        self.config = dict(DEFAULTS)
        self.config["install"] = install
        self._install = install

    @property
    def install(self):
        return self._install


def composite_textures(install, index, log=print):
    """Textures only a rebuilt composite draws through: {leaf: source package}.

    A composite rebuilt into a content package keeps whatever its slots named.
    If the texture underneath was never accrued -- nothing draws it directly,
    only the composite does -- the rebuilt composite points at the ORIGINAL and
    renders at source resolution.

    A slot can also name another COMPOSITE rather than a texture; those need
    rebuilding, not upscaling, and are reported instead.
    """
    from . import ue2
    from .mesh import _find_package
    known = set(texture_map(index))
    wanted = {}
    for entry in index["packages"].values():
        for name, leaf in (entry.get("rebuilt_diffuse") or {}).items():
            path = (entry.get("composites") or {}).get(name)
            if not leaf or leaf in known or leaf in wanted or not path:
                continue
            wanted[leaf] = path.split(".")[0]
    real, nested = {}, []
    cache = {}
    for leaf, source in sorted(wanted.items()):
        if source not in cache:
            found = _find_package(install, source)
            cache[source] = {}
            if found:
                pkg = ue2.Package(found)
                cache[source] = {e["name"]: (pkg.class_of(e), pkg.export_path(i))
                                 for i, e in enumerate(pkg.exports)}
            
        cls, _path = cache[source].get(leaf, (None, None))
        if cls == "Texture":
            real[leaf] = (source, cache[source][leaf][1])
        elif cls:
            nested.append("%s (%s)" % (leaf, cls))
    if nested:
        log("  %d composite slot(s) name another composite, not a texture: %s"
            % (len(nested), ", ".join(nested[:6])))
    return real


def accrue_composite_textures(install, index, log=print):
    """Extract and upscale composite-only textures into the store."""
    from . import extract as extract_mod, upscale as upscale_mod
    from .survey import umodel_dump
    from .manifest import parse_dumps
    from .extract import probe

    real = composite_textures(install, index, log=log)
    if not real:
        log("every rebuilt composite already draws through an upscaled texture")
        return set()
    log("%d texture(s) reached only through a composite: %s"
        % (len(real), ", ".join(sorted(real)[:8])
           + (" ..." if len(real) > 8 else "")))

    project = _StoreProject(install)
    changed = set()
    for leaf, (source, full_path) in sorted(real.items()):
        group = full_path.rsplit(".", 1)[0] if "." in full_path else ""
        extract_mod.export(project, {leaf: {"path": "%s.%s" % (source, full_path),
                                            "group": group}},
                           log=lambda *a: None)
        png = os.path.join(project.raw, source, "Texture", leaf + ".png")
        if not os.path.exists(png):
            log("  could not export %s from %s" % (leaf, source))
            continue
        umodel_dump(install, source, [leaf],
                    os.path.join(project.meta, "tex-%s.txt" % source))
        props = parse_dumps(project.meta).get(leaf, {})
        w, h, has_alpha, alpha_mean = probe(png)
        fmt = props.get("Format", "")
        masked = props.get("bMasked", "false") == "true"
        alpha_tex = props.get("bAlphaTexture", "false") == "true"
        keep = alpha_tex or masked or (
            has_alpha and alpha_mean < 0.99 and ("DXT3" in fmt or "DXT5" in fmt))
        rec = {"name": leaf, "group": group or "Misc",
               "source": "%s.%s" % (source, full_path),
               "png": os.path.relpath(png, project.dir),
               "width": w, "height": h,
               "alpha_texture": alpha_tex, "masked": masked,
               "scale": 4, "out_width": w * 4, "out_height": h * 4,
               "dxt": 5 if keep else 1, "lodset": 1, "normal_lod": 0,
               "wrap_u": True, "wrap_v": True, "surface_type": 0,
               "detail": None, "detail_source": "None", "detail_scale": "8",
               "has_alpha": has_alpha, "map": "_shared"}
        upscale_mod.run_all(project, {"textures": [rec]}, log=lambda *a: None)
        entry = index["packages"].setdefault(
            source, {"package": package_name(source), "textures": {}, "maps": []})
        keep_rec = {k: rec[k] for k in KEEP if k in rec}
        # KEEP does not carry "map", but build_package needs it to find the
        # upscaled PNG. These belong to no map, so they are marked _shared and
        # read from the store's own working area.
        keep_rec["map"] = SHARED_MAP
        if entry["textures"].get(leaf) != keep_rec:
            entry["textures"][leaf] = keep_rec
            changed.add(source)
    log("%d composite texture(s) accrued, %d package(s) changed"
        % (len(real), len(changed)))
    return changed

