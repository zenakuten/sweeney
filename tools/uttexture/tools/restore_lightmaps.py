#!/usr/bin/env python3
"""Copy per-surface LightMapScale from a source map into its 4K rebuild.

    python3 tools/restore_lightmaps.py original.ut2 rebuilt.ut2 [--dry-run]

A .t3d cannot carry it. `UPolysExporterT3D::ExportText` never writes it and the
polygon parser (Editor/Src/UnEdFact.cpp:1588) accepts only LINK, ITEM, FLAGS,
TEXTURE, PAN, ORIGIN, VERTEX, TEXTUREU and TEXTUREV -- so every surface comes
back at the editor's default and the original lighting resolution is lost.

It can be put back afterwards. CSG copies the value from the brush polygon to
the surface (`Surf->LightMapScale = EdPoly->LightMapScale`, UnBsp.cpp:226), and
in the built package it is a plain float in each FBspSurf record
(UnModel.cpp:45) -- fixed size, so rewriting it in place moves nothing and needs
no re-indexing.

Surfaces are matched between the two maps by material and plane. A BSP rebuild
splits and reorders surfaces, so the counts differ and the match is many-to-one;
where a plane is not found, the most common scale that material had in the
original is used, and failing that the value is left alone.
"""

import os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uttexture.ue2 import Package                                   # noqa: E402


def leaf(material):
    """Compare materials by leaf name, ignoring which package they now live in.

    The rebuild repoints every surface: `cp_wasteland.Rock.cp_scorchedbrick1`
    becomes `MyLevel.Rock.cp_scorchedbrick1`, and anything needing
    Detail/SurfaceType or a rebuilt composite gains a `_SH` wrapper. Matching on
    the full path finds almost nothing.
    """
    name = (material or "").rsplit(".", 1)[-1]
    return name[:-3] if name.endswith("_SH") else name


def key(surf, places=1):
    """Material leaf plus quantised plane. The plane survives a rebuild; order does not."""
    return (leaf(surf["material"]),) + tuple(round(v, places) for v in surf["plane"])


def main():
    src_path, dst_path = sys.argv[1], sys.argv[2]
    dry = "--dry-run" in sys.argv

    src = Package(src_path).surfaces()
    dst_pkg = Package(dst_path)
    dst = dst_pkg.surfaces()

    by_plane, by_material = {}, {}
    for s in src:
        by_plane.setdefault(key(s), []).append(s["lightmap_scale"])
        by_material.setdefault(leaf(s["material"]), []).append(s["lightmap_scale"])

    def common(values):
        return max(set(values), key=values.count)

    data = bytearray(open(dst_path, "rb").read())

    # When the rebuild produced the same number of surfaces, check whether it
    # also produced them in the same ORDER -- on DM-Corrugation every one of
    # 1991 agrees by index on both material and plane, which makes the copy
    # exact rather than a best guess. Fall back to matching by plane only when
    # the rebuild actually reshuffled things.
    positional = len(src) == len(dst) and all(
        leaf(a["material"]) == leaf(b["material"])
        and all(abs(p - q) < 0.01 for p, q in zip(a["plane"], b["plane"]))
        for a, b in zip(src, dst))
    if positional:
        changed = 0
        for a, b in zip(src, dst):
            if abs(a["lightmap_scale"] - b["lightmap_scale"]) > 1e-6:
                struct.pack_into("<f", data, b["scale_offset"], a["lightmap_scale"])
                changed += 1
        print("%s -> %s" % (os.path.basename(src_path), os.path.basename(dst_path)))
        print("  %d surfaces, matched 1:1 by index (exact)" % len(src))
        print("  rewritten          : %d" % changed)
        if dry:
            print("  --dry-run, nothing written")
            return
        if changed:
            open(dst_path, "wb").write(bytes(data))
            print("  written in place")
        return

    exact = fallback = unmatched = already = 0
    for s in dst:
        k = key(s)
        if k in by_plane:
            want = common(by_plane[k]); exact += 1
        elif leaf(s["material"]) in by_material:
            want = common(by_material[leaf(s["material"])]); fallback += 1
        else:
            unmatched += 1
            continue
        if abs(want - s["lightmap_scale"]) < 1e-6:
            already += 1
            continue
        struct.pack_into("<f", data, s["scale_offset"], want)

    print("%s -> %s" % (os.path.basename(src_path), os.path.basename(dst_path)))
    print("  source surfaces %d, rebuilt %d" % (len(src), len(dst)))
    print("  matched by plane   : %d" % exact)
    print("  matched by material: %d" % fallback)
    print("  unmatched (left)   : %d" % unmatched)
    print("  already correct    : %d" % already)
    changed = exact + fallback - already
    print("  rewritten          : %d" % changed)
    if dry:
        print("  --dry-run, nothing written")
        return
    if changed:
        open(dst_path, "wb").write(bytes(data))
        print("  written in place")


if __name__ == "__main__":
    main()
