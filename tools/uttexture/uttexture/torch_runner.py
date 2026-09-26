#!/usr/bin/env python3
"""Upscale with a spandrel-loaded PyTorch model.

    python3 -m uttexture.torch_runner <Map> --model models/X.safetensors [texture...]

Same wrap-padding as the ncnn path -- these textures tile, and a model run on a
bare tile invents different content at each edge so the seams stop matching.

The reason for going past realesrgan-ncnn-vulkan: every ESRGAN-family model
shipped with Upscayl either smooths the texture to plastic (x4plus, digital-art,
upscayl-standard) or posterizes flat areas into hard-edged polygons that read as
an aerial view of city blocks (remacri, ultrasharp, high-fidelity, ultramix).
Transformer architectures -- DAT, HAT, SwinIR -- are markedly better behaved on
flat surfaces, and spandrel loads them from a bare .pth without needing to know
which architecture it is.

    python3 tools/upscale_torch.py --model models/4xNomos8kSCHAT-L.pth [name...]
"""
import os, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# torch cannot even be imported on this box without help. python-pytorch-rocm
# depends on roctracer/rocprofiler, but opencl-amd (AUR) declares provides= for
# those names and conflicts with the real packages, so pacman considered the
# dependency satisfied and the libraries were never installed -- torch dies with
# ImportError on librocprofiler-sdk.so.1 and libroctx64.so.4.
#
# .rocmlibs/stub/ holds the real libroctx64 (extracted from the repo package,
# ABI-compatible) plus a no-op librocprofiler-sdk built from stub.c. Using the
# REAL rocprofiler-sdk there segfaults instead: opencl-amd's
# librocprofiler-register.so.0 finds it and calls across a mismatched ABI.
#
# LD_LIBRARY_PATH is read by the dynamic linker at process start, so it cannot
# be set from inside a running interpreter -- re-exec once with it in place.
_STUB = os.path.join(ROOT, ".rocmlibs", "stub")   # shared, at the project root


def _bootstrap():
    if not os.path.isdir(_STUB):
        return                                   # a sane ROCm install, nothing to do
    current = os.environ.get("LD_LIBRARY_PATH", "")
    if _STUB in current.split(":"):
        return
    os.environ["LD_LIBRARY_PATH"] = _STUB + (":" + current if current else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)


_bootstrap()
META = os.path.join(ROOT, "meta")
UP   = os.path.join(ROOT, "up")
WORK = os.path.join(ROOT, ".work")

MARGIN = 64          # wrap margin, in source pixels
TILE   = 512         # process in tiles; 4096x4096 at once will not fit in VRAM
OVERLAP = 32         # tile overlap, averaged in the shared region


def margin_for(w, h):
    m = min(MARGIN, w // 4, h // 4)
    return max(4, (m // 4) * 4)


def load_model(path):
    import torch
    from spandrel import ImageModelDescriptor, ModelLoader
    model = ModelLoader().load_from_file(path)
    assert isinstance(model, ImageModelDescriptor), "not a single-image model"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        sys.stderr.write("WARNING: no GPU visible to torch, this will be slow\n")
    return model.eval().to(device), device, model.scale


def run_tiled(model, device, img):
    """img: float tensor 1x3xHxW on device. Tiled to keep VRAM bounded.

    Overlapping tiles are averaged with uniform weights. A feathered window was
    tried and removed: measured on bas05dHA, plain averaging leaves no seam at
    all (worst column/row step at a tile boundary stayed below the median step
    for the texture), while the feather introduced one, because ramping every
    tile edge to zero also ramps the outer image border where no neighbour
    overlaps. Simple is both correct and measurably better here.
    """
    import torch
    _, _, h, w = img.shape
    scale = model.scale
    out = torch.zeros((1, 3, h * scale, w * scale), device=device)
    weight = torch.zeros((1, 1, h * scale, w * scale), device=device)
    step = TILE - OVERLAP
    for y in range(0, h, step):
        for x in range(0, w, step):
            y1, x1 = min(y + TILE, h), min(x + TILE, w)
            y0, x0 = max(0, y1 - TILE), max(0, x1 - TILE)
            with torch.no_grad():
                tile = model(img[:, :, y0:y1, x0:x1])
            out[:, :, y0 * scale:y1 * scale, x0 * scale:x1 * scale] += tile
            weight[:, :, y0 * scale:y1 * scale, x0 * scale:x1 * scale] += 1
    if float(weight.min()) <= 0:
        raise SystemExit("tiling left uncovered pixels -- check TILE/OVERLAP")
    return out / weight


def _infer(model, device, planes, ph, pw):
    """Run the model over an HxWx3 float array and return the upscaled array."""
    import numpy as np, torch
    t = torch.from_numpy(planes).permute(2, 0, 1).unsqueeze(0).to(device)
    big = run_tiled(model, device, t)
    return big.clamp(0, 1).squeeze(0).permute(1, 2, 0).cpu().numpy()


def has_alpha(path):
    out = subprocess.run(["magick", "identify", "-format", "%[channels]", path],
                         capture_output=True, text=True, check=True).stdout
    return "rgba" in out or "graya" in out


def upscale(model, device, src, dst, width, height, scale):
    import numpy as np
    m = margin_for(width, height)
    padded = os.path.join(WORK, "_t_pad.png")
    vp = "%dx%d-%d-%d" % (width + 2 * m, height + 2 * m, m, m)
    subprocess.run(["magick", src, "-virtual-pixel", "tile",
                    "-define", "distort:viewport=" + vp, "-distort", "SRT", "0",
                    "+repage", padded], check=True)
    pw, ph = width + 2 * m, height + 2 * m
    alpha = has_alpha(src)

    raw = subprocess.run(["magick", padded, "-alpha", "off", "-depth", "8", "RGB:-"],
                         capture_output=True, check=True).stdout
    rgb = np.frombuffer(raw, np.uint8).reshape(ph, pw, 3).astype(np.float32) / 255.0
    big = _infer(model, device, rgb, ph, pw)

    if alpha:
        # The model is a 3-channel network, so alpha gets its own pass with the
        # channel replicated across RGB. It cannot just be resized: for a cutout
        # like grt01HA the alpha IS the detail -- it defines the holes in the
        # grate -- and it has to gain the same sharpness as the colour or the
        # edges of every hole go soft against a crisp interior.
        araw = subprocess.run(["magick", padded, "-alpha", "extract",
                               "-depth", "8", "GRAY:-"],
                              capture_output=True, check=True).stdout
        a = np.frombuffer(araw, np.uint8).reshape(ph, pw).astype(np.float32) / 255.0
        abig = _infer(model, device, np.repeat(a[:, :, None], 3, axis=2), ph, pw)
        abig = abig.mean(axis=2, keepdims=True)
        # Sharpening alpha also shifts its mean, and for these textures alpha is
        # opacity -- glass03 drifted 0.238 -> 0.210, i.e. 12% more transparent
        # than the original glass. Rescale to the source mean so the material
        # keeps its intended translucency; clipping makes this a no-op for a
        # hard cutout like grt01HA, which is already within 0.001.
        src_mean, new_mean = float(a.mean()), float(abig.mean())
        if new_mean > 1e-6:
            abig = np.clip(abig * (src_mean / new_mean), 0.0, 1.0)
        big = np.concatenate([big, abig], axis=2)

    big = (big * 255.0 + 0.5).clip(0, 255).astype(np.uint8)
    o = m * scale
    big = big[o:o + height * scale, o:o + width * scale]
    fmt = "RGBA:-" if alpha else "RGB:-"
    subprocess.run(["magick", "-size", "%dx%d" % (width * scale, height * scale),
                    "-depth", "8", fmt, dst], input=big.tobytes(), check=True)
    os.remove(padded)
    return "%dx%d (margin %d)%s" % (width * scale, height * scale, m,
                                    " +alpha" if alpha else "")


def main():
    import shutil
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("--"):
        raise SystemExit("usage: python3 -m uttexture.torch_runner <Map> --model X.safetensors [texture...]")
    sys.path.insert(0, ROOT)
    from uttexture.project import Project
    project = Project(argv.pop(0))
    if "--model" not in argv:
        raise SystemExit("--model <path to .safetensors> is required")
    i = argv.index("--model")
    model_path = argv[i + 1]
    del argv[i:i + 2]

    global UP, WORK
    UP, WORK = project.up, project.work
    man = project.load("manifest.json")
    if man is None:
        raise SystemExit("run `./uttexture.py extract %s` first" % project.map)
    model, device, scale = load_model(model_path)
    print("%s on %s, scale x%d" % (os.path.basename(model_path), device, scale))

    recs = [r for r in man["textures"] if not argv or r["name"] in argv]
    for n, rec in enumerate(recs, 1):
        print("[%2d/%d] %-22s " % (n, len(recs), rec["name"]), end="", flush=True)
        src = os.path.join(project.dir, rec["png"])
        dst = os.path.join(UP, rec["name"] + ".png")
        if rec["scale"] == 1:
            shutil.copy2(src, dst)
            print("copied", flush=True)
            continue
        if scale != rec["scale"]:
            raise SystemExit("model is x%d but manifest wants x%d" % (scale, rec["scale"]))
        print(upscale(model, device, src, dst,
                      rec["width"], rec["height"], scale), flush=True)


if __name__ == "__main__":
    main()
