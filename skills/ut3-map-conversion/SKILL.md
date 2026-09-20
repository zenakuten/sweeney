---
name: ut3-map-conversion
description: Converting UT3 maps and content packages to UT2004 with the ut3converter toolchain — ut3conv.py, batch.py, and the limits of what converts. Use when converting a .ut3 map or a .upk content package to UT2004, when a converted map is dark, untextured or missing geometry, or when batch-converting and building map packages.
---

# UT3 → UT2004 conversion

`ut3converter` is a Python 3 toolchain that reads a UT3 map (`.ut3`, UE3 package version
512) and writes a UT2004 `.t3d` plus a buildable asset package. It runs natively on Linux
or Windows; only `UCC.exe` needs Wine.

Find it via `tools.ut3converter` in `~/.sweeney/config.json`. It must live inside the
UT2004 install — `ucc make` resolves the `FILE=` paths in the generated `.uc` relative to
the install root, so anywhere else means copying it back before every build.

## The essential fact

**UT3 maps are static-mesh-based.** BSP is used for blocking volumes and rough shapes, not
for what you see. A conversion that carries only BSP produces an empty shell. If a
converted map looks like a grey box, that is what happened.

## One command does the conversion

```bash
./ut3conv.py t3d "<UT3>/UTGame/CookedPC/Maps/DM-Deck.ut3" -o out-dm-deck/DM-Deck.t3d
```

Two things come out: the `.t3d`, and `<UT2004>/DMDeckTex/` — the asset package source,
with a generated `.uc` of `#exec` imports. The package name is the map name stripped of
punctuation plus `Tex`; override with `--texture-package`.

**Read the summary it prints.** It is where the conversion says what it could not do:
counts of brushes, meshes, actors, movers, sounds, lights, pickups and path nodes, plus
any unresolved materials.

Other subcommands: `assets <pkg.upk>` extracts a content package's textures, meshes,
sounds, skeletal meshes and animations into one buildable package; `info`, `classes` and
`imports` inspect a package without converting.

## Building and importing

```bash
cd System && rm -f DMDeckTex.u && wine UCC.exe make
mv DMDeckTex.u ../Textures/DMDeckTex.utx
```

Deleting the `.u` first is not optional — `ucc make` skips a package whose `.u` exists.

**Name only this map package in `EditPackages`.** With a 32-bit `UCC.exe` a build has
about 2GB of address space, and that must cover every package `EditPackages` names —
including already-built ones, since UCC still *loads* them to resolve references. Seven map
packages come to 434MB; sixty-odd would be several GB. Leave unrelated entries alone; their
`.u` files already exist, so they cost nothing.

In UnrealEd: **File > Import** the `.t3d` — not File > Open, it is not a `.ut2` — then
**Build > Geometry** and **Build > Paths**. UT2004 derives its own reachspecs and jump
velocities, so bots have no paths until you do. Then save to `Maps/`.

See the `ut2004-maps` skill for what to check after an import; a partial import that
silently deletes actors is a real failure mode here.

## In bulk

```bash
./batch.py --list                  # what would be converted
./batch.py --match Deck            # convert, no build
./batch.py --match Deck --build    # convert and build
./batch.py --build                 # everything
```

It rewrites `EditPackages` between its own markers so your entries survive, and builds one
map package at a time for the address-space reason above.

**Other maps are knowingly stale.** After a fix that changes conversion output, do not
re-run every map per fix — batch them.

## What does not convert

Not planned: particles and emitters, Kismet, SpeedTree, post-process volumes,
destructibles.

Known and accepted:

- Meshes needing per-poly collision want `UseSimpleKarmaCollision=False` set **by hand in
  the editor**. Forcing it off at import broke every mesh, so there is no build-time fix.
- A few BSP faces resolve to a checkerboard placeholder where the UE3 material is a shader
  with no texture in it.
- Vehicles fall through some meshes players walk on — a collision flag UE2 does not derive
  from an ASE.
- A follower attached to a rotating mover turns on its own pivot instead of orbiting.

## Things that surprised, and are worth knowing

- **Materials are the hard part.** The heuristic is "first texture sample reaching
  Diffuse", corrected for relief bakes, instances that override only a normal map,
  non-colour markers and explicit slot overrides. Every fix generalised; the per-map escape
  hatch was never needed.
- **Lighting needs global re-tuning.** `--light-gain` (default 32) and `--ambient-gain`
  (default 16) are the dials. UT3 maps convert dark otherwise, and a map with any SkyLight
  gets an ambient floor.
- **Scale is a non-issue** — 1.0 throughout. UT2004's dodge and higher jump make UT3
  geometry play fine.
- **UT2004's limits were never the problem**; the 32-bit compiler's address space was.

## Testing

The test suite reads the stock UT3 maps directly, so a regression shows against real data
rather than a fixture. Run it after changing conversion logic.
