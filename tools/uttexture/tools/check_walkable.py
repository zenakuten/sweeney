#!/usr/bin/env python3
"""Compare the space a PLAYER can occupy in two built maps.

    python3 tools/check_walkable.py a.ut2 b.ut2 [step] [x0 x1 y0 y1 z0 z1]

Point-sampling solidity is useless for this: a .t3d round trip moves surface
planes by hundredths of a unit, and a sample landing exactly on a face reads as
a difference no player could ever feel. DM-Entropic looked like it had 1050
blocked cells until the faces were binary-searched and found 0.00013uu apart.

Testing a 34x34x88 box instead -- UT2004's human: radius 17, half-height 44 --
ignores sub-unit drift entirely. Only space a player could actually stand in
counts, which is what "there is invisible geometry in my way" means.

Cells LOST in B are reported as connected blobs with their bounding boxes, so a
missing-brush-shaped volume is obvious: it lands on grid multiples, where a CSG
sliver lands on arbitrary fractions.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "uttexture"))
from sweeney import ut3converter_tools                       # noqa: E402
_t = ut3converter_tools()
if not _t:
    raise SystemExit("this check needs ut3converter; set tools.ut3converter "
                     "in ~/.sweeney/config.json")
sys.path.insert(0, _t)
from ut2bsp import load_bsp, point_region                    # noqa: E402

R, H = 17.0, 44.0
CORNERS = [(dx, dy, dz) for dx in (-R, 0, R) for dy in (-R, 0, R) for dz in (-H, 0, H)]


def free(bsp, p):
    for dx, dy, dz in CORNERS:
        if point_region(bsp, (p[0] + dx, p[1] + dy, p[2] + dz))[0] == 0:
            return False
    return True


def blobs(cells, step):
    seen, out = set(), []
    for c in cells:
        if c in seen:
            continue
        stack, blob = [c], []
        seen.add(c)
        while stack:
            p = stack.pop()
            blob.append(p)
            for d in ((step, 0, 0), (-step, 0, 0), (0, step, 0),
                      (0, -step, 0), (0, 0, step), (0, 0, -step)):
                q = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
                if q in cells and q not in seen:
                    seen.add(q)
                    stack.append(q)
        out.append(blob)
    out.sort(key=len, reverse=True)
    return out


def main():
    a_path, b_path = sys.argv[1], sys.argv[2]
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 32
    if len(sys.argv) > 9:
        box = [float(v) for v in sys.argv[4:10]]
    else:
        box = [-3500, 3500, -3500, 3500, -1500, 1500]
    A, B = load_bsp(a_path), load_bsp(b_path)
    assert not free(A, (1e6, 1e6, 1e6)), "outside the world must not be free"
    x0, x1, y0, y1, z0, z1 = box

    lost, gained, both = set(), 0, 0
    x = x0
    while x <= x1:
        y = y0
        while y <= y1:
            z = z0
            while z <= z1:
                fa, fb = free(A, (x, y, z)), free(B, (x, y, z))
                if fa and not fb:
                    lost.add((int(x), int(y), int(z)))
                elif fb and not fa:
                    gained += 1
                elif fa:
                    both += 1
                z += step
            y += step
        x += step

    print("%s\n  vs %s" % (os.path.basename(a_path), os.path.basename(b_path)))
    print("  nodes %d -> %d" % (len(A), len(B)))
    print("  player-sized space: %d cells free in both, %d LOST, %d gained"
          % (both, len(lost), gained))
    for blob in blobs(lost, step)[:8]:
        lo = [min(p[i] for p in blob) for i in range(3)]
        hi = [max(p[i] for p in blob) for i in range(3)]
        print("     %5d cells  %-24s .. %-24s size %s"
              % (len(blob), tuple(lo), tuple(hi),
                 tuple(hi[i] - lo[i] + step for i in range(3))))
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
