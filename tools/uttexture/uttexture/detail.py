"""Carry the map's detail textures into the rebuilt package.

A detail texture is a tiny tiling grain -- 128x128, occasionally 512 -- that the
renderer modulates over the base at close range (`SrcBlend=DESTCOLOR`,
`DestBlend=SRCCOLOR`, so the result is 2 x base x detail and 128 is neutral).
It is an effect, not artwork, and it is what stops a surface going flat when you
walk into it. Upscaling one would be meaningless: its tiling is set by
DetailScale, not by its own size.

They are copied into <Map>Tex rather than referenced where they live, so the
package needs no `#exec OBJ LOAD` of a stock texture package at build time and
carries no runtime dependency on one. They cost a few tens of KB.

**Do not guess which package holds one.** The name in a texture's Detail
property arrives group-qualified but package-less (`Texture'DetailTextures.
detail40'`), and assuming it sits in the same package as the base texture is
wrong: every detail texture DM-DE-Ironic uses lives in UCGeneric, while its base
textures come from HumanoidArchitecture, HumanoidArchitecture2, ArboreaTerrain
and HumanoidHardwareBrush. That guess produced four unresolvable references and
a failed build, because a defaultproperties object reference is resolved at
compile time. `resolve()` finds the real package by scanning name tables.
"""

import glob, mmap, os, struct, subprocess, zlib

from .ue2 import Reader, TAG

GROUP = "DetailTextures"


def package_names(path):
    """The name table only -- mmapped, so scanning a 74MB .utx stays cheap."""
    try:
        with open(path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                tag, _ver, _lic = struct.unpack_from("<IHH", mm, 0)
                if tag != TAG:
                    return []
                _flags, count, off = struct.unpack_from("<III", mm, 8)[:3]
                r, names = Reader(mm, off), []
                for _ in range(count):
                    n = r.string(); r.u32()
                    names.append(n)
                return names
            finally:
                mm.close()
    except (OSError, ValueError, struct.error, IndexError):
        return []


def generated_packages(project):
    """Every package uttexture builds, across all maps in this checkout.

    They have to be excluded from the search below. A rebuilt package holds a
    group called DetailTextures containing the same NAMES as the stock grain --
    and, in synth mode, content that has already been enlarged. Resolving
    against one means synthesising from a synthesis: the run that found
    DMIronic4KTex before UCGeneric turned a 128px stock grain into 2048px, and
    detail68 into 8192px, silently.
    """
    from .project import ROOT
    import json
    names = {project.package}
    for cfg in glob.glob(os.path.join(ROOT, "maps", "*", "config.json")):
        try:
            names.add(json.load(open(cfg)).get("package"))
        except (OSError, ValueError):
            pass
    # The shared content store builds packages too, and DetailTextures4K holds
    # the stock grain under the stock NAMES. Resolving against it feeds our own
    # output back in as the source: detail68 went 512 -> 2048 -> 8192 and its
    # grain 28.1 -> 11.3 -> 4.5, because strength is reapplied every pass.
    store = os.path.join(project.install, "UT2004K", "content.json")
    try:
        index = json.load(open(store))
        names |= {e.get("package") for e in index.get("packages", {}).values()}
    except (OSError, ValueError):
        pass
    return {n for n in names if n}


def resolve(project, leaves, log=print):
    """{texture leaf: package that exports it}, searching the install."""
    from .ue2 import Package
    wanted, found = set(leaves), {}
    if not wanted:
        return found
    ours = generated_packages(project)
    for path in sorted(glob.glob(os.path.join(project.install, "Textures", "*.utx"))):
        if os.path.splitext(os.path.basename(path))[0] in ours:
            continue
        hits = wanted.intersection(package_names(path))
        if not hits:
            continue
        # A name-table hit can be an import reference rather than the object
        # itself, so confirm against the export table before believing it.
        try:
            exports = {n for n, _p, _e in Package(path).exports_of_class("Texture")}
        except (ValueError, struct.error, IndexError):
            continue
        for leaf in hits & exports:
            found[leaf] = os.path.splitext(os.path.basename(path))[0]
            wanted.discard(leaf)
        if not wanted:
            break
    for leaf in sorted(wanted):
        log("  WARNING: no package found exporting detail texture %s" % leaf)
    return found


def export(project, leaves, log=print):
    """umodel the detail textures out. Returns {leaf: png path}."""
    from .survey import UMODEL, UMODEL_CMD, windows_path
    out = os.path.join(project.work, "detail")
    os.makedirs(out, exist_ok=True)
    made = {}
    by_package = {}
    for leaf, package in sorted(resolve(project, leaves, log=log).items()):
        by_package.setdefault(package, []).append(leaf)
    for package, names in sorted(by_package.items()):
        missing = [n for n in names
                   if not os.path.exists(os.path.join(out, package, "Texture", n + ".png"))]
        if missing:
            args = UMODEL_CMD + [ "-export", "-png",
                    "-path=%s" % windows_path(project.install),
                    "-out=%s" % windows_path(out)]
            args += ["-obj=%s" % n for n in missing] + [package]
            subprocess.run(args, capture_output=True, text=True, timeout=1800)
        for leaf in names:
            png = os.path.join(out, package, "Texture", leaf + ".png")
            if os.path.exists(png):
                made[leaf] = png
            else:
                log("  WARNING: could not export detail texture %s from %s"
                    % (leaf, package))
    return made


def _stats(png):
    """(mean, std) per channel, via magick -> raw RGB."""
    import numpy as np
    out = subprocess.run(["magick", "identify", "-format", "%w %h", png],
                         capture_output=True, text=True, check=True).stdout.split()
    w, h = int(out[0]), int(out[1])
    raw = subprocess.run(["magick", png, "-depth", "8", "RGB:-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float32)
    return a.reshape(-1, 3).mean(0), a.reshape(-1, 3).std(0), (w, h)


def upscale(project, pngs, factor=4, model=None, models_dir=None,
            strength=1.0, log=print):
    """Upscale the detail textures, preserving their strength. {leaf: png}.

    Worth doing for the same reason the base textures are: `DetailScale` is a
    plain multiplier on the surface's normalised UVs, so it fixes the tile's
    world size and NOT the texel density inside it. A 4x detail texture
    therefore tiles exactly as before -- same grain size, same repeat -- with
    four times the texels describing it, which is the difference between grain
    made of single chunky texels and grain with shape. DetailScale must stay
    untouched; changing it is what would move the tiling.

    The catch is that an upscaler trained to denoise photographs treats pure
    grain as noise and smooths it, and a detail texture that loses its contrast
    loses the whole effect -- it is modulated as 2 x base x detail, so it does
    nothing at all once it flattens toward neutral 128. So each one is rescaled
    back to the mean and standard deviation it had before, per channel: the
    upscale buys resolution, never a change in strength.
    """
    import numpy as np
    from .upscale import one
    model = model or project.config["model"]
    models_dir = models_dir or project.config["models_dir"]
    scale = factor
    out = {}
    for leaf, png in sorted(pngs.items()):
        mean0, std0, (w, h) = _stats(png)
        rec = {"png": os.path.relpath(png, project.dir), "name": leaf + "_up",
               "width": w, "height": h, "scale": scale}
        note = one(project, rec, model, models_dir, log=log)
        big = os.path.join(project.up, leaf + "_up.png")
        mean1, std1, (w1, h1) = _stats(big)
        raw = subprocess.run(["magick", big, "-depth", "8", "RGB:-"],
                             capture_output=True, check=True).stdout
        a = np.frombuffer(raw, np.uint8).reshape(h1, w1, 3).astype(np.float32)
        # strength scales the restored contrast about the neutral mean. 1.0 is
        # the stock grain's own std; the detail layer modulates as
        # 2 x base x detail, so halving the std halves how far the surface is
        # pushed either side of neutral -- the same marks, less shouting.
        gain = np.where(std1 > 1e-3, std0 / np.maximum(std1, 1e-3), 1.0) * strength
        a = (a - mean1) * gain + mean0
        fixed = os.path.join(project.up, leaf + ".png")
        subprocess.run(["magick", "-size", "%dx%d" % (w1, h1), "-depth", "8",
                        "RGB:-", fixed], input=np.clip(a, 0, 255).astype(np.uint8).tobytes(),
                       check=True)
        os.remove(big)
        out[leaf] = fixed
        log("  %-12s %s  std %s -> %s, set to %s (x%.2f)"
            % (leaf, note,
               " ".join("%.1f" % v for v in std0),
               " ".join("%.1f" % v for v in std1),
               " ".join("%.1f" % (v * strength) for v in std0), strength))
    return out


def synthesize(project, pngs, factor=4, strength=1.0, log=print):
    """A larger tileable grain with the same character. {leaf: png}.

    The repeat, not the resolution, is what reads as wrong on an upscaled map.
    A detail tile's world size is set by DetailScale alone, so the stock grain
    repeats exactly as often as it always did -- what changed is that the
    smoothed 4x base no longer carries pixel noise of its own to camouflage it.

    Enlarging the tile (DetailScale / factor) fixes the repeat but needs more
    unique content to fill it, and an upscale has none to give: it interpolates
    the same 128x128 of information over four times the area, so the grain goes
    soft. This synthesises new content instead -- white noise shaped to the
    source's own radial power spectrum, measured in cycles per TEXEL and
    evaluated on the larger grid, so a texel of the result carries the same
    grain as a texel of the original once DetailScale has been divided to match.
    Phases are random, which is what makes it new; the amplitudes are the
    source's, which is what keeps it recognisable. Being built in the frequency
    domain it is periodic, so it tiles seamlessly by construction rather than by
    blending edges.
    """
    import numpy as np
    out = {}
    for leaf, png in sorted(pngs.items()):
        mean0, std0, (w, h) = _stats(png)
        raw = subprocess.run(["magick", png, "-depth", "8", "RGB:-"],
                             capture_output=True, check=True).stdout
        src = np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float32)
        H, W = h * factor, w * factor

        # Radial power spectrum of the source, in cycles per texel.
        fy, fx = np.meshgrid(np.fft.fftfreq(h), np.fft.fftfreq(w), indexing="ij")
        r_src = np.sqrt(fy ** 2 + fx ** 2).ravel()
        edges = np.linspace(0, r_src.max() + 1e-6, 96)
        idx = np.clip(np.digitize(r_src, edges) - 1, 0, len(edges) - 2)

        fy2, fx2 = np.meshgrid(np.fft.fftfreq(H), np.fft.fftfreq(W), indexing="ij")
        r_dst = np.sqrt(fy2 ** 2 + fx2 ** 2)
        centres = 0.5 * (edges[:-1] + edges[1:])

        # ONE noise field for all three channels, shaped by the luminance
        # spectrum and then scaled per channel. Giving each channel its own
        # field instead invents chroma noise that is not in the source: these
        # grains are essentially greyscale (R=G=B per texel), and independent
        # per-channel noise turns them into colour speckle -- visible as a tint
        # crawling over what should be neutral grain.
        # crc32, not hash(): Python randomises string hashing per process, so
        # hash() would invent different grain on every run and a rebuild would
        # silently not reproduce the texture that was judged by eye.
        rng = np.random.default_rng(zlib.crc32(leaf.encode()))
        lum = src.mean(axis=2)
        power = np.abs(np.fft.fft2(lum - lum.mean())) ** 2
        counts = np.maximum(np.bincount(idx, None, len(edges) - 1), 1)
        profile = np.bincount(idx, power.ravel(), len(edges) - 1) / counts
        amp = np.sqrt(np.interp(r_dst, centres, profile))
        plane = np.real(np.fft.ifft2(np.fft.fft2(rng.standard_normal((H, W))) * amp))
        sd = plane.std()
        plane = (plane - plane.mean()) / (sd if sd > 1e-6 else 1.0)
        a = np.clip(np.stack([plane * std0[c] * strength + mean0[c]
                              for c in range(3)], -1),
                    0, 255).astype(np.uint8)

        dst = os.path.join(project.up, leaf + ".png")
        subprocess.run(["magick", "-size", "%dx%d" % (W, H), "-depth", "8",
                        "RGB:-", dst], input=a.tobytes(), check=True)
        out[leaf] = dst
        f = a.astype(float)
        chroma_src = float(np.abs(src[:, :, 0] - src[:, :, 1]).mean())
        chroma_out = float(np.abs(f[:, :, 0] - f[:, :, 1]).mean())
        log("  %-12s %dx%d -> %dx%d, std %s kept, seam %.2f vs interior %.2f,"
            " chroma %.2f vs source %.2f"
            % (leaf, w, h, W, H, " ".join("%.1f" % v for v in std0),
               float(np.abs(f[0] - f[-1]).mean()),
               float(np.abs(np.diff(f, axis=0)).mean()),
               chroma_out, chroma_src))
    return out
