#!/usr/bin/env python3
"""uttexture -- 4x texture rebuild for any UT2004 map.

    ./uttexture.py survey   DM-Deck          what the map uses, and how it is reached
    ./uttexture.py extract  DM-Deck          pull those textures out, read properties
    ./uttexture.py upscale  DM-Deck          run the upscaler
    ./uttexture.py package  DM-Deck          write <Map>Tex: TGAs + generated .uc
    ./uttexture.py t3d      DM-Deck [--embed]  export and rewrite the map
    ./uttexture.py all      DM-Deck          the five above, in order
    ./uttexture.py compare  DM-Deck <tex>    one texture through every method

Work lives in maps/<Map>/; models, scans and the ROCm shim are shared at the
project root. Per-map settings go in maps/<Map>/config.json -- upscaler model,
sharpening, scan injection, and the sky chain.

Then: name <Map>Tex in EditPackages, `ucc make`, copy the .u to Textures/ as
.utx, and File > Import the .t3d into a new map (not File > Open).
"""

import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uttexture.project import Project, ROOT
from uttexture import extract, manifest as manifest_mod, pkg as pkg_mod, t3d as t3d_mod
from uttexture.survey import survey as run_survey
from uttexture.ue2 import Package


def need(project, name, step):
    data = project.load(name)
    if data is None:
        raise SystemExit("run `./uttexture.py %s %s` first" % (step, project.map))
    return data


def cmd_survey(project, args):
    package = Package(project.map_file())
    from uttexture.t3d import find_export, export as export_t3d
    t3d_path = find_export(project.work)
    if not t3d_path:
        # The survey READS the map's .t3d export -- brush-poly-only textures,
        # Skins() overrides and every external mesh's slot list come from it --
        # so on a map that has never been processed it has to make it first.
        # Printing a note and carrying on silently under-reports the map.
        print("no .t3d export yet; exporting first")
        export_t3d(project)
        t3d_path = find_export(project.work)
    s = run_survey(package, project.install, project.meta,
                   t3d_path=t3d_path,
                   keep_unused=(args.keep_unused
                                or project.config.get("keep_unused", False)),
                   include=set(project.config.get("include", [])))
    project.store("survey.json", s)
    project.save_config()
    print("%s: %d BSP surfaces, lightmap scale %s"
          % (s["map"], s["surfaces"], ", ".join(str(x) for x in s["lightmap_scales"])))
    print("  %d textures to upscale, %d meshes" % (len(s["textures"]), len(s["meshes"])))
    for leaf, e in sorted(s["textures"].items(), key=lambda kv: -kv[1]["surfaces"])[:8]:
        print("     %-24s %-40s %d surfaces" % (leaf, e["path"], e["surfaces"]))
    if s["composites"]:
        print("  %d composite materials -- not upscaled, rebuild by hand if wanted:"
              % len(s["composites"]))
        for leaf, e in sorted(s["composites"].items()):
            print("     %-24s %s" % (leaf, e["path"]))
    if s["unused"]:
        print("  %d referenced but never drawn (mesh slots with no faces, or"
              % len(s["unused"]))
        print("    reachable only via a composite or a Detail slot): %s"
              % ", ".join(sorted(s["unused"])))
    if not t3d_path:
        print("  WARNING: no .t3d export and one could not be made, so"
              " brush-poly-only")
        print("    and Skins()-only textures are NOT counted -- this survey is"
              " incomplete.")


def cmd_extract(project, args):
    s = need(project, "survey.json", "survey")
    extract.export(project, s["textures"])
    m = manifest_mod.build(project, s)
    project.store("manifest.json", m)
    print("%d textures -> %s" % (len(m["textures"]), project.raw))
    for r in m["textures"]:
        print("  %-24s %4dx%-4d -> %4dx%-4d DXT%d %s"
              % (r["name"], r["width"], r["height"], r["out_width"], r["out_height"],
                 r["dxt"], "alpha" if r["keep_alpha"] else ""))


def cmd_upscale(project, args):
    from uttexture import upscale
    m = need(project, "manifest.json", "extract")
    if args.model:
        project.config["model"] = args.model
    if args.models_dir:
        project.config["models_dir"] = args.models_dir
    project.save_config()
    upscale.run_all(project, m, only=args.only)


def cmd_package(project, args):
    m = need(project, "manifest.json", "extract")
    pkg_mod.build(project, m, out_root=args.out)


def cmd_meshes(project, args):
    """Copy the source map's static meshes into the package ucc just built.

    Between `ucc make` and the mv to Textures/, on the .u -- a mesh is copied
    export by export rather than compiled in, because an ASE round trip
    re-tessellates a collision hull and an open hull re-tessellated is an
    invisible wall. The package is therefore NOT reproducible from its .uc
    alone, and this is part of the build, not an extra.
    """
    from uttexture import carry
    package = project.config["package"]
    if args.into_map:
        # The shipped map, not the package: a 4K rebuild embeds its meshes, so
        # this is the only thing that fixes one that has already gone out.
        built = os.path.join(project.install, "Maps", project.out_map + ".ut2")
        if not os.path.exists(built):
            raise SystemExit("%s does not exist" % built)
    else:
        built = args.package or _built_package(project, package)
    print("carrying meshes into %s" % built)
    carry.carry_map_meshes(project, built, new_guid=bool(args.into_map))


def _built_package(project, package):
    """The .u ucc just made, or the .utx it was already moved to."""
    u = os.path.join(project.install, "System", package + ".u")
    utx = os.path.join(project.install, "Textures", package + ".utx")
    if os.path.exists(u):
        return u
    if os.path.exists(utx):
        return utx
    raise SystemExit("neither %s nor %s exists -- run `ucc make` first" % (u, utx))


def cmd_t3d(project, args, embed=None):
    m = need(project, "manifest.json", "extract")
    from uttexture.project import engine_patch_warning
    warn = engine_patch_warning(project.map, project.out_map)
    if warn:
        print("  " + warn)
    src = t3d_mod.export(project)
    embed = args.embed if embed is None else embed
    name = "%s%s.t3d" % (project.out_map, "-embed" if embed else "")
    dst = os.path.join(project.out, name)
    sky = project.config.get("sky")
    slot = (sky["mesh"], sky.get("slot", 0)) if sky and sky.get("mesh") else None
    t3d_mod.rewrite(project, m, src, dst, embed=embed, sky_slot=slot)
    if project.config.get("align_planes"):
        # Has to run on the rewritten file, and every time it is rewritten:
        # a map whose faces sit inside UE2's 0.1 plane tolerance rebuilds its
        # CSG with solid where the brushes carve empty, and regenerating the
        # .t3d without this would quietly undo the fix.
        import subprocess
        subprocess.run([sys.executable,
                        os.path.join(ROOT, "tools", "align_t3d.py"), dst, dst],
                       check=True)


def cmd_all(project, args):
    cmd_survey(project, args); print()
    cmd_extract(project, args); print()
    cmd_upscale(project, args); print()
    cmd_package(project, args); print()
    # BOTH variants, always. They are a matched pair with the package, and
    # regenerating only the one the flag asked for leaves the other pointing at
    # an older build -- DM-1on1-Roughinery kept importing a stale -embed.t3d
    # that still named the source map, and DM-Elucidation nearly repeated it.
    cmd_t3d(project, args, embed=False); print()
    cmd_t3d(project, args, embed=True)


def cmd_content(args):
    """Fold maps into the shared 4K content store, then build what changed."""
    import json, os
    from uttexture import content
    from uttexture.project import Project
    install = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    changed = set()
    for name in args.maps:
        project = Project(name)
        manifest = json.load(open(os.path.join(project.meta, "manifest.json")))
        print("== %s" % name)
        changed |= content.accrue(install, project, manifest)
    index = content.load_index(install)
    if args.rebuild_all:
        changed = set(index["packages"])
    else:
        # A package whose .utx is missing has to be rebuilt whatever the index
        # says: the index records what a package SHOULD hold, not that it was
        # ever successfully compiled.
        for source, entry in index["packages"].items():
            if entry.get("empty"):
                continue
            if not os.path.exists(os.path.join(install, "Textures",
                                               entry["package"] + ".utx")):
                changed.add(source)
    if not changed:
        print("nothing to build"); return
    # Detail textures have their own pipeline and must exist as TGAs BEFORE the
    # package that imports them is written.
    det = index["packages"].get(content.DETAIL_SOURCE)
    if det and content.DETAIL_SOURCE in changed:
        print()
        content.make_details(install, det, index)

    print()
    names = []
    for source in sorted(changed):
        entry = index["packages"][source]
        if content.build_package(install, source, entry, index,
                                 Project(entry["maps"][0]).config):
            names.append(entry["package"])
    content.save_index(install, index)
    print()
    for name in args.maps:
        project = Project(name)
        manifest = json.load(open(os.path.join(project.meta, "manifest.json")))
        content.write_map_refs(install, project, manifest, index)

    if args.no_build:
        print("\n%d package(s) staged, not compiled (--no-build)" % len(names)); return
    print()
    built, failed = content.compile_packages(install, names)
    print("\n%d built, %d failed" % (len(built), len(failed)))
    if failed:
        print("failed: %s" % ", ".join(failed))
    print("Next: ./uttexture.py package <map> and ./uttexture.py t3d <map> for each map,")
    print("with \"shared_content\": true in its config.json.")


def cmd_compare(project, args):
    from uttexture import compare
    compare.run(project, args.texture)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("content")
    c.add_argument("maps", nargs="+")
    c.add_argument("--no-build", action="store_true")
    c.add_argument("--rebuild-all", action="store_true")
    for name in ("survey", "extract", "upscale", "package", "meshes", "t3d",
                 "all", "compare"):
        p = sub.add_parser(name)
        p.add_argument("map")
        if name in ("survey", "all"):
            p.add_argument("--keep-unused", action="store_true")
        if name in ("upscale", "all"):
            p.add_argument("--model"); p.add_argument("--models-dir")
            p.add_argument("--only", nargs="*")
        if name in ("package", "all"):
            p.add_argument("-o", "--out", help="install to write <Map>Tex into")
        if name in ("t3d", "all"):
            p.add_argument("--embed", action="store_true",
                           help="reference MyLevel, for embedding into the .ut2")
        if name == "meshes":
            p.add_argument("--package", help="the built .u/.utx to carry into")
            # NOT --map: every subcommand already takes the map name as a
            # positional called "map", and argparse would quietly hand this the
            # same attribute.
            p.add_argument("--into-map", action="store_true",
                           help="patch the built Maps/<out>.ut2 instead, and give"
                                " it a new GUID")
        if name == "compare":
            p.add_argument("texture")
    args = ap.parse_args()
    for opt in ("keep_unused", "model", "models_dir", "only", "out", "embed",
                "package", "into_map"):
        if not hasattr(args, opt):
            setattr(args, opt, None)
    if args.cmd == "content":
        cmd_content(args)
    else:
        globals()["cmd_" + args.cmd](Project(args.map), args)


if __name__ == "__main__":
    main()
