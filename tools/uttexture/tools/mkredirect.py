#!/usr/bin/env python3
"""Compress every shipped 4K file into Maps/redirect/ and Textures/redirect/.

`ucc compress` writes <file>.uz2 beside the source and ONLY accepts backslash
paths -- a forward-slash path fails with "Error occurred opening <basename>",
having silently discarded the directory. The reported percentage overflows a
signed 32-bit int above ~21MB and goes negative; that is not growth, so real
sizes are compared here instead.
"""
import glob, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uttexture.sweeney import install_root   # noqa: E402
INSTALL = install_root()
SYSTEM = os.path.join(INSTALL, "System")

# What ships: every rebuilt map and every shared 4K texture package. A map
# package embeds its own <Map>Tex, so those are build inputs and never upload.
# An explicit list of files on the command line overrides this, for refreshing
# just the few that changed.
if len(sys.argv) > 1:
    jobs = [(os.path.relpath(os.path.abspath(f), INSTALL),
             os.path.basename(os.path.dirname(os.path.abspath(f))))
            for f in sys.argv[1:]]
else:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from uttexture import ue2
    maps = sorted(glob.glob(os.path.join(INSTALL, "Maps", "*4K*.ut2")))
    # Transitive, not a glob over Textures/: a package can be reached only
    # through another shared package (DetailTextures4K is named by wrappers,
    # never by a map), while some built .utx no shipped map imports -- the
    # three shader packages -- must stay out of the redirect.
    want, seen = set(), set()
    queue = list(maps)
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        pkg = ue2.Package(path)
        for name in {e["name"] for e in pkg.imports
                     if e["class"] == "Package" and e["outer"] == 0}:
            utx = os.path.join(INSTALL, "Textures", name + ".utx")
            if name.endswith("4K") and os.path.exists(utx) and utx not in want:
                want.add(utx)
                queue.append(utx)
    jobs = [(os.path.relpath(f, INSTALL), "Maps") for f in maps]
    jobs += [(os.path.relpath(f, INSTALL), "Textures") for f in sorted(want)]

for folder in ("Maps", "Textures"):
    os.makedirs(os.path.join(INSTALL, folder, "redirect"), exist_ok=True)

done = skipped = failed = 0
src_total = uz_total = 0
start = time.time()
for rel, folder in jobs:
    src = os.path.join(INSTALL, rel)
    dest = os.path.join(INSTALL, folder, "redirect", os.path.basename(rel) + ".uz2")
    if os.path.exists(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
        skipped += 1
        src_total += os.path.getsize(src); uz_total += os.path.getsize(dest)
        continue
    win = "Z:" + src.replace("/", "\\")
    # NOT capture_output: wineserver inherits the pipe and keeps it open after
    # UCC.exe exits, so subprocess.run blocks forever on a process already shown
    # as <defunct>. Discarding the output avoids the pipe entirely.
    made = src + ".uz2"
    if os.path.exists(made):
        os.remove(made)                      # stale from an interrupted run
    with open(os.devnull, "wb") as null:
        try:
            subprocess.run(["./UCC.exe", "compress", win], cwd=SYSTEM,
                           stdout=null, stderr=null, timeout=1800)
        except subprocess.TimeoutExpired:
            print("  TIMEOUT %s" % rel, flush=True)
    if os.path.exists(made):
        os.replace(made, dest)
        done += 1
        src_total += os.path.getsize(src); uz_total += os.path.getsize(dest)
    else:
        failed += 1
        print("  FAILED %s" % rel, flush=True)
    if (done + skipped) % 20 == 0:
        print("  ... %d/%d  (%.0fs)" % (done + skipped, len(jobs), time.time() - start), flush=True)

print("compressed %d, reused %d, failed %d" % (done, skipped, failed))
print("source %.2f GB -> uz2 %.2f GB  (%.0f%%)"
      % (src_total / 1e9, uz_total / 1e9, 100.0 * uz_total / max(src_total, 1)))
