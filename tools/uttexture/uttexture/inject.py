#!/usr/bin/env python3
"""Replace an upscaler's invented micro-detail with real detail from a scan.

A 4x upscale of a 1024 DXT1 texture has nothing real below one source pixel
(4 output pixels) -- DXT1 destroyed it in 2004 -- so every generative model
fabricates that band, each with its own signature: remacri and ultrasharp
posterize flat areas into hard-edged polygons, DAT lays a 4px stipple lattice.

This substitutes measured material instead. The map texture keeps everything
that makes it that texture -- formwork bands, tie-holes, weathering, and its
value, which the lightmaps were baked against -- and only the high band is
swapped for a photogrammetry scan's.

Two properties make this safe rather than a blend of two images:

  * the injected signal is zero-mean, so colour, brightness and contrast of the
    original are untouched; the baked lighting stays correct.
  * the scan is resampled to exactly the target size, so it repeats with the
    same period as the texture and the result still tiles.

`--grain` tiles the scan more than once across the texture, for when the scan's
physical footprint is larger than the surface the texture covers.
"""
import argparse, subprocess, sys
import numpy as np

def read(path, size=None):
    cmd = ["magick", path]
    if size:
        cmd += ["-resize", "%dx%d!" % size]
    cmd += ["-depth", "8", "RGB:-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    w, h = size if size else identify(path)
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float32)

def identify(path):
    w, h = subprocess.run(["magick", "identify", "-format", "%w %h", path],
                          capture_output=True, text=True, check=True).stdout.split()
    return int(w), int(h)

def write(arr, path):
    h, w, _ = arr.shape
    subprocess.run(["magick", "-size", "%dx%d" % (w, h), "-depth", "8", "RGB:-", path],
                   input=np.clip(arr, 0, 255).astype(np.uint8).tobytes(), check=True)

def highpass(img, radius):
    """Everything finer than `radius` pixels, via a box low-pass in the FFT."""
    h, w, _ = img.shape
    k = np.zeros((h, w), np.float32)
    r = max(1, int(radius))
    k[:r, :r] = 1.0 / (r * r)
    K = np.fft.rfft2(k)
    out = np.empty_like(img)
    for c in range(3):
        lo = np.fft.irfft2(np.fft.rfft2(img[:, :, c]) * K, (h, w))
        out[:, :, c] = img[:, :, c] - lo
    return out

def inject(base, scan_path, scale=4, grain=1.0, amount=1.0, chroma=False):
    """base: HxWx3 float. Returns base with its invented high band replaced.

    The scan is resampled to a SQUARE tile and repeated, so grain stays
    isotropic on a non-square texture -- bdr02bHA is 4096x1024, and sizing the
    tile from width and height independently would squash the scan 4:1 and
    stretch its grain with it. The tile is sized from the short edge, which for
    power-of-two textures and integer grain divides both edges exactly, so the
    injected detail repeats with the texture and the result still tiles.
    """
    h, w, _ = base.shape
    side = max(1, int(round(min(w, h) / grain)))
    if w % side or h % side:
        raise SystemExit("grain %g gives a %dpx tile that does not divide %dx%d"
                         % (grain, side, w, h))
    scan = read(scan_path, (side, side))
    scan = np.tile(scan, (h // side, w // side, 1))
    if not chroma:
        # Luminance only. Surface relief modulates brightness, not hue, and a
        # scan whose fine detail carries its own colour (Metal021's blue-grey
        # speckle, Rust009's orange) otherwise lands as chroma noise on a base
        # of a different colour -- visible as blue and orange dots over brown
        # steel. Taking luma and applying it equally to R, G and B keeps the map
        # texture's own colour and adds only the micro-shading.
        luma = (scan * np.array([0.2126, 0.7152, 0.0722], np.float32)).sum(2)
        scan = np.repeat(luma[:, :, None], 3, axis=2)
    detail = highpass(scan, scale)
    # `amount` is the injected detail's standard deviation in 0-255 levels, i.e.
    # an absolute target rather than a gain. Two earlier attempts were worse:
    # a fixed multiplier made the steels visibly rougher than the concretes
    # beside them, because Metal021 carries far more fine detail than
    # Concrete031; and scaling by each texture's own high-frequency energy gave
    # the least detail to the flattest textures, which are precisely the ones
    # that read as low resolution. An absolute target makes every surface gain
    # the same amount of micro-relief whatever its scan or its own contrast.
    if detail.std() > 1e-6:
        detail *= amount / detail.std()
    detail -= detail.mean(axis=(0, 1), keepdims=True)   # keep it value-neutral
    return base + detail, float(detail.std())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("scan"); ap.add_argument("out")
    ap.add_argument("--scale", type=int, default=4,
                    help="upscale factor; detail below this many output px is invented")
    ap.add_argument("--grain", type=float, default=1.0,
                    help="times the scan repeats across the texture")
    ap.add_argument("--amount", type=float, default=8.0,
                    help="injected detail std dev, in 0-255 levels")
    ap.add_argument("--chroma", action="store_true",
                    help="inject colour detail too (default: luminance only)")
    a = ap.parse_args()

    base = read(a.base)
    out, sd = inject(base, a.scan, a.scale, a.grain, a.amount, a.chroma)
    write(out, a.out)
    print("%s + %s(grain %.1f) -> %s   injected sd %.2f, mean shift %+.4f"
          % (a.base.split("/")[-1], a.scan.split("/")[-1], a.grain,
             a.out.split("/")[-1], sd, float(out.mean() - base.mean())))

if __name__ == "__main__":
    main()
