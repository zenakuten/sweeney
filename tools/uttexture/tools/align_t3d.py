#!/usr/bin/env python3
"""Make near-coplanar brush faces in a UE2 .t3d exactly coplanar.

    python3 tools/align_t3d.py in.t3d out.t3d

UE2 decides which side of a plane a point is on with a fixed 0.10 tolerance
(THRESH_POINT_ON_PLANE, Core/Inc/UnMath.h), and CSG rests on that answer being
self-consistent. Faces a few thousandths of a unit apart are "the same plane" to
the engine and different planes to the arithmetic, which produces slivers and
mis-classified space -- solid where the brushes carve empty.

That is not only a UT3-import problem. DM-1on1-Aerowalk, authored in UT2004, has
3748 such pairs among 4195 brush planes; rebuilding its CSG from the .t3d puts
solid geometry across a walkway that two Subtract brushes carve out, while the
BSP the author shipped has it correctly empty.

The work is ut3converter's convert/align.py: cluster the planes by direction,
collapse runs closer than the tolerance onto their mean, then rebuild every
vertex as the intersection of the faces meeting there, so each polygon lies on
its plane by construction and the brush keeps its topology. This is only the
reader and writer for a UE2 .t3d around it.
"""

import os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "uttexture"))
from sweeney import ut3converter                             # noqa: E402
UT3CONV = ut3converter()
if not UT3CONV:
    raise SystemExit("align_t3d needs ut3converter; set tools.ut3converter "
                     "in ~/.sweeney/config.json")
sys.path.insert(0, UT3CONV)
from convert.align import align_brushes                      # noqa: E402

VERTEX = re.compile(r"^(\s*Vertex\s+)(\S+),(\S+),(\S+)\s*$")
NORMAL = re.compile(r"^(\s*Normal\s+)(\S+),(\S+),(\S+)\s*$")
LOCATION = re.compile(r"Location=\(([^)]*)\)")


class Poly:
    __slots__ = ("vertices", "normal", "vlines", "nline")

    def __init__(self):
        self.vertices, self.normal, self.vlines, self.nline = [], None, [], None


class Brush:
    def __init__(self, location):
        self.properties = [("Location", location)]
        self.polygons = []


def parse(lines):
    """[(Brush, ...)] for every Brush actor, remembering its line numbers."""
    brushes, i = [], 0
    while i < len(lines):
        if re.match(r"\s*Begin Actor Class=Brush ", lines[i]):
            loc, polys, cur = "(X=0,Y=0,Z=0)", [], None
            j = i
            while j < len(lines) and not re.match(r"\s*End Actor", lines[j]):
                m = LOCATION.search(lines[j])
                if m and not polys:
                    # Missing components are legal and mean zero; align.py's
                    # _origin_of would otherwise hand back a short list.
                    got = dict(p.split("=") for p in m.group(1).split(",") if "=" in p)
                    loc = "(X=%s,Y=%s,Z=%s)" % (got.get("X", "0"), got.get("Y", "0"),
                                                got.get("Z", "0"))
                if re.match(r"\s*Begin Polygon", lines[j]):
                    cur = Poly()
                elif cur is not None and re.match(r"\s*End Polygon", lines[j]):
                    if len(cur.vertices) >= 3:
                        polys.append(cur)
                    cur = None
                elif cur is not None:
                    v = VERTEX.match(lines[j])
                    if v:
                        cur.vertices.append(tuple(float(v.group(k)) for k in (2, 3, 4)))
                        cur.vlines.append(j)
                    n = NORMAL.match(lines[j])
                    if n:
                        cur.nline = j
                j += 1
            b = Brush(loc)
            b.polygons = polys
            brushes.append(b)
            i = j
        i += 1
    return brushes


def write_back(lines, brushes):
    changed = 0
    for b in brushes:
        for poly in b.polygons:
            for line_no, v in zip(poly.vlines, poly.vertices):
                m = VERTEX.match(lines[line_no])
                new = "%s%+013.6f,%+013.6f,%+013.6f" % (m.group(1), v[0], v[1], v[2])
                if new != lines[line_no]:
                    lines[line_no] = new
                    changed += 1
            if poly.normal is not None and poly.nline is not None:
                m = NORMAL.match(lines[poly.nline])
                lines[poly.nline] = "%s%+013.6f,%+013.6f,%+013.6f" % (
                    m.group(1), poly.normal[0], poly.normal[1], poly.normal[2])
    return changed


def main():
    src, dst = sys.argv[1], sys.argv[2]
    raw = open(src, encoding="latin-1").read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    brushes = parse(lines)
    print("%d brushes, %d polygons"
          % (len(brushes), sum(len(b.polygons) for b in brushes)))
    moved, skipped = align_brushes(brushes)
    print("brushes adjusted: %d, skipped as implausible: %d" % (moved, skipped))
    changed = write_back(lines, brushes)
    print("vertex/normal lines rewritten: %d" % changed)
    open(dst, "w", encoding="latin-1", newline="").write(eol.join(lines))
    print("->", dst)


if __name__ == "__main__":
    main()
