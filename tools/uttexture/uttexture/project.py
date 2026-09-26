"""Per-map working directory and its settings.

Everything a map needs lives under maps/<MapName>/ so several maps can be in
flight without colliding, and the shared expensive things -- upscaler weights,
material scans, the ROCm shim -- sit once at the project root.

The package name follows ut3converter's convention: the map name stripped of
punctuation with "Tex" appended, so DM-1on1-Roughinery gives DM1on1RoughineryTex.
That is also the name the .uc class takes, which is why it has to be a legal
UnrealScript identifier.

config.json's "map_name" is the name the rebuild ships under, defaulting to
<Map>4K. Shipping under the *source* map's own name (what Roughinery did) is the
special case, not the norm: references to assets embedded in the map package --
static meshes, the level shot -- are written as <MapPkg>.<Name>, so they only
resolve to the new map when the two names agree. Under any other name they
silently resolve against the original map instead, so `renamed` makes t3d relink
them to MyLevel and report what has to be imported there.
"""

import json, os, re

from .sweeney import install_root, work_root

# Bundled with Sweeney the code sits outside the install, so ROOT is the WORK
# directory -- maps/, models/, scans/ -- not the directory the code lives in.
# Per-map data runs to gigabytes and must never land in Sweeney's checkout.
ROOT = work_root()
MODELS = os.path.join(ROOT, "models")
SCANS = os.path.join(ROOT, "scans")
ROCM_STUB = os.path.join(ROOT, ".rocmlibs", "stub")

DEFAULTS = {
    "install": None,        # filled from ~/.sweeney/config.json; see sweeney.py
    "scale": 4,
    "model": "digital-art-4x",
    "models_dir": "/usr/lib/upscayl/models",
    "sharpen": "",
    "inject": {},
    "sky": None,
    "skip_upscale": [],
    "keep_unused": False,
    "include": [],
    "map_name": None,
    "level_shot": None,
    "detail_mode": "synth",  # none | copy | upscale | synth
    "detail_factor": None,   # detail enlargement; defaults to "scale"
    "detail_model": None,    # model for detail_mode=upscale; defaults to "model"
    "detail_exclude": [],    # textures that get no Detail (SurfaceType still applies)
    "detail_strength": 1.0,  # contrast of the detail layer; 1.0 = the stock grain
    "carry_meshes": True,    # a rebuild that references the source map breaks online; see mesh.py
    "align_planes": False,   # collapse near-coplanar faces so the CSG rebuild is sane
    "drop_terrain": False,   # a .t3d cannot carry terrain; dropping it lets the map open
    "detail_models_dir": None,
    # Run the model at source size: upscale by restyle_factor, then resize back.
    # Only meaningful at scale 1 -- above that the model output IS the result.
    "restyle": False,
    "restyle_factor": 4,
    "model_overrides": {},  # texture name -> model, for ones "model" smears
}



# Maps the engine patches at load time by NAME, in UGameEngine::FixUpLevel
# (Engine/Src/UnGame.cpp). The fixes are per-map hacks Epic shipped for retail
# content -- a KillZ, collision turned off on named StaticMeshActors -- and they
# are keyed on the level's full name, so a rebuild shipping under any other name
# silently loses them. Lower-cased, as the comparison is case-insensitive.
ENGINE_PATCHED_MAPS = {
    "as-junkyard", "br-anubis", "br-de-elecfields", "br-disclosure",
    "br-icefields", "br-skyline", "br-twintombs", "ctf-chrome", "ctf-citadel",
    "ctf-december", "ctf-de-elecfields", "ctf-doubledammage", "ctf-face3",
    "ctf-geothermal", "ctf-lostfaith", "ctf-maul", "ctf-twintombs",
    "dm-antalus", "dm-asbestos", "dm-curse3", "dm-de-grendelkeep", "dm-gael",
    "dm-insidious", "dm-leviathan", "dm-oceanic", "dm-phobos2", "dm-plunge",
    "dm-serpentine", "dm-tokaraforest", "dm-trainingday", "dom-core",
    "dom-junkyard", "dom-ruination", "dom-suntemple",
}


def engine_patch_warning(source_map, out_map):
    """Text to print when a rebuild renames a map the engine patches by name."""
    if source_map.lower() not in ENGINE_PATCHED_MAPS:
        return None
    if source_map.lower() == out_map.lower():
        return None
    return ("WARNING: the engine patches %s at load time by name, and this"
            " rebuild ships as %s, so those fixes will not apply. They are"
            " per-map hacks -- a KillZ, collision disabled on named"
            " StaticMeshActors -- so expect the faults they were written to"
            " cover. Keep the original name, or reproduce the fix in the map."
            % (source_map, out_map))

def package_name(map_name):
    stem = re.sub(r"[^A-Za-z0-9]", "", map_name)
    if stem and stem[0].isdigit():
        stem = "M" + stem
    return stem + "Tex"


class Project:
    def __init__(self, map_name):
        self.map = map_name
        self.dir = os.path.join(ROOT, "maps", map_name)
        self.meta = os.path.join(self.dir, "meta")
        self.raw = os.path.join(self.dir, "raw")
        self.up = os.path.join(self.dir, "up")
        self.work = os.path.join(self.dir, ".work")
        self.out = os.path.join(self.dir, "out")
        for d in (self.meta, self.raw, self.up, self.work, self.out):
            os.makedirs(d, exist_ok=True)
        self.config = dict(DEFAULTS)
        path = os.path.join(self.dir, "config.json")
        if os.path.exists(path):
            self.config.update(json.load(open(path)))
        self.package = self.config.get("package") or package_name(map_name)
        self.out_map = self.config.get("map_name") or (map_name + "4K")

    @property
    def renamed(self):
        """Does the rebuild ship under a different name than the source map?"""
        return self.out_map != self.map

    def save_config(self):
        cfg = {k: v for k, v in self.config.items() if DEFAULTS.get(k) != v}
        cfg["package"] = self.package
        json.dump(cfg, open(os.path.join(self.dir, "config.json"), "w"), indent=2)

    @property
    def install(self):
        return self.config["install"] or install_root()

    def map_file(self):
        for ext in (".ut2", ".unr"):
            p = os.path.join(self.install, "Maps", self.map + ext)
            if os.path.exists(p):
                return p
        raise SystemExit("no map %s in %s/Maps" % (self.map, self.install))

    def load(self, name):
        p = os.path.join(self.meta, name)
        return json.load(open(p)) if os.path.exists(p) else None

    def store(self, name, data):
        json.dump(data, open(os.path.join(self.meta, name), "w"), indent=2)
