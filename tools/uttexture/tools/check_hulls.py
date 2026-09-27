#!/usr/bin/env python3
"""Verify the collision hulls in a BUILT package against the source map.

    python3 tools/check_hulls.py DM-Corrugation [...]     (or: all)

Run this after `ucc make` and the mv to Textures/, BEFORE importing the .t3d.
Everything it looks at is invisible in the editor and in game: a hull can be
inside-out, be another mesh's hull, or be there at all when the source mesh
asked for per-polygon collision, and the map still builds clean, renders
correctly, and shows nothing unusual in the Karma collision view.

The meshes are copied out of the source map verbatim (uttexture/carry.py), so
the answer here is not "close enough" but IDENTICAL -- same polygons, same
vertices, same stored normals. Anything else means the copy did not happen and
something re-tessellated the hull, which is how DM-1on1-Roughinery's traeger_512
became four invisible walls.

  MISSING   the source has a hull and the rebuild does not.
  EXTRA     the rebuild has a hull and the source does not.
  CHANGED   the hull differs from the source's -- polygon count, vertices or
            normals. An open hull re-tessellated this way changes which side
            the engine calls solid: an invisible wall, or a mesh you fall
            through.
  COLLISION UseSimpleBoxCollision/UseSimpleLineCollision do not match the
            source, so the hull is consulted for different traces than it was.
"""

import glob
import json
import os
import sys

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)
from uttexture.sweeney import install_root, work_root   # noqa: E402
ROOT = work_root()

from uttexture import ue2                                             # noqa: E402

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
    """Check <Map>Tex and, when it exists, the shipped map as well.

    The map is the one that matters. A 4K rebuild is imported with
    PACKAGE=MyLevel, so the .ut2 holds the meshes as its OWN exports and a fixed
    <Map>Tex does nothing for it until the map is rebuilt or patched.
    """
    work = os.path.join(ROOT, "maps", map_name)
    config = json.load(open(os.path.join(work, "config.json")))
    package = config["package"]
    out_map = config.get("map_name") or (map_name + "4K")
    built = os.path.join(INSTALL, "Textures", package + ".utx")
    source = os.path.join(INSTALL, "Maps", map_name + ".ut2")
    shipped = os.path.join(INSTALL, "Maps", out_map + ".ut2")
    if not os.path.exists(built):
        print("  %-22s %s not built" % (map_name, package))
        return 1
    bad = check_one(map_name, work, package, source, built)
    if os.path.exists(shipped) and os.path.abspath(shipped) != os.path.abspath(source):
        bad += check_one(map_name, work, out_map, source, shipped)
    return bad


def check_one(map_name, work, package, source, built):
    carried = {n.split(".")[-1] for n in
               json.load(open(os.path.join(work, "meta", "carried.json")))}
    src, out = ue2.Package(source), ue2.Package(built)
    have = {n: e for n, _p, e in out.exports_of_class("StaticMesh")}
    problems, notes = [], []
    for name, _path, export in src.exports_of_class("StaticMesh"):
        if name not in carried or name not in have:
            continue
        for flag in ("UseSimpleBoxCollision", "UseSimpleLineCollision"):
            want = src.properties(export).get(flag)
            got = out.properties(have[name]).get(flag)
            if bool(want) != bool(got) or (want and want[0].raw != got[0].raw):
                problems.append("COLLISION %s (%s differs from the source)"
                                % (name, flag))
        a = ue2.collision_polys(src, export, normals=True)
        b = ue2.collision_polys(out, have[name], normals=True)
        if a and not b:
            problems.append("MISSING  %s (source hull has %d polys)" % (name, len(a)))
        elif b and not a:
            problems.append("EXTRA    %s (rebuild has a hull, source has none)" % name)
        elif a and a != b:
            problems.append("CHANGED  %s (%d polys -> %d, %s)"
                            % (name, len(a), len(b),
                               "re-tessellated" if len(a) != len(b)
                               else "same count, different geometry"))
        elif a:
            notes.append("hull ok  %s (%d polys, identical)" % (name, len(a)))
    print("  %-22s %-22s %d carried mesh(es), %d problem(s)"
          % (map_name, package, len(carried), len(problems)))
    if notes and os.environ.get("CHECK_HULLS_VERBOSE"):
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
