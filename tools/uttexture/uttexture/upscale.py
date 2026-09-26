"""Upscaling, wrap-padded so tiling textures stay tiling.

Run an upscaler on a bare tile and it invents different content at each of the
four edges, so the seams stop matching. Each texture is padded by wrapping its
opposite edge round first, upscaled with that margin in place, then cropped
back. Measured on a 1024 grate, as mean absolute difference between the first
and last pixel column against a known-continuous interior column: source 4.06 vs
35.04 interior, wrap-padded 4x 1.85 vs 1.74, naive 4x 7.05 vs 1.60.
"""

import os, shutil, subprocess, sys

MARGIN = 64


def margin_for(w, h):
    m = min(MARGIN, w // 4, h // 4)
    return max(4, (m // 4) * 4)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    # realesrgan-ncnn-vulkan exits 0 even when the model is missing: it prints
    # "find_blob_index_by_name output failed" and writes a garbage image.
    if r.returncode != 0 or "find_blob_index_by_name" in (r.stdout + r.stderr):
        sys.stderr.write(" ".join(cmd) + "\n" + r.stdout + r.stderr + "\n")
        raise SystemExit("upscale failed")
    return r


def check_model(model, models_dir):
    for ext in (".param", ".bin"):
        if not os.path.exists(os.path.join(models_dir, model + ext)):
            have = sorted({n.rsplit(".", 1)[0] for n in os.listdir(models_dir)})
            raise SystemExit("no model %r in %s\navailable: %s"
                             % (model, models_dir, ", ".join(have)))


def pad(src, dst, w, h, m):
    run(["magick", src, "-virtual-pixel", "tile",
         "-define", "distort:viewport=%dx%d-%d-%d" % (w + 2 * m, h + 2 * m, m, m),
         "-distort", "SRT", "0", "+repage", dst])


def one(project, rec, model, models_dir, log=print):
    model = project.config.get("model_overrides", {}).get(rec["name"], model)
    src = os.path.join(project.dir, rec["png"])
    dst = os.path.join(project.up, rec["name"] + ".png")
    restyle = (rec["scale"] == 1 and not rec.get("skipped")
               and project.config.get("restyle"))
    if rec["scale"] == 1 and not restyle:
        shutil.copy2(src, dst)
        return "copied"
    w, h = rec["width"], rec["height"]
    # Restyling runs the model at its own factor and resizes the result back.
    # An image model has a fixed scale -- the anime model is 4x -- so there is
    # no "run it at 1x"; going up and back down is what applies its look at
    # source size, and the round trip is the point, not a workaround.
    s = project.config.get("restyle_factor", 4) if restyle else rec["scale"]
    m = margin_for(w, h)
    padded = os.path.join(project.work, "_pad.png")
    big = os.path.join(project.work, "_big.png")
    rgb = os.path.join(project.work, "_rgb.png")
    alpha = os.path.join(project.work, "_a.png")
    bigalpha = os.path.join(project.work, "_abig.png")
    tmp = [padded, big]

    # An RGBA source has to be split first. realesrgan reads the alpha channel
    # and treats near-zero pixels as transparent, rewriting their RGB to
    # garbage -- which is invisible for a texture that blends by alpha and
    # catastrophic for one that does not. GeoWallA is stored DXT5 with an alpha
    # channel that is 96% zero and bAlphaTexture=false, so the game shows the
    # ruined RGB: black voids in the shape of the alpha mask.
    source = src
    if rec.get("has_alpha"):
        run(["magick", src, "-alpha", "extract", alpha])
        run(["magick", src, "-alpha", "off", rgb])
        source = rgb
        tmp += [rgb, alpha, bigalpha]

    pad(source, padded, w, h, m)
    run(["realesrgan-ncnn-vulkan", "-i", padded, "-o", big,
         "-s", str(s), "-n", model, "-m", models_dir, "-f", "png"])
    run(["magick", big, "-crop", "%dx%d+%d+%d" % (w * s, h * s, m * s, m * s),
         "+repage", dst])
    if restyle:
        run(["magick", dst, "-filter", "Lanczos",
             "-resize", "%dx%d!" % (w, h), dst])

    if rec.get("has_alpha"):
        # The mask is resized rather than modelled: it carries no detail to
        # recover, and a model would invent soft edges on a hard cutout.
        run(["magick", alpha, "-filter", "Lanczos",
             "-resize", "%dx%d!" % (w * s, h * s), bigalpha])
        run(["magick", dst, bigalpha, "-alpha", "off",
             "-compose", "CopyOpacity", "-composite", dst])

    for f in tmp:
        if os.path.exists(f):
            os.remove(f)
    note = ""
    if model != project.config["model"]:
        note = " [%s]" % model
    if restyle:
        return "%dx%d restyled via %dx%s%s" % (w, h, s,
                                               " +alpha" if rec.get("has_alpha") else "",
                                               note)
    return "%dx%d (margin %d)%s%s" % (w * s, h * s, m,
                                      " +alpha" if rec.get("has_alpha") else "", note)


def run_all(project, manifest, only=None, log=print):
    model = project.config["model"]
    models_dir = project.config["models_dir"]
    check_model(model, models_dir)
    for m in set(project.config.get("model_overrides", {}).values()):
        check_model(m, models_dir)
    recs = [r for r in manifest["textures"] if not only or r["name"] in only]
    log("model %s (%s), %d textures" % (model, models_dir, len(recs)))
    for i, rec in enumerate(recs, 1):
        log("[%2d/%d] %-24s %s" % (i, len(recs), rec["name"],
                                   one(project, rec, model, models_dir, log)))
