#!/usr/bin/env python3
"""Verify the collision hulls in a BUILT package against the source map.

    python3 tools/check_hulls.py DM-Corrugation [...]     (or: all)

Run this after `ucc make` and the mv to Textures/, BEFORE importing the .t3d.
Everything it looks at is invisible in the editor and in game: a hull can be
inside-out, be another mesh's hull, or be there at all when the source mesh
asked for per-polygon collision, and the map still builds clean, renders
correctly, and shows nothing unusual in the Karma collision view.

  INVERTED  the hull's polygons enclose a NEGATIVE volume, so the engine treats
            the outside as solid -- an invisible wall around the mesh.
  MOVED     the hull's extent does not match the source mesh's hull.
  EXTRA     the source set UseSimpleBoxCollision=False (per-polygon collision)
            but the rebuilt mesh has a hull, which seals every gap.
  MISSING   the source has a hull and the rebuild does not.
"""

import glob
import json
import os
import sys

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)
from uttexture.sweeney import install_root, work_root   # noqa: E402
ROOT = work_root()

from uttexture import ue2, mesh                                       # noqa: E402

INSTALL = install_root()


def volume(polys):
    total = 0.0
    for _normal, poly in polys:
        a = poly[0]
        for i in range(1, len(poly) - 1):
            b, c = poly[i], poly[i + 1]
            total += (a[0] * (b[1] * c[2] - b[2] * c[1])
                      - a[1] * (b[0] * c[2] - b[2] * c[0])
                      + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return total


def extent(polys):
    pts = [v for _normal, poly in polys for v in poly]
    return (tuple(round(min(p[i] for p in pts)) for i in range(3)),
            tuple(round(max(p[i] for p in pts)) for i in range(3)))


def covers(outer, inner):
    """True when box `outer` contains box `inner`."""
    return all(outer[0][i] <= inner[0][i] and outer[1][i] >= inner[1][i]
               for i in range(3))


def check(map_name):
    work = os.path.join(ROOT, "maps", map_name)
    config = json.load(open(os.path.join(work, "config.json")))
    package = config["package"]
    built = os.path.join(INSTALL, "Textures", package + ".utx")
    source = os.path.join(INSTALL, "Maps", map_name + ".ut2")
    if not os.path.exists(built):
        print("  %-22s %s not built" % (map_name, package))
        return 1
    carried = {n.split(".")[-1] for n in
               json.load(open(os.path.join(work, "meta", "carried.json")))}
    src, out = ue2.Package(source), ue2.Package(built)
    have = {n: e for n, _p, e in out.exports_of_class("StaticMesh")}
    problems, notes = [], []
    for name, _path, export in src.exports_of_class("StaticMesh"):
        if name not in carried or name not in have:
            continue
        props = src.properties(export)
        simple = props.get("UseSimpleBoxCollision")
        wants = simple is None or bool(simple[0].raw[0])
        a = ue2.collision_polys(src, export, normals=True)
        b = ue2.collision_polys(out, have[name], normals=True)
        if not wants:
            if b:
                problems.append("EXTRA    %s (source asks for per-polygon collision)" % name)
            continue
        if a and mesh.hull_cannot_close(a):
            # mesh.drop_open_hull discards a hull that encloses no volume, so
            # the rebuild is SUPPOSED to have none and fall back to per-polygon
            # collision. Anything else means the drop did not happen.
            if b:
                problems.append("OPENKEPT %s (source hull encloses no volume but the rebuild kept one)"
                                % name)
            else:
                notes.append("dropped  %s (source hull has %d poly(s), encloses no volume; "
                             "rebuild uses per-polygon collision)" % (name, len(a)))
            continue
        if a and not b:
            problems.append("MISSING  %s (source hull has %d polys)" % (name, len(a)))
            continue
        if not b:
            continue
        if volume(b) <= 0:
            problems.append("INVERTED %s (signed volume %+.0f)" % (name, volume(b)))
        elif mesh.hull_cannot_close(b):
            problems.append("OPEN     %s (rebuild hull is not a closed volume)" % name)
        elif a and extent(a) != extent(b):
            problems.append("MOVED    %s %s -> %s" % (name, extent(a), extent(b)))
    print("  %-22s %-22s %d carried mesh(es), %d problem(s)"
          % (map_name, package, len(carried), len(problems)))
    for line in notes:
        print("      %s" % line)
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
