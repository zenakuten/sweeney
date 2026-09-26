#!/usr/bin/env python3
"""Compare the solid space of two built .ut2 maps.

    python3 tools/compare_bsp.py a.ut2 b.ut2 [step]

UE2 treats zone 0 as solid (Engine/Src/UnTrace.cpp:760 walks the tree;
UnPhysic.cpp:336 kills anything standing in it), so walking PointRegion over a
grid says exactly where two builds disagree about what is solid -- which is what
"there is a random BSP blocking the path" means in measurable terms.

NOTE: point_region returns a (zone, nodes) TUPLE. Comparing it to 0 directly is
always False, which silently turns this whole check into a no-op that reports
everything as fine. That mistake cost real debugging time on DM-1on1-Aerowalk;
the sanity assertions below exist so it cannot happen quietly again.
"""

import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "ut3converter", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "uttexture"))
from sweeney import ut3converter_tools                       # noqa: E402
_t = ut3converter_tools()
if not _t:
    raise SystemExit("this check needs ut3converter; set tools.ut3converter "
                     "in ~/.sweeney/config.json")
sys.path.insert(0, _t)

from ut2bsp import load_bsp, point_region                      # noqa: E402


def solid(bsp, p):
    return point_region(bsp, p)[0] == 0


def bounds(bsp, span=3200):
    return (-span, span, -span, span, -1024, 1792)


def main():
    a_path, b_path = sys.argv[1], sys.argv[2]
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 32
    A, B = load_bsp(a_path), load_bsp(b_path)

    # If these fail the parse is wrong and every number below is meaningless.
    assert solid(A, (100000, 100000, 100000)), "outside the world should be solid"
    assert solid(B, (100000, 100000, 100000)), "outside the world should be solid"

    x0, x1, y0, y1, z0, z1 = bounds(A)
    extra, missing, both = [], [], 0
    x = x0
    while x <= x1:
        y = y0
        while y <= y1:
            z = z0
            while z <= z1:
                p = (x, y, z)
                sa, sb = solid(A, p), solid(B, p)
                if sb and not sa:
                    extra.append(p)
                elif sa and not sb:
                    missing.append(p)
                else:
                    both += 1
                z += step
            y += step
        x += step

    total = both + len(extra) + len(missing)
    print("%s\n  vs %s\n  %d samples at %duu" % (a_path, b_path, total, step))
    print("  solid in B, empty in A : %5d  (%.3f%%)   <- blocks the player"
          % (len(extra), 100.0 * len(extra) / total))
    print("  empty in B, solid in A : %5d  (%.3f%%)   <- holes"
          % (len(missing), 100.0 * len(missing) / total))
    if extra:
        import collections
        c = collections.Counter((p[0] // 256 * 256, p[1] // 256 * 256,
                                 p[2] // 256 * 256) for p in extra)
        print("  %d distinct 256uu regions; worst:" % len(c))
        for k, v in c.most_common(6):
            print("     %-22s %d points" % (str(k), v))


if __name__ == "__main__":
    main()
