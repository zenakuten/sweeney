"""Rebuild a map's own Shader materials so its surfaces can be repointed.

Some maps put almost nothing directly on a surface. DM-1on1-Aerowalk draws 1186
of its 1325 BSP surfaces through Shaders it defines itself -- wall1shader,
trim5shader and friends -- with only 139 on plain textures. Upscaling only what
the survey sees drawn would rebuild a tenth of that map and still leave every
surface pointing at the original .ut2, which for a community map is a real
dependency rather than a formality.

The shaders themselves are mechanical:

    Shader wall1shader
        Diffuse         = Texture'Walls.eX_wall_01_d'
        SpecularityMask = Texture'Walls.eX_wall_01_s'
        Specular        = TexEnvMap'Kretzig.KretzigTexEnvMap'
        Detail          = Texture'DetailTextures.detail46'
        DetailScale     = 8
        SurfaceType     = 3

Every slot is either a plain texture -- which this pipeline can upscale -- or a
reference into another package, which is left exactly as it is. So the shader is
re-emitted into <Map>Tex with its texture slots repointed at the upscaled copies
and everything else carried across verbatim, and t3d sends the surfaces there.

Only classes in REBUILDABLE are attempted. A Combiner or a MaterialSequence
nests other materials and would need the whole graph walked; those are still
reported and left alone.
"""

import os, re

# Per-class property sets, each verified against THIS engine's class:
# Engine/Classes/{Shader,FinalBlend,Combiner,Modifier,Material}.uc. Reading a
# composite back and reproducing it is not the same job as CONSTRUCTING one
# (which MaterialSet can do for any class) -- here the properties have to match
# what the original actually carried, so a class is only listed once its real
# property set has been checked. Anything not listed is left where it is.
# Modifier.Material is the one material slot the whole TexModifier family has;
# TexModifier itself adds these three.
_MOD = ("Material", "FallbackMaterial")
_TEXMOD = ("TexCoordSource", "TexCoordCount", "TexCoordProjected", "SurfaceType")

CLASS_SLOTS = {
    "Shader": None,             # filled in below from MATERIAL_SLOTS/SCALAR_SLOTS
    "FinalBlend": (("Material", "FallbackMaterial"),
                   ("FrameBufferBlending", "ZWrite", "ZTest", "AlphaTest",
                    "TwoSided", "AlphaRef", "SurfaceType")),
    "Combiner": (("Material1", "Material2", "Mask", "FallbackMaterial"),
                 ("CombineOperation", "AlphaOperation", "InvertMask",
                  "Modulate2X", "Modulate4X", "SurfaceType")),
    # The TexModifier family: each wraps ONE material (Modifier.Material) and
    # adds its own scalars. They all share TexModifier's three coordinate
    # fields. Engine/Classes/Tex*.uc.
    "TexScaler": (_MOD, _TEXMOD + ("UScale", "VScale", "UOffset", "VOffset")),
    "TexPanner": (_MOD, _TEXMOD + ("PanDirection", "PanRate")),
    "TexRotator": (_MOD, _TEXMOD + ("TexRotationType", "Rotation", "UOffset",
                                    "VOffset", "OscillationRate",
                                    "OscillationAmplitude", "OscillationPhase")),
    "TexOscillator": (_MOD, _TEXMOD + (
        "UOscillationRate", "VOscillationRate", "UOscillationPhase",
        "VOscillationPhase", "UOscillationAmplitude", "VOscillationAmplitude",
        "UOscillationType", "VOscillationType")),
    "TexCoordSource": (_MOD, _TEXMOD + ("SourceChannel",)),
    "TexEnvMap": (_MOD, _TEXMOD + ("EnvMapType",)),
    # Engine/Classes/Cubemap.uc -- six face textures. A TexEnvMap usually names
    # one, so it has to travel too or the env map keeps the source map alive.
    "Cubemap": (tuple("Faces[%d]" % i for i in range(6)), ()),
}
REBUILDABLE = set(CLASS_SLOTS)

# Slots that name a material. Rebuilt when they point at a plain texture in this
# map, carried across untouched when they point anywhere else.
# Verified against Engine/Shader.uc and Engine/Material.uc for THIS engine.
# umodel dumps against its own class model, which carries fields a UT2004
# Shader does not have (NormalMap, TreatAsTwoSided, AlphaTest, AlphaRef,
# ZWrite, and the *Mask structs). Emitting those makes UCC warn "Unknown
# property in defaults" once per shader and drop them -- harmless, but it
# buries any real warning in noise, so only real properties are written.
MATERIAL_SLOTS = ("Diffuse", "Opacity", "Specular", "SpecularityMask",
                  "SelfIllumination", "SelfIlluminationMask", "Detail",
                  "FallbackMaterial")

# Scalar/bool/enum properties worth preserving. Enum values arrive from the dump
# as "OB_Normal (0)" and go back as the NAME: UCC silently drops an integer
# assigned to an enum in defaultproperties.
SCALAR_SLOTS = ("DetailScale", "OutputBlending", "TwoSided", "Wireframe",
                "ModulateStaticLighting2X", "PerformLightingOnSpecularPass",
                "ModulateSpecular2X", "SurfaceType")

CLASS_SLOTS["Shader"] = (MATERIAL_SLOTS, SCALAR_SLOTS)

OBJ_REF = re.compile(r"^(\w+)'([\w\-.]+)'$")


def parse_dump(path):
    """{object: (class, {property: raw value})} from umodel -dump."""
    out, cur = {}, None
    for line in open(path, encoding="latin-1", errors="ignore"):
        m = re.match(r"ClassName: (\S+) ObjectName: (\S+)", line.strip())
        if m:
            cur = {}
            out[m.group(2)] = (m.group(1), cur)
            continue
        if cur is None:
            continue
        # Name, or Name[i] for a static array: a Cubemap's six Faces are
        # written `Faces[0] = ...` and a plain \w+ stops at the bracket, so the
        # whole array was invisible.
        m = re.match(r"\s+(\w+(?:\[\d+\])?) = (.+?)\s*$", line)
        if m:
            cur[m.group(1)] = m.group(2)
    return out


def reference_map(project, extra=None):
    """{leaf name: fully qualified path} for everything the map can name.

    umodel dumps an object reference group-qualified but package-less --
    `TexEnvMap'Core.DomCoreTexEnv'` for what is really
    `CubeMaps.Core.DomCoreTexEnv`. Written out verbatim that resolves against a
    package called Core, and `ucc make` stops with "unresolved reference". The
    map's own import table has the real path, so every reference is re-resolved
    through it before being written.

    DM-1on1-Aerowalk only built because its one such reference,
    `Kretzig.KretzigTexEnvMap`, happens to start with its package name.
    """
    from .ue2 import Package
    import glob
    out = {}
    paths = [project.map_file()]
    for name in sorted(set(extra or ())):
        for ext in (".utx", ".usx", ".uax"):
            hit = os.path.join(project.install, {"utx": "Textures", "usx":
                               "StaticMeshes", "uax": "Sounds"}[ext[1:]],
                               name + ext)
            if os.path.exists(hit):
                paths.append(hit)
                break
    # A generic name like TexEnvMap1 exists in several packages, and only a
    # PUBLIC object can be referenced from another package: UE2 refuses to save
    # with "Graph is linked to external private object" and the build dies.
    # X_AW-Shaders.shaders.TexEnvMap1 is private (flags 0x70000) while
    # cp_Mech1.Skins.TexEnvMap1 is public (0x70004), so which package happens to
    # be scanned first decided whether the package built. Imports rank highest:
    # they are references the map itself already makes, so they must resolve.
    RF_PUBLIC = 0x0004
    # Only things a material slot can actually hold. Matching on leaf name
    # alone crosses classes: cp_Mechstaticpack1 has a STATIC MESH called
    # sg_Mechwallchunk, and a Shader's Diffuse resolved to it, giving
    # `Texture'cp_Mechstaticpack1.Decos.sg_Mechwallchunk'` -- a correct path to
    # the wrong kind of object, which UCC reports as unresolved.
    from .survey import COMPOSITE
    MATERIAL = COMPOSITE | {"Texture", "Material", "Palette"}
    rank = {}

    def offer(name, path, quality):
        if quality > rank.get(name, -1):
            rank[name] = quality
            out[name] = path

    for path in paths:
        try:
            pkg = Package(path)
        except Exception:
            continue
        for i, imp in enumerate(pkg.imports):
            if imp["class"] in MATERIAL:
                offer(imp["name"], pkg.import_path(-(i + 1)), 2)
        for i, exp in enumerate(pkg.exports):
            if exp["flags"] & RF_PUBLIC and pkg.class_of(exp) in MATERIAL:
                offer(exp["name"], "%s.%s" % (pkg.name, pkg.export_path(i)), 1)
    return out


def scan(project, entries, log=print):
    """{name: (class, props)} for composites, wherever they live.

    `entries` maps leaf name to its full path, because a composite the map draws
    through is as often in a content package as in the map itself -- and both
    are worth rebuilding. Rebuilding an external one does not touch that
    package: it copies the shader into <Map>Tex over upscaled textures, and this
    map stops using the original.
    """
    if not entries:
        return {}
    from .survey import umodel_dump
    by_pkg = {}
    for leaf, path in entries.items():
        by_pkg.setdefault(path.split(".")[0], []).append(leaf)
    defs = {}
    for pkg, leaves in sorted(by_pkg.items()):
        dump = umodel_dump(project.install, pkg, sorted(leaves),
                           os.path.join(project.meta, "composites_%s.txt" % pkg))
        defs.update(parse_dump(dump))
    return defs


def textures_of(defs):
    """Leaf names of every plain texture the rebuildable composites reference."""
    return set(texture_refs(defs))


def texture_refs(defs):
    """{leaf: reference as dumped} for the textures rebuildable composites use.

    A composite living in a content package reaches textures the MAP never names
    itself, so these are invisible to a survey that only walks the map's own
    name table -- and they are exactly the textures that have to be upscaled for
    the rebuilt shader to be worth anything.
    """
    out = {}
    for name, (cls, props) in defs.items():
        if cls not in REBUILDABLE:
            continue
        # This class's OWN material slots: a Cubemap keeps its textures in
        # Faces[0..5], a modifier in Material, a Combiner in Material1/2/Mask.
        # Walking the Shader slot list found none of them, so their textures
        # were never marked used and never upscaled.
        for slot in CLASS_SLOTS[cls][0]:
            m = OBJ_REF.match(props.get(slot, "None"))
            if m and m.group(1) == "Texture":
                out.setdefault(m.group(2).rsplit(".", 1)[-1], m.group(2))
    return out


# Engine/Material.uc:19. Needed because the dump prints SurfaceType as a bare
# integer where it spells OutputBlending out, and an integer assigned to an enum
# in defaultproperties is discarded by UCC in silence -- the surface would keep
# EST_Default and the wrong footstep sounds.
SURFACE_TYPES = [
    "EST_Default", "EST_Rock", "EST_Dirt", "EST_Metal", "EST_Wood", "EST_Plant",
    "EST_Flesh", "EST_Ice", "EST_Snow", "EST_Water", "EST_Glass",
] + ["EST_Custom%02d" % i for i in range(18)]


# An integer assigned to an enum in defaultproperties is discarded by UCC in
# silence, so every enum slot needs its value spelled out.
ENUMS = {
    "FrameBufferBlending": [
        "FB_Overwrite", "FB_Modulate", "FB_AlphaBlend",
        "FB_AlphaModulate_MightNotFogCorrectly", "FB_Translucent",
        "FB_Darken", "FB_Brighten", "FB_Invisible"],
    "CombineOperation": [
        "CO_Use_Color_From_Material1", "CO_Use_Color_From_Material2",
        "CO_Multiply", "CO_Add", "CO_Subtract", "CO_AlphaBlend_With_Mask",
        "CO_Add_With_Mask_Modulation", "CO_Use_Color_From_Mask"],
    "AlphaOperation": [
        "AO_Use_Mask", "AO_Multiply", "AO_Add",
        "AO_Use_Alpha_From_Material1", "AO_Use_Alpha_From_Material2"],
    "SurfaceType": SURFACE_TYPES,
    "TexCoordSource": [
        "TCS_Stream0", "TCS_Stream1", "TCS_Stream2", "TCS_Stream3",
        "TCS_Stream4", "TCS_Stream5", "TCS_Stream6", "TCS_Stream7",
        "TCS_WorldCoords", "TCS_CameraCoords", "TCS_WorldEnvMapCoords",
        "TCS_CameraEnvMapCoords", "TCS_ProjectorCoords", "TCS_NoChange"],
    "TexCoordCount": ["TCN_2DCoords", "TCN_3DCoords", "TCN_4DCoords"],
    "TexRotationType": ["TR_FixedRotation", "TR_ConstantlyRotating",
                        "TR_OscillatingRotation"],
    "UOscillationType": ["OT_Pan", "OT_Stretch", "OT_StretchRepeat", "OT_Jitter"],
    "VOscillationType": ["OT_Pan", "OT_Stretch", "OT_StretchRepeat", "OT_Jitter"],
    "EnvMapType": ["EM_WorldSpace", "EM_CameraSpace"],
}


def enum_name(slot, value):
    """"OB_Normal (0)" -> "OB_Normal"; SurfaceType 3 -> EST_Metal."""
    value = value.strip()
    m = re.match(r"([A-Za-z_]\w*)\s*\(\d+\)$", value)
    if m:
        return m.group(1)
    table = ENUMS.get(slot)
    if table and value.isdigit() and int(value) < len(table):
        return table[int(value)]
    return value


_OBJECT_CACHE = {}


def _object_exists(path):
    """Does `Package.Group.Name` name an object that really exists?

    Two ways a raw reference out of a umodel dump is a lie, and both stop the
    compile dead:
      * the package does not exist -- umodel writes a placeholder for a
        reference whose real package it could not name
        (`TexScaler'Package0.BannerSymbolPlacement'`);
      * the package exists but the object does not -- `cp_Mechstaticpack1` is a
        real package and `Decos.sg_Mechwallchunk` is not in it.
    So the package is opened and its name table checked. Results are cached;
    a package is read at most once.
    """
    from .mesh import _find_package
    from .ue2 import Package
    install = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    package, _, rest = path.partition(".")
    if not rest:
        return False
    if package not in _OBJECT_CACHE:
        found = _find_package(install, package)
        names = set()
        if found:
            try:
                pkg = Package(found)
                # EXPORT names, not the name table: a package's name table also
                # holds every name it merely REFERENCES, so cp_Mechstaticpack1
                # lists sg_Mechwallchunk without containing it.
                names = {e["name"] for e in pkg.exports}
            except Exception:
                names = set()
        _OBJECT_CACHE[package] = names
    names = _OBJECT_CACHE[package]
    return bool(names) and rest.rsplit(".", 1)[-1] in names


def primary_slot(cls):
    """The slot holding the material a composite of this class draws through."""
    spec = CLASS_SLOTS.get(cls)
    return spec[0][0] if spec else "Diffuse"


def _emit_order(defs):
    """Composite names ordered so a dependency is emitted before its dependent."""
    from .survey import COMPOSITE
    deps = {}
    for name, (_cls, props) in defs.items():
        need = set()
        for raw in props.values():
            m = OBJ_REF.match((raw or "").strip())
            if m and m.group(1) in COMPOSITE:
                leaf = m.group(2).rsplit(".", 1)[-1]
                if leaf in defs and leaf != name:
                    need.add(leaf)
        deps[name] = need
    order, done = [], set()

    def visit(n, stack):
        if n in done or n in stack:       # a cycle just emits in found order
            return
        stack.add(n)
        for d in sorted(deps.get(n, ())):
            visit(d, stack)
        stack.discard(n)
        done.add(n)
        order.append(n)

    for n in sorted(defs):
        visit(n, set())
    return order


def closure(project, entries, log=print):
    """Expand a composite set to everything it reaches, transitively.

    A composite's slot can name ANOTHER composite, and the survey never sees
    that one -- nothing draws it directly. Rebuilding only what the survey found
    leaves those references pointing at the package they came from, which for a
    map-owned composite means the rebuilt package still depends on the SOURCE
    MAP: DM-Structured needed `FinalBlend'DM-Structured.Misc.advskynew'` and
    `TexEnvMap'DM-Structured.Decos.EnvmapOrangeDark'` this way, invisibly, since
    neither appears anywhere in the .t3d.

    umodel writes these references package-less, so one that does not resolve
    as-is is assumed to live in the same package as the composite naming it.
    """
    from .survey import COMPOSITE
    seen = dict(entries)
    for _ in range(8):                       # depth guard; 2 is typical
        defs = scan(project, seen, log=lambda *a: None)
        added = {}
        for name, (_cls, props) in defs.items():
            owner = seen.get(name, "").split(".")[0]
            for raw in props.values():
                m = OBJ_REF.match((raw or "").strip())
                if not m or m.group(1) not in COMPOSITE:
                    continue
                path, leaf = m.group(2), m.group(2).rsplit(".", 1)[-1]
                if leaf in seen or leaf in added:
                    continue
                full = path if _object_exists(path) else "%s.%s" % (owner, path)
                if _object_exists(full):
                    added[leaf] = full
        if not added:
            break
        log("  %d composite(s) reachable only through another: %s"
            % (len(added), ", ".join(sorted(added)[:6])))
        seen.update(added)
    return seen


def rebuild(ms, package, defs, repoint, refs=None, log=print):
    """Emit each rebuildable composite. Returns {original name: (class, new name)}.

    `repoint` maps a texture leaf to the path it now lives at, so a slot that
    named a texture this package upscaled follows it; anything else -- another
    package's envmap, a texture that was not upscaled -- is written unchanged.
    """
    made, skipped = {}, []
    for name in _emit_order(defs):
        cls, props = defs[name]
        if cls not in REBUILDABLE:
            skipped.append("%s'%s'" % (cls, name))
            continue
        material_slots, scalar_slots = CLASS_SLOTS[cls]
        out = []
        for slot in material_slots:
            raw = props.get(slot, "None")
            if raw == "None":
                continue
            m = OBJ_REF.match(raw)
            if not m:
                continue
            kind, path = m.group(1), m.group(2)
            leaf = path.rsplit(".", 1)[-1]
            if leaf in made:
                # Another composite in THIS package, already emitted: point at
                # the rebuilt copy, not the original -- otherwise the package
                # keeps a reference to wherever it came from, which for a
                # map-owned composite is the source map.
                out.append((slot, "%s'%s.%s'" % (made[leaf][0], package,
                                                 made[leaf][1])))
            elif kind == "Texture" and leaf in repoint:
                out.append((slot, "Texture'%s'" % repoint[leaf]))
            elif refs and leaf in refs:
                # Anything else keeps living where it is, but has to be named
                # in full or the compile cannot resolve it.
                out.append((slot, "%s'%s'" % (kind, refs[leaf])))
            elif "." in path and _object_exists(path):
                out.append((slot, raw))
            else:
                # Package-less and unresolvable: writing it verbatim either
                # fails the compile or, worse, binds a private object and fails
                # at SAVE. A missing Specular or Detail is cosmetic; a package
                # that will not build is not.
                log("  %s: dropped %s=%s, cannot resolve it to a public object"
                    % (name, slot, raw))
        # A composite whose PRIMARY material could not be resolved is worse
        # rebuilt than left alone: it would render with no texture at all. The
        # original still resolves, so skip it and say so. The primary slot is
        # Diffuse on a Shader but Material on the TexModifier family and
        # Material1 on a Combiner.
        primary = material_slots[0]
        if props.get(primary, "None") != "None" \
                and not any(k == primary for k, _v in out):
            log("  %s: %s %s does not resolve, left on its own package"
                % (name, primary, props.get(primary)))
            skipped.append("%s'%s'" % (cls, name))
            continue
        for slot in scalar_slots:
            raw = props.get(slot)
            if raw is None or raw in ("None", ""):
                continue
            out.append((slot, enum_name(slot, raw)))
        if out:
            # The rebuilt object keeps its ORIGINAL class: a FinalBlend behind a
            # Shader' prefix would not resolve, and its blending properties do
            # not exist on a Shader anyway.
            made[name] = (cls, ms.add(cls, name, out))
    if skipped:
        log("  %d composites left on the source map (not a plain Shader): %s"
            % (len(skipped), ", ".join(skipped[:4])
               + (" ..." if len(skipped) > 4 else "")))
    return made


# -- composites umodel cannot dump -------------------------------------------
#
# umodel refuses several UE2 material classes outright ("Unknown class
# ColorModifier"), so scan() above never sees them and rebuild() leaves them on
# the source map. For a map-owned composite that is a dependency on the source
# map, which is exactly what a rebuild must not ship -- DM-1on1-Backspace draws
# 81 BSP surfaces through two ColorModifiers.
#
# Their tagged properties are readable straight out of the package, so they are
# rebuilt from there instead. A class is only listed here once every property it
# can carry is understood: an unknown property means the rebuild would be a
# guess, so the composite is reported and left alone rather than emitted wrong.

NATIVE_REBUILD = {
    # Engine/Classes/ColorModifier.uc -- Modifier.Material plus three of its own.
    "ColorModifier": {"Material": "object", "Color": "color",
                      "AlphaBlend": "bool", "RenderTwoSided": "bool"},
    # Engine/Classes/ConstantColor.uc -- a flat colour and nothing else.
    "ConstantColor": {"Color": "color", "FallbackMaterial": "object"},
}


def _decode(pkg, how, record, repoint, refs):
    """One tagged property as an UnrealScript literal, or None if unreadable."""
    from . import ue2
    if how == "bool":
        return "True" if record.raw and record.raw[0] else "False"
    if how == "color":
        if len(record.raw) < 4:
            return None
        b, g, r, a = record.raw[0], record.raw[1], record.raw[2], record.raw[3]
        return "(R=%d,G=%d,B=%d,A=%d)" % (r, g, b, a)
    if how == "object":
        ref = ue2.Reader(record.raw).index()
        path = pkg.ref_path(ref)
        if not path:
            return "None"
        leaf = path.rsplit(".", 1)[-1]
        if leaf in repoint:
            return "Texture'%s'" % repoint[leaf]
        if refs and leaf in refs:
            return "Material'%s'" % refs[leaf]
        return "Material'%s'" % path
    return None


def rebuild_native(project, ms, package, names, repoint, refs=None, log=print):
    """Rebuild map-owned composites straight from the package's own properties.

    `names` is the set of leaf names still wanted. Returns
    ({leaf: (class, name)}, {leaf: (r,g,b,a)}, {leaf: material leaf}) -- the
    second are the ones that reduce to a flat colour and become a texture; the
    third names the texture each draws through, whose upscale factor the
    surfaces' UVs must follow.
    """
    from . import ue2
    pkg = ue2.Package(project.map_file())
    made, colours, refused = {}, {}, []
    # The leaf each composite draws through, so the caller can rescale the
    # surfaces' UVs by that texture's factor -- they are in ITS texels.
    diffuse = {}
    for export in pkg.exports:
        name = export["name"]
        if name not in names:
            continue
        kind = pkg.class_of(export)
        spec = NATIVE_REBUILD.get(kind)
        if not spec:
            continue
        props, ok = [], True
        for key, records in pkg.properties(export).items():
            if key == "Material":
                ref = ue2.Reader(records[0].raw).index()
                path = pkg.ref_path(ref)
                if path:
                    diffuse[name] = path.rsplit(".", 1)[-1]
            how = spec.get(key)
            value = _decode(pkg, how, records[0], repoint, refs) if how else None
            if value is None:
                refused.append("%s (%s.%s)" % (name, kind, key))
                ok = False
                break
            props.append((key, value))
        if ok:
            flat = _flat_colour(kind, props)
            if flat:
                # A ColorModifier with no Material under it is nothing but a
                # flat colour, and a plain texture draws that with none of the
                # fragility of a material: a generated material is a subobject
                # of the generated CLASS, while a texture is an ordinary member
                # of a group package, which is what a .t3d reference resolves
                # against cleanly. Emitted as an 8x8 TGA by pkg.py.
                colours[name] = flat
            else:
                made[name] = (kind, ms.add(kind, name, props))
    if refused:
        log("  %d composite(s) NOT rebuilt, property not understood: %s"
            % (len(refused), ", ".join(refused)))
    return made, colours, diffuse


COLOUR = re.compile(r"\(R=(\d+),G=(\d+),B=(\d+),A=(\d+)\)")


def _flat_colour(kind, props):
    """(r,g,b,a) if this rebuilds to a plain colour with no material under it."""
    if kind != "ColorModifier":
        return None
    by = dict(props)
    if by.get("Material", "None") != "None":
        return None
    m = COLOUR.match(by.get("Color", ""))
    return tuple(int(g) for g in m.groups()) if m else None
