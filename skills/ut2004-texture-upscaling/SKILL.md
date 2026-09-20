---
name: ut2004-texture-upscaling
description: Rebuilding a UT2004 map's textures at higher resolution with the utupscaler toolchain — survey, extract, upscale, package, t3d. Use when upscaling a map's textures, building a <Map>Tex package, when a rebuilt map renders flat, zoomed, untextured or transparent, or when working out which textures a map actually uses.
---

# Texture upscaling

`utupscaler` works out what textures a map uses, pulls them out of whatever packages they
live in, upscales them, writes a buildable `<Map>Tex` package, and rewrites the map's
`.t3d` to match. Find it via `tools.utupscaler` in `~/.sweeney/config.json`.

```bash
./utup.py survey   DM-Deck      # what it uses, and how each texture is reached
./utup.py extract  DM-Deck      # pull them out, read their properties
./utup.py upscale  DM-Deck
./utup.py package  DM-Deck      # write <Map>Tex: TGAs + generated .uc
./utup.py t3d      DM-Deck      # export the map and rewrite it
./utup.py all      DM-Deck
```

Then name `<Map>Tex` in `EditPackages` and:

```bash
cd System && rm -f DMDeckTex.u && ./UCC.exe make
mv DMDeckTex.u ../Textures/DMDeckTex.utx
```

**`mv`, not `cp`** — `System/*.u` is searched before `Textures/*.utx`, so a leftover `.u`
silently shadows the shipped `.utx`. Copying instead has produced gigabytes of duplicates
and builds that tested the wrong file.

Per-map settings live in `maps/<Map>/config.json` — model, sharpening, scan injection, sky
chain. Everything else is derived from the map.

## Survey twice

**Run `survey` again after `t3d`.** The first survey cannot see textures that appear only
on brush polygons or in actor `Skins()` overrides, and it says so. A fresh map can
under-report badly — 20 textures against a real 41.

Survey separates three cases because they need different handling:

- **textures** — upscaled and repointed.
- **composites** (Shader, Combiner, TexPanner) — graphs over other textures, so they cannot
  be upscaled. Rebuild by hand; the `sky` block in `config.json` automates the common
  panning cloud dome.
- **referenced but never drawn** — mesh slots whose section has no faces, and textures
  reachable only through a composite or another texture's `Detail`. Excluded by default and
  listed, so nothing disappears silently. `--keep-unused` includes them.

## A new map needs the content step

`utup.py all` skips accrual into the shared content store. Run the content step for a map
that has not been through the pipeline before, or it comes out with hundreds of NULL
surfaces.

The shared store holds `<Source>4K` packages that grow as maps need them, which is what
took one map set from 2135MB to 211MB.

## What goes wrong, and what it looks like

Each of these is silent — the build is clean and the map is wrong in game. All are covered
in the `ut2004-packages` and `ut2004-maps` skills; this is the index.

| Symptom | Cause |
|---|---|
| surfaces see-through, only detail texture visible | `ALPHA=` driven from "has an alpha channel" rather than the source's `bAlphaTexture` |
| directional texture upside down | TGA written top-down; the importer flips unconditionally |
| everything reads zoomed in | brush UVs are texels — scale `TextureU`/`TextureV` by the same factor |
| surfaces go flat up close, wrong footstep sounds | no Shader wrapper, so `Detail` and `SurfaceType` never got set |
| a cut-out drawn as a solid rectangle | Shader wrapper with `Opacity=None` |
| a few meshes in flat grey | mesh material slots not bound — check both umodel dump shapes |
| players snag, or a gap seals | collision hull carried when it should not be, or missing |

## Caching and scratch

**A dump cache must key on the object set it was taken with**, not just the package. A
stale dump silently strips alpha: builds clean, looks wrong in game.

**`/tmp` is tmpfs — scratch costs RAM.** Leftover debug exports have got long builds
killed by the OOM killer. Judge headroom by *available* memory, and clean up exports.

## Settled decisions

The upscaler model choice is settled. Do not re-litigate it.

The worked example (`roughinery`) is the shipped map this was generalised from; consult it
for how a finished map turned out, not as the current pipeline.
