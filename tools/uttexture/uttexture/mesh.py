"""Bring a map's own static meshes and sounds into the rebuilt package.

Assets embedded in the map package are referenced as <MapPkg>.<Name>, so under
a new name they have to travel with the rebuild: a .t3d carries references,
never assets. Leaving them pointing at the source map is not an option online.
UPackageMap::AddLinker recurses the map's import table, so the source map joins
the net package map and every client is told it needs it -- matched BY GUID. A
client without that exact build downloads it, or, if the server forbids
downloads or the redirect lacks it, is dropped with DownloadNotAllowed. For a
stock map that is merely wasteful; for a community map it is a broken server.

Meshes no longer go through this module. They are copied out of the source map
export by export by carry.py, after the package compiles. The route before that
was an ASE round trip -- geometry and smoothing from `UCC.exe batchexport ...
T3D`, material slot index per face from umodel's .pskx -- and it worked for
rendering but not for collision. An ASE carries triangles, so a collision hull
built from quads came back re-tessellated; a hull whose edges are not each
shared by two faces has no defined inside, and re-tessellating one moves which
side the engine calls solid. DM-1on1-Roughinery got four invisible walls out of
a single such mesh. Copying the bytes cannot change the geometry, so that is
what the pipeline does now, and the ASE writer is gone rather than left as a
fallback nobody would notice taking over.

Sounds still come through umodel, and package_slots below reads the material
slot list of a mesh in ANOTHER package, which is what lets t3d give a retail
mesh actor a Skins() override pointing at the rebuilt texture.
"""

import os, re, struct, subprocess, sys


AUDIO_EXEC = "#exec AUDIO IMPORT FILE=Sounds\\%s NAME=%s"


# ---------------------------------------------------------------- exporting

def export_umodel(project, log=print):
    """umodel the map's static meshes and sounds out. Returns (meshes, sounds).

    meshes is {leaf: (pskx, props)}, sounds is {leaf: wav}.
    """
    from .survey import UMODEL, UMODEL_CMD, windows_path
    out = os.path.join(project.work, "umodel")
    marker = os.path.join(out, ".complete")
    if not os.path.exists(marker):
        os.makedirs(out, exist_ok=True)
        subprocess.run(UMODEL_CMD + [ "-export", "-nomesh", "-noanim", "-novert",
                        "-sounds",
                        "-path=%s" % windows_path(project.install),
                        "-out=%s" % windows_path(out), project.map],
                       capture_output=True, text=True, timeout=1800)
        open(marker, "w").close()
    meshes, sounds = {}, {}
    root = os.path.join(out, project.map)
    mesh_dir = os.path.join(root, "StaticMesh")
    if os.path.isdir(mesh_dir):
        for f in os.listdir(mesh_dir):
            if f.endswith(".pskx") or f.endswith(".psk"):
                leaf = f.rsplit(".", 1)[0]
                props = os.path.join(mesh_dir, leaf + ".props.txt")
                meshes[leaf] = (os.path.join(mesh_dir, f),
                                props if os.path.exists(props) else None)
    sound_dir = os.path.join(root, "Sound")
    if os.path.isdir(sound_dir):
        for f in os.listdir(sound_dir):
            if f.lower().endswith(".wav"):
                sounds[f[:-4]] = os.path.join(sound_dir, f)
    return meshes, sounds


def build_sounds(project, pkg_dir, entries, log=print):
    """Copy the map's embedded sounds in. entries is [(group, name)]."""
    import shutil
    _meshes, wavs = export_umodel(project, log=log)
    out = os.path.join(pkg_dir, "Sounds")
    os.makedirs(out, exist_ok=True)
    made = []
    for group, name in entries:
        src = wavs.get(name)
        if not src:
            log("  WARNING: sound %s not exported, left on the source map" % name)
            continue
        shutil.copyfile(src, os.path.join(out, name + ".wav"))
        made.append((group, name))
    return made


# ------------------------------------------------- meshes in other packages

def package_slots(path, only=None):
    """{mesh name: {slot index: (full material path, class)}} from a package.

    A static mesh in a RETAIL package binds its own skins, so a map drawing one
    shows the original textures however much of the map is rebuilt -- and that
    is most mesh actors on most maps (DM-DE-Ironic has 935, of which 2 are its
    own). Giving those actors a Skins() override pointing at the 4K texture is
    what brings them up, and it needs the mesh's slot list.

    Read from the package rather than exported: UStaticMesh.Materials is an
    ordinary tagged array of FStaticMeshMaterial (EnableCollision, Material),
    so no umodel run and no disk are needed for what is only a name per slot.
    """
    from . import ue2
    pkg = ue2.Package(path)
    out = {}
    for export in pkg.exports:
        if pkg.class_of(export) != "StaticMesh":
            continue
        if only is not None and export["name"] not in only:
            continue
        try:
            props = pkg.properties(export)
        except Exception:
            continue
        if "Materials" not in props:
            continue
        r = ue2.Reader(props["Materials"][0].raw)
        slots = {}
        try:
            for i in range(r.index()):
                entry = ue2._read_properties(pkg, r)
                ref = entry.get("Material")
                if not ref:
                    continue
                idx = ue2.Reader(ref[0].raw).index()
                full = pkg.ref_path(idx)
                if full:
                    cls = (pkg.imports[-idx - 1]["class"] if idx < 0
                           else pkg.class_of(pkg.exports[idx - 1]))
                    slots[str(i)] = (full, cls)
        except Exception:
            continue
        if slots:
            out[export["name"]] = slots
    return out


_PKG_INDEX = {}


def _find_package(install, name):
    """Path of a content package, matched without regard to case."""
    if not _PKG_INDEX:
        for folder in ("StaticMeshes", "Textures", "Animations", "Maps", "Sounds"):
            d = os.path.join(install, folder)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                stem, _dot, ext = f.rpartition(".")
                if stem and ext.lower() in ("usx", "utx", "ukx", "ut2", "uax"):
                    _PKG_INDEX.setdefault(stem.lower(), os.path.join(d, f))
    return _PKG_INDEX.get(name.lower())


def external_slots(install, map_name, t3d_path, log=print):
    """{mesh leaf: {slot: material leaf}} for meshes in OTHER packages."""
    import collections
    want = collections.defaultdict(set)
    pattern = re.compile(r"StaticMesh=StaticMesh'([\w\-]+)\.(?:[\w\-]+\.)*([\w\-]+)'")
    for line in open(t3d_path, encoding="latin-1"):
        m = pattern.search(line)
        if m and m.group(1).lower() != map_name.lower():
            want[m.group(1)].add(m.group(2))
    out, refs = {}, {}
    for package, names in sorted(want.items()):
        # Case-insensitively: UE package names are case-insensitive and the
        # maps do not match the filenames -- 2k4ChargerMeshes on disk is
        # 2K4chargerMESHES.usx.
        path = _find_package(install, package)
        if not path:
            log("  external meshes: %s not found on disk" % package)
            continue
        found = package_slots(path, only=names)
        for mesh_name, slots in found.items():
            out[mesh_name] = {}
            for slot, (full, cls) in slots.items():
                leaf = full.rsplit(".", 1)[-1]
                out[mesh_name][slot] = leaf
                # The map never imports these -- the MESH package does -- so
                # the survey cannot see them unless they are handed over with
                # their full path and class.
                refs.setdefault(leaf, (full, cls))
        if len(found) < len(names):
            log("  %s: %d of %d meshes had no material list"
                % (package, len(names) - len(found), len(names)))
    return out, refs
