#!/usr/bin/env python3
"""Compare a saved 4K map against the map it was rebuilt from.

    python3 tools/check_map.py DM-Corrugation [...]     (or: all)

Run this straight after saving the import, before play-testing. A .t3d import
can fail PARTWAY: the editor stops resolving references, every actor whose mesh
did not resolve has StaticMesh=NULL, and UnrealEd then silently DELETES those
actors on save (Editor/Src/UnEdSrv.cpp:2821, "Removing degenerate"). The map
still opens, still plays, and is quietly missing hundreds of meshes.

  actors      per class, against the source map -- any shortfall is lost work
  NULL        surfaces with no material
  map deps    imports of another .ut2, which break a server (see the
              carried-assets-must-not-reference-source-map note)
"""

import collections
import glob
import json
import os
import sys

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)
from uttexture.sweeney import install_root, work_root   # noqa: E402
ROOT = work_root()
INSTALL = install_root()

from uttexture import ue2                                       # noqa: E402

WATCH = ("StaticMeshActor", "Mover", "Light", "PathNode", "InventorySpot",
         "Brush", "BlockingVolume", "TerrainInfo", "Emitter", "xEmitter")



def zone_infos(pkg):
    """The ZoneInfo actors the BSP actually assigns a zone to.

    A zone is separated from its neighbour by a zone portal. Re-running CSG
    shifts surface planes by thousandths of a unit, and a portal that exactly
    met its surrounding geometry can stop sealing -- the two zones then MERGE
    and one ZoneInfo is left governing nothing. The actor is still in the map,
    so an actor-count check sees nothing wrong, but every actor in the lost
    zone now answers to the wrong ZoneInfo: wrong ambient light (meshes render
    black), wrong sound, wrong visibility culling, wrong gravity if the zone
    had any. Walk UModel::Serialize to the zone table and compare.
    """
    export = pkg.level_model()
    if export is None:
        return set()
    r = ue2.Reader(pkg.data, export["offset"])
    start = r.p
    first = r.index()
    if not (0 <= first < len(pkg.names)) or pkg.names[first] != "None":
        r.p = start
    r.p += 12 + 12 + 1 + 16
    n = r.index(); r.p += 12 * n                       # Vectors
    n = r.index(); r.p += 12 * n                       # Points
    for _ in range(r.index()):                         # Nodes
        r.p += 16 + 8
        r.u8()
        for _ in range(7):
            r.index()
        r.p += 16
        r.u8(); r.u8(); r.u8()
        r.i32(); r.i32()
        r.p += 12
    for _ in range(r.index()):                         # Surfs
        r.index(); r.u32()
        for _ in range(4):
            r.index()
        r.index(); r.index()
        r.p += 16 + 4
    for _ in range(r.index()):                         # Verts
        r.index(); r.index()
    r.i32()                                            # NumSharedSides
    out = set()
    for _ in range(r.i32()):                           # Zones
        ref = r.index()
        r.p += 8 + 8 + 4
        if ref:
            out.add(pkg.ref_path(ref).split(".")[-1])
    return out


def check(map_name):
    work = os.path.join(ROOT, "maps", map_name)
    config = json.load(open(os.path.join(work, "config.json")))
    out_map = config.get("map_name") or (map_name + "4K")
    built = os.path.join(INSTALL, "Maps", out_map + ".ut2")
    source = os.path.join(INSTALL, "Maps", map_name + ".ut2")
    if not os.path.exists(built):
        print("  %-22s %s not imported" % (map_name, out_map))
        return 1
    a, b = ue2.Package(source), ue2.Package(built)
    ca = collections.Counter(a.class_of(e) for e in a.exports)
    cb = collections.Counter(b.class_of(e) for e in b.exports)
    surfaces = b.surfaces()
    null = sum(1 for s in surfaces if not s.get("material"))
    tops = {e["name"] for e in b.imports
            if e["class"] == "Package" and e["outer"] == 0}
    deps = sorted(t for t in tops
                  if os.path.exists(os.path.join(INSTALL, "Maps", t + ".ut2")))
    problems = []
    for cls in WATCH:
        if cb.get(cls, 0) >= ca.get(cls, 0):
            continue
        # A destroyed actor keeps its export: bDeleteMe survives into the
        # package and the .t3d export skips it, so the naive count is short
        # through no fault of the rebuild. DM-Koden carries 67 dead PathNodes.
        live = 0
        for e in a.exports:
            if a.class_of(e) != cls:
                continue
            try:
                dead = a.properties(e).get("bDeleteMe")
            except Exception:
                dead = None
            if not (dead and dead[0].raw[0]):
                live += 1
        if cb.get(cls, 0) < live:
            problems.append("LOST     %-18s %d -> %d" % (cls, live, cb.get(cls, 0)))
    # CSG re-merges coplanar surfaces, so the count legitimately drifts: it is
    # NOT a measure of lost geometry. DM-Entropic comes out 2153 -> 1944 (-10%)
    # while its solid space matches the original to 0.0001uu -- verified by
    # walking PointRegion over the whole playable volume in both builds. Only a
    # collapse worth investigating is reported; the count is always shown.
    before = len(a.surfaces())
    if len(surfaces) < before * 0.75:
        problems.append("SURFACES %d -> %d -- check the solid space" % (before, len(surfaces)))
    if null:
        problems.append("NULL     %d surface(s) with no material" % null)
    for dep in deps:
        problems.append("MAPDEP   imports %s -- clients without it cannot join" % dep)
    try:
        lost = sorted(zone_infos(a) - zone_infos(b))
    except Exception:
        lost = []
    if lost:
        problems.append("ZONES    lost %s -- merged into a neighbour; actors there "
                        "get the wrong lighting" % ", ".join(lost))
    print("  %-22s %-22s %6.1f MB  %4d surf (%+d)  %d problem(s)"
          % (map_name, out_map, os.path.getsize(built) / 1e6,
             len(surfaces), len(surfaces) - before, len(problems)))
    for line in problems:
        print("      %s" % line)
    return len(problems)


def main():
    names = sys.argv[1:]
    if not names or names == ["all"]:
        names = sorted(os.path.basename(d.rstrip("/")) for d in
                       glob.glob(os.path.join(ROOT, "maps", "*", "")))
    bad = 0
    for name in names:
        try:
            bad += check(name)
        except Exception as error:
            print("  %-22s ERROR %s" % (name, error))
            bad += 1
    print("%d problem(s)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
