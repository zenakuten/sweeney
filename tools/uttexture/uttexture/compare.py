"""Render one texture through every upscale method, for side-by-side review.

Scalar image metrics are not trustworthy here. A high-pass "detail" measure
ranked the sharpest ESRGAN variant best on this map's concrete -- and the gain
it was measuring *was* the artifact, hard-edged polygons that read as an aerial
view of city blocks. Judge these by eye, on flat areas, at 100%, and tiled.
"""

import os, subprocess, sys

NCNN_RE = "/usr/share/realesrgan-ncnn-vulkan/models"
NCNN_UP = "/usr/lib/upscayl/models"

METHODS = [
    ("00_source-nearest", "point", None, None),
    ("01_lanczos", "lanczos", None, None),
    ("02_realesrgan-x4plus", "ncnn", "realesrgan-x4plus", NCNN_RE),
    ("03_realesrgan-x4plus-anime", "ncnn", "realesrgan-x4plus-anime", NCNN_RE),
    ("04_realesr-animevideov3", "ncnn", "realesr-animevideov3-x4", NCNN_RE),
    ("05_upscayl-standard", "ncnn", "upscayl-standard-4x", NCNN_UP),
    ("06_upscayl-lite", "ncnn", "upscayl-lite-4x", NCNN_UP),
    ("07_ultrasharp", "ncnn", "ultrasharp-4x", NCNN_UP),
    ("08_remacri", "ncnn", "remacri-4x", NCNN_UP),
    ("09_high-fidelity", "ncnn", "high-fidelity-4x", NCNN_UP),
    ("10_ultramix-balanced", "ncnn", "ultramix-balanced-4x", NCNN_UP),
    ("11_digital-art", "ncnn", "digital-art-4x", NCNN_UP),
    ("12_DAT-NomosUniDAT", "torch", "4xNomosUniDAT_otf", None),
    ("13_HAT-L-Nomos8kSCHAT", "torch", "4xNomos8kSCHAT-L", None),
]


def _sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0 or "find_blob_index_by_name" in (r.stdout + r.stderr):
        sys.stderr.write(" ".join(map(str, cmd)) + "\n" + r.stdout + r.stderr + "\n")
        raise SystemExit("failed")
    return r


def run(project, texture, log=print):
    from .project import MODELS
    from .upscale import margin_for, pad
    man = project.load("manifest.json")
    rec = next((r for r in man["textures"] if r["name"] == texture), None)
    if rec is None:
        raise SystemExit("no such texture in the manifest: " + texture)
    src = os.path.join(project.dir, rec["png"])
    w, h, s = rec["width"], rec["height"], rec["scale"]
    out = os.path.join(project.dir, "compare", texture)
    os.makedirs(out, exist_ok=True)
    m = margin_for(w, h)
    padded = os.path.join(project.work, "_cmp_pad.png")
    pad(src, padded, w, h, m)
    crop = "%dx%d+%d+%d" % (w * s, h * s, m * s, m * s)

    for name, kind, model, mdir in METHODS:
        dst = os.path.join(out, name + ".png")
        if os.path.exists(dst):
            log("  %-28s (have)" % name); continue
        if kind in ("point", "lanczos"):
            _sh(["magick", src, "-filter", "Point" if kind == "point" else "Lanczos",
                 "-resize", "%d%%" % (s * 100), dst])
        elif kind == "ncnn":
            big = os.path.join(project.work, "_cmp_big.png")
            _sh(["realesrgan-ncnn-vulkan", "-i", padded, "-o", big,
                 "-s", str(s), "-n", model, "-m", mdir, "-f", "png"])
            _sh(["magick", big, "-crop", crop, "+repage", dst])
        elif kind == "torch":
            weights = os.path.join(MODELS, model + ".safetensors")
            if not os.path.exists(weights):
                log("  %-28s (no weights)" % name); continue
            staged = os.path.join(project.up, texture + ".png")
            keep = staged + ".keep"
            if os.path.exists(staged):
                os.replace(staged, keep)
            try:
                _sh([sys.executable, "-m", "uttexture.torch_runner", project.map,
                     "--model", weights, texture],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                os.replace(staged, dst)
            finally:
                if os.path.exists(keep):
                    os.replace(keep, staged)
        log("  %-28s ok" % name)
    log("%d files -> %s" % (len(os.listdir(out)), out))

