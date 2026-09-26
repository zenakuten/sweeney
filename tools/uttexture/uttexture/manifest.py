"""Texture properties, and the decisions that follow from them.

`#exec TEXTURE IMPORT` can carry LODSET, NORMALLOD, ALPHA, MASKED, DXT and the
clamp modes, but not Detail, DetailScale or SurfaceType -- the importer only
preserves those when overwriting a texture that already exists
(Editor/Src/UnEdSrv.cpp:586-704). Those three are read here and restored with
`#exec OBJ POKE` at package time. Losing SurfaceType changes footstep and impact
sounds; losing Detail drops the close-range detail overlay.
"""

import os, re

from .survey import umodel_dump
from .extract import find_png, probe


def parse_dumps(meta_dir):
    """umodel -dump output -> {object: {property: raw value}}."""
    out = {}
    for fn in sorted(os.listdir(meta_dir)):
        if not fn.startswith("tex-") or not fn.endswith(".txt"):
            continue
        current = None
        for line in open(os.path.join(meta_dir, fn), encoding="latin-1"):
            head = re.match(r"ClassName: (\S+) ObjectName: (\S+)", line.strip())
            if head:
                current = out.setdefault(head.group(2), {"_class": head.group(1)})
                continue
            if current is None:
                continue
            kv = re.match(r"^    (\w+) = (.*)$", line.rstrip())
            if kv:
                current.setdefault(kv.group(1), kv.group(2))
    return out


def enum_int(value, default=0):
    m = re.search(r"\((\d+)\)\s*$", value or "")
    if m:
        return int(m.group(1))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Every source format that can hold a real alpha channel. DXT1 has at most one
# bit of it, so anything landing there loses its mask. Listing only DXT3/DXT5
# here quietly flattened uncompressed masks: DM-Elucidation's cf_FaceMaskTop is
# TEXF_RGBA8 with alpha_mean 0.03 -- almost entirely transparent -- and came
# back DXT1, which took the top face of the sky with it.
ALPHA_FORMATS = ("DXT3", "DXT5", "RGBA8", "BGRA8", "RGBA7", "P8")


def _carries_alpha(fmt):
    return any(f in fmt for f in ALPHA_FORMATS)


def build(project, survey, log=print):
    """Dump properties for every texture, then decide format and scale."""
    from .extract import source_packages
    for package, names in sorted(source_packages(survey["textures"]).items()):
        umodel_dump(project.install, package, names,
                    os.path.join(project.meta, "tex-%s.txt" % package))
    props = parse_dumps(project.meta)

    scale = project.config["scale"]
    skip = set(project.config.get("skip_upscale", []))
    records = []
    for leaf, entry in sorted(survey["textures"].items(), key=lambda kv: kv[0].lower()):
        png = find_png(project, leaf)
        if not png:
            log("  no image extracted for %s" % leaf)
            continue
        w, h, has_alpha, alpha_mean = probe(png)
        p = props.get(leaf, {})
        fmt = p.get("Format", "")
        masked = p.get("bMasked", "false") == "true"
        alpha_tex = p.get("bAlphaTexture", "false") == "true"

        # umodel hands back an alpha channel for plenty of textures that never
        # used one -- a P8 palette's unused alpha byte, a DXT3 block's constant
        # 0xFF. Trust the source's own flag first, and otherwise keep alpha only
        # where it is both stored in an alpha-carrying format and actually
        # varies. Everything else drops to DXT1, which halves the texture and
        # avoids importing a bogus transparency.
        keep_alpha = alpha_tex or masked or (
            has_alpha and alpha_mean < 0.99 and _carries_alpha(fmt))

        # Storing alpha and *blending* with it are different things, and the
        # import parameters for them are different too: DXT=5 keeps the channel,
        # while ALPHA= sets bAlphaTexture, which makes the renderer draw the
        # surface FB_AlphaBlend (UnEdSrv.cpp:617, D3D9MaterialState.cpp:1645).
        # Driving ALPHA= off keep_alpha turned every DXT5 texture into a
        # translucent one: DM-Rankin's GeoWallA is stored DXT5 but has
        # bAlphaTexture=false, and came out see-through with only its detail
        # layer visible. Follow the source's own flag.
        alpha_texture = alpha_tex

        skipped = leaf in skip
        factor = 1 if skipped else scale
        detail = re.match(r"\w+'(?:[\w\-]+\.)?([\w\-]+)'$", p.get("Detail", "None"))
        records.append({
            "name": leaf,
            "group": entry["group"] or "Misc",
            "source": entry["path"],
            "png": os.path.relpath(png, project.dir),
            "width": w, "height": h,
            "has_alpha": has_alpha, "alpha_mean": round(alpha_mean, 4),
            "keep_alpha": keep_alpha,
            "alpha_texture": alpha_texture,
            "scale": factor,
            # 1x means two different things: this texture was excluded, or the
            # project rebuilds at source size. Only the first is a plain copy.
            "skipped": skipped,
            "out_width": w * factor, "out_height": h * factor,
            "dxt": 5 if keep_alpha else 1,
            "masked": masked, "source_format": fmt,
            "wrap_u": enum_int(p.get("UClampMode", "0")) == 0,
            "wrap_v": enum_int(p.get("VClampMode", "0")) == 0,
            "lodset": enum_int(p.get("LODSet", "1"), 1),
            "normal_lod": enum_int(p.get("NormalLOD", "0")),
            "surface_type": enum_int(p.get("SurfaceType", "0")),
            "detail": detail.group(1) if detail else None,
            "detail_source": p.get("Detail", "None"),
            "detail_scale": p.get("DetailScale", "8"),
            "surfaces": entry["surfaces"], "mesh": entry["mesh"],
        })
    return {"map": project.map, "package": project.package,
            "scale": scale, "textures": records}
