"""Find textures the upscaler smeared: detail lost relative to the source.

realesrgan-x4plus-anime is trained on line art and sometimes flattens a whole
region of a photographic texture into a smooth blob -- a visible blur patch in
game (reported on Koden's BrickWall12c). It is localised, so a whole-image
average hides it; this grades an 8x8 grid and reports the worst cell.

Score is gradient energy of the upscale, downsampled back to source size,
over the source's own: 1.0 means the detail survived, 0.2 means four fifths of
it went. Cells whose source is nearly flat carry no detail to lose and are
skipped, or they dominate the result with meaningless ratios.

  python3 tools/check_detail.py DM-Koden                 # grade what is in up/
  python3 tools/check_detail.py DM-Koden --try x4plus    # and retest the bad
                                                         # ones with another model
Textures it names go in the project's "model_overrides".
"""

import argparse, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uttexture.project import Project, ROOT

Image.MAX_IMAGE_PIXELS = None
GRID = 8
FLAT = 0.15          # a cell below this share of the mean is flat; skip it


def energy(a, n=GRID):
    gy, gx = np.gradient(a)
    e = gx * gx + gy * gy
    h, w = a.shape
    ch, cw = h // n, w // n
    return np.array([[e[i * ch:(i + 1) * ch, j * cw:(j + 1) * cw].mean()
                      for j in range(n)] for i in range(n)])


def grade(src_png, up_png):
    s = np.asarray(Image.open(src_png).convert("L"), dtype=np.float32)
    if min(s.shape) < GRID * 8:
        return None
    u = Image.open(up_png).convert("L").resize(s.shape[::-1], Image.LANCZOS)
    es, eu = energy(s), energy(np.asarray(u, dtype=np.float32))
    r = np.where(es > es.mean() * FLAT, eu / np.maximum(es, 1e-6), np.nan)
    if np.all(np.isnan(r)):
        return None
    return float(np.nanmean(r)), float(np.nanmin(r))


def upscale_with(src_png, model, models_dir, scale, work):
    """Re-run one texture through another model, wrap-padded as upscale.py does."""
    from uttexture import upscale
    w, h = Image.open(src_png).size
    m = upscale.margin_for(w, h)
    pad = os.path.join(work, "_p.png")
    big = os.path.join(work, "_b.png")
    out = os.path.join(work, "_o.png")
    upscale.pad(src_png, pad, w, h, m)
    upscale.run(["realesrgan-ncnn-vulkan", "-i", pad, "-o", big, "-s", str(scale),
                 "-n", model, "-m", models_dir, "-f", "png"])
    subprocess.run(["magick", big, "-crop", "%dx%d+%d+%d"
                    % (w * scale, h * scale, m * scale, m * scale), "+repage", out],
                   check=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--worst", type=float, default=0.55,
                    help="flag a texture whose worst cell falls below this")
    ap.add_argument("--try", dest="alt", default=None,
                    help="retest flagged textures with this model")
    args = ap.parse_args()

    project = Project(args.map)
    import json
    manifest = json.load(open(os.path.join(project.meta, "manifest.json")))
    models_dir = project.config["models_dir"]

    bad = []
    for rec in manifest["textures"]:
        if rec.get("scale", 1) == 1:
            continue
        src = os.path.join(project.dir, rec["png"])
        up = os.path.join(project.up, rec["name"] + ".png")
        if not (os.path.exists(src) and os.path.exists(up)):
            continue
        g = grade(src, up)
        if g and g[1] < args.worst:
            bad.append((g[1], g[0], rec))

    bad.sort()
    print("%s: %d of %d upscaled textures lost detail (worst cell < %.2f)"
          % (args.map, len(bad), len(manifest["textures"]), args.worst))
    if not bad:
        return

    alt_wins = []
    with tempfile.TemporaryDirectory() as work:
        for worst, mean, rec in bad:
            line = "  %-28s %4dx%-4d  mean %.2f  worst %.2f" % (
                rec["name"], rec["width"], rec["height"], mean, worst)
            if args.alt:
                out = upscale_with(os.path.join(project.dir, rec["png"]),
                                   args.alt, models_dir, rec["scale"], work)
                g = grade(os.path.join(project.dir, rec["png"]), out)
                line += "   -> %s mean %.2f worst %.2f" % (args.alt, g[0], g[1])
                if g[1] > worst * 1.5 and g[1] >= args.worst:
                    alt_wins.append(rec["name"])
                    line += "  FIXED"
            print(line)

    if args.alt and alt_wins:
        print('\n  "model_overrides": {')
        print(",\n".join('    "%s": "%s"' % (n, args.alt) for n in sorted(alt_wins)))
        print("  }")


if __name__ == "__main__":
    main()
