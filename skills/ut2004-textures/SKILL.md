---
name: ut2004-textures
description: Rebuilding a UT2004 map's textures with AI image models — surveying what a map actually uses, extracting it, regenerating it at any scale, and writing a buildable <Map>Tex package plus a rewritten .t3d. Use when remaking, upscaling, restyling or resharpening a map's textures, when working out which textures a map really uses, or when a rebuilt map renders flat, zoomed, untextured or transparent.
---

# Rebuilding a map's textures

The `utupscaler` toolchain works out what textures a map uses, pulls them out of whatever
packages they live in, **runs them through an image model**, writes a buildable
`<Map>Tex` package, and rewrites the map's `.t3d` to match. Find it via
`tools.utupscaler` in `~/.sweeney/config.json`.

**Upscaling is one use, not the purpose.** `scale` is a parameter, and the pipeline is
just as usable for regenerating textures at their existing size — restyling a map,
resharpening it, or replacing surface detail — because the hard parts are the same
whatever the output resolution: knowing what the map really uses, resolving it through
composites and mesh slots, rebuilding a package that loads, and repointing the map at it
without breaking anything.

The only step that *cares* about resolution is brush UV rescaling, and that is driven by
the factor a texture actually grew — so at 1× there is nothing to rescale.

```bash
./utup.py survey   DM-Deck      # what it uses, and how each texture is reached
./utup.py extract  DM-Deck      # pull them out, read their properties
./utup.py upscale  DM-Deck      # run the image model
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

## Per-map configuration

`maps/<Map>/config.json`. Everything not set here is derived from the map.

| Key | |
|---|---|
| `model`, `models_dir` | the image model and where its weights live |
| `scale` | output factor — **not required to be > 1** |
| `skip_upscale` | textures to pass through untouched |
| `sharpen` | post-process strength |
| `detail_mode` | `none` / `copy` / `upscale` / `synth` — what to do with detail textures |
| `detail_model`, `detail_factor`, `detail_strength` | the detail pass, independently of the main one |
| `inject` | material scans to blend in for surface grain |
| `sky` | rebuild a panning cloud dome, the one automated composite case |
| `shared_content` | accrue into the shared store rather than a per-map package |
| `carry_meshes` | carry meshes and sounds into the package (default true) |

Two models can be in play at once — the base pass and the detail pass — and either can be
told to leave things alone. That is what makes the pipeline a general texture-remaking
tool rather than an upscaler.

## Survey twice

**Run `survey` again after `t3d`.** The first survey cannot see textures that appear only
on brush polygons or in actor `Skins()` overrides, and it says so. A fresh map can
under-report badly — 20 textures against a real 41.

Survey separates three cases because they need different handling:

- **textures** — regenerated and repointed.
- **composites** (Shader, Combiner, TexPanner) — graphs over other textures, so they
  cannot be fed to an image model. Rebuild by hand; the `sky` block automates the common
  panning cloud dome.
- **referenced but never drawn** — mesh slots whose section has no faces, and textures
  reachable only through a composite or another texture's `Detail`. Excluded by default
  and listed, so nothing disappears silently. `--keep-unused` includes them.

## A new map needs the content step

`utup.py all` skips accrual into the shared content store. Run the content step for a map
that has not been through the pipeline before, or it comes out with hundreds of NULL
surfaces.

The shared store holds `<Source>` packages that grow as maps need them, which is what took
one map set from 2135MB to 211MB.

## Detail textures

A detail texture is close-range grain, not artwork — typically 128×128, a few tens of KB.
Its tiling comes from `DetailScale`, not from its own size, so regenerating it larger is
usually pointless; `detail_mode=copy` is often right.

`synth` generates larger grain and divides `DetailScale` to match, which is what a
resolution increase actually needs — at 1× it has nothing to do.

Copy detail textures into your own package rather than reaching for a stock one, and never
assume which package one lives in: the `Detail` property names it group-qualified but
package-less, and detail textures do not live beside the base texture.

## What goes wrong, and what it looks like

Each is silent — the build is clean and the map is wrong in game. All are covered in the
`ut2004-packages` and `ut2004-maps` skills; this is the index.

| Symptom | Cause |
|---|---|
| surfaces see-through, only detail texture visible | `ALPHA=` driven from "has an alpha channel" rather than the source's `bAlphaTexture` |
| directional texture upside down | TGA written top-down; the importer flips unconditionally |
| everything reads zoomed in | brush UVs are texels — scale `TextureU`/`TextureV` by the factor the texture grew |
| surfaces go flat up close, wrong footstep sounds | no Shader wrapper, so `Detail` and `SurfaceType` never got set |
| a cut-out drawn as a solid rectangle | Shader wrapper with `Opacity=None` |
| a few meshes in flat grey | mesh material slots not bound — check both umodel dump shapes |
| players snag, or a gap seals | collision hull carried when it should not be, or missing |

## Caching and scratch

**A dump cache must key on the object set it was taken with**, not just the package. A
stale dump silently strips alpha: builds clean, looks wrong in game.

**`/tmp` is tmpfs — scratch costs RAM.** Leftover debug exports have got long builds killed
by the OOM killer. Judge headroom by *available* memory, and clean up exports.

## Settled decisions

The model choice for the 4× upscaling work is settled. Do not re-litigate it — that is a
decision about one project's output, and does not constrain a different texture pass.

The worked example (`roughinery`) is the shipped map this was generalised from; consult it
for how a finished map turned out, not as the current pipeline.
