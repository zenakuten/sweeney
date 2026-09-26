"""Pull the textures a map uses out of the packages they live in.

umodel rather than UCC: UCC's exporters are split by format -- DDS handles only
DXT, BMP only P8/RGBA8/G16 -- and `batchexport` calls appErrorf, which is fatal,
on the first texture the chosen exporter cannot handle. No single run can export
a mixed package. umodel decodes every UE2 texture format and takes -obj filters,
so one invocation per source package does the whole job.
"""

import os, re, subprocess, collections

from .survey import UMODEL, UMODEL_CMD, windows_path


def source_packages(textures):
    """{package: [leaf names]} from the resolved paths in a survey."""
    out = collections.defaultdict(list)
    for leaf, entry in textures.items():
        out[entry["path"].split(".")[0]].append(leaf)
    return out


def export(project, textures, log=print):
    """Export every texture to raw/<Package>/Texture/<Name>.png."""
    root = project.install
    done = 0
    for package, names in sorted(source_packages(textures).items()):
        missing = [n for n in names
                   if not os.path.exists(os.path.join(
                       project.raw, package, "Texture", n + ".png"))]
        if not missing:
            done += len(names)
            continue
        args = UMODEL_CMD + [ "-export", "-png",
                "-path=%s" % windows_path(root),
                "-out=%s" % windows_path(project.raw)]
        args += ["-obj=%s" % n for n in missing]
        args.append(package)
        r = subprocess.run(args, capture_output=True, text=True, timeout=1800)
        exported = [l for l in r.stdout.splitlines() if l.startswith("Exporting Texture")]
        log("  %-26s %d/%d" % (package, len(exported), len(missing)))
        done += len(names)
    return done


def find_png(project, leaf):
    for package in sorted(os.listdir(project.raw)):
        p = os.path.join(project.raw, package, "Texture", leaf + ".png")
        if os.path.exists(p):
            return p
    return None


def probe(path):
    """(width, height, has alpha, mean alpha) via ImageMagick."""
    out = subprocess.run(["magick", "identify", "-format", "%w %h %[channels]", path],
                         capture_output=True, text=True, check=True).stdout.split()
    w, h, channels = int(out[0]), int(out[1]), out[2]
    alpha = "rgba" in channels or "graya" in channels
    mean = 1.0
    if alpha:
        mean = float(subprocess.run(
            ["magick", path, "-alpha", "extract", "-format", "%[fx:mean]", "info:"],
            capture_output=True, text=True, check=True).stdout.strip())
    return w, h, alpha, mean
