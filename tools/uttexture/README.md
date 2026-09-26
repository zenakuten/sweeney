# uttexture

Rebuilds any UT2004 map's textures at 4x: works out what the map uses, pulls
those textures out of whatever packages they live in, upscales them, writes a
buildable `<Map>Tex` package, and rewrites the map's `.t3d` to match.

Generalised from the `../roughinery` project, which shipped
DM-1on1-Roughinery and is kept as the worked example.

## Requirements

- Python 3 with `numpy` and `Pillow`
- ImageMagick's `magick` command
- `realesrgan-ncnn-vulkan` and its model files
- `umodel`, configured by Sweeney's setup

Install the normal ImageMagick package from
https://imagemagick.org/script/download.php; only its `magick` command is needed, not an
SDK or Python binding. Download the model runner from
https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases/latest. The runner's release
archive contains a `models/` directory; set `models_dir` in the map's `config.json` to
that native path and put the executable on `PATH`. On Windows, run from Git Bash, invoke
Python as `py`, and use a JSON path such as
`C:/Tools/realesrgan-ncnn-vulkan/models` rather than `/c/Tools/...`.

## Use

```bash
./uttexture.py survey   DM-Deck      # what it uses, and how each texture is reached
./uttexture.py extract  DM-Deck      # pull them out, read their properties
./uttexture.py upscale  DM-Deck      # run the upscaler
./uttexture.py package  DM-Deck      # write <Map>Tex: TGAs + generated .uc
./uttexture.py t3d      DM-Deck      # export the map and rewrite it
./uttexture.py all      DM-Deck
```

Then name `<Map>Tex` in `EditPackages`, and:

```bash
cd System && rm -f DMDeckTex.u && ./UCC.exe make
cp DMDeckTex.u ../Textures/DMDeckTex.utx
```

`ucc make` skips a package whose `.u` already exists, so the delete is not
optional. In UnrealEd, **File > Import** the `.t3d` (not File > Open), then
Build Geometry, Lighting and Paths.

Run `survey` again after `t3d`: the first survey cannot see textures that only
ever appear on brush polygons or in actor `Skins()` overrides, and it says so.

## Layout

```
uttexture.py       CLI
uttexture/              ue2, survey, extract, manifest, upscale, pkg, t3d, inject, compare
models/            .safetensors weights for the torch path
scans/             ambientCG material scans for detail injection
.rocmlibs/         shim that makes python-pytorch-rocm importable here
maps/<Map>/        raw/ up/ meta/ out/ compare/ config.json
```

`maps/<Map>/config.json` holds the per-map settings — model, sharpening, scan
injection, sky chain. Everything else is derived from the map.

## What it works out for itself

The texture list comes from the map, not a hand-written list: the import table
is authoritative for external references, the BSP surface array and the exported
`.t3d` say what is actually drawn, and `umodel -dump` supplies the embedded
meshes' materials, which appear nowhere else. Groups come from the resolved
import paths, and the package name from the map name.

`survey` separates three cases, because they need different handling:

- **textures** — upscaled and repointed.
- **composites** (Shader, Combiner, TexPanner…) — graphs over other textures, so
  they cannot be upscaled. Rebuild by hand; `config.json`'s `sky` block
  automates the one common case, a panning cloud dome.
- **referenced but never drawn** — mesh slots whose section has no faces, and
  textures reachable only through a composite or another texture's `Detail`.
  Excluded by default, listed so nothing disappears silently. `--keep-unused`
  includes them.

## Things that will bite

**Brush UVs must be rescaled with the textures.** UE2 states polygon texture
coordinates in *texels*, so a 4x texture at the same `TextureU` covers 4x the
world space and every surface reads as zoomed in. `t3d` multiplies TextureU/V by
the same factor. `Origin` is left alone — brush polys carry no Pan, it is folded
into Origin, a world point on the surface.

**TGAs go out bottom-up.** UT2004's importer flips scanlines unconditionally and
never reads the descriptor byte (`Editor/Src/UnEdFact.cpp:2498`), so a top-down
file imports upside down — invisible on a seamless texture, unmistakable on
anything with a top and a bottom.

**Upscale wrap-padded, or tiling breaks.** Each texture is padded by wrapping
its opposite edge round before upscaling and cropped back after, or the model
invents different content at each edge and the seams stop matching.

**A mesh with no CollisionModel can still hand you a hull.** `UStaticMesh`'s
serialised layout is walked to reach its `CollisionModel` reference; when a mesh
has none, that walk reads whatever follows, and any value that happens to index
a `Model` export looks valid. DM-1on1-Lea's `idomacrate` resolved to `Model80`,
which belongs to `Brush77`, and DM-Corrugation's `pipeend` did the same. The
rebuild then carries unrelated brush geometry into the mesh as its collision
hull, hundreds of units from the mesh itself -- an invisible wall in mid-air,
while the original map is fine because the engine sees a NULL `CollisionModel`
and falls through to the kDOP tree (`UnStaticMeshCollision.cpp:77`).

`static_mesh_collision` now requires the resolved Model's bounds to overlap the
mesh's own, since a real collision model bounds what it belongs to. Two meshes
across the 20 maps were affected.

The symptoms differ by how the bogus hull happens to be shaped. `pipeend`'s was
a single quad -- fewer than four faces, so it encloses no volume, and the engine
answers "is this point solid" and "does this sweep hit" differently for it. That
is invisible offline, where movement is all swept `MoveActor` calls (and so are
the server's, and bots'), but a network client also PLACES its pawn through
`ClientAdjustPosition` -> `SetLocation`: the placement test disagreed with the
sweep, the client could not accept corrections, and the player warped between
two points. `idomacrate`'s was a closed box, so it simply blocked -- the same
fault with a less exotic symptom.

`drop_open_hull` stays as a guard for a hull that encloses no volume. Closing
such a hull by extruding it into a slab was tried and is worse: the back and
side faces it invents are real surfaces with nothing rendered at them, so
players walk up onto them and bump into them.

**One model does not suit every texture.** `realesrgan-x4plus-anime` is trained
on line art and will flatten a patch of a photographic texture into a smooth
blob -- localised, so an image-wide average hides it, and unmistakable in game
(reported on Koden's `BrickWall12c`). `tools/check_detail.py <Map>` grades an
8x8 grid of each texture against its source and names the ones that lost
detail; `--try realesrgan-x4plus` retests those with another model and prints a
ready-made `"model_overrides"` block. That config key maps a texture name to
the model that upscales it, leaving `model` as the default for everything else.

**Shared-store ownership is keyed on the source package, not the name.** Two
packages can both hold a `White`: DM-Koden has its own `Koden.White` and
UCGeneric has another. Matching on the bare name made the store claim the map's
copy, so `package` stopped importing it while the `.t3d` went on referencing
`MyLevel.Koden.White` -- 90 surfaces about to import with no texture. Both
`shared_leaves` and `write_map_refs` compare `<source package>.<name>`.

**Shipping under a new name carries the map's own assets.** `config.json`'s
`map_name` sets the name the rebuild ships under, defaulting to `<Map>4K`;
Roughinery keeping the stock name is the special case. Objects embedded in the
map package -- static meshes, a UV2 overlay, the level shot -- are referenced as
`<MapPkg>.<Name>`, which resolves to the new map only while the two names agree.
Under any other name it resolves against the *original* map instead, quietly
making the stock map a dependency. So `package` carries them into `<Map>Tex` and
`t3d` repoints the references there; no hand-importing in the editor.

Two things this has to work around:

- **Static meshes cannot round-trip in their exported form.** `UCC.exe
  batchexport <Map>.ut2 StaticMesh T3D` is the only way out, but the Static Mesh
  browser imports `.ase`/`.lwo`/`.obj` only, so `uttexture/mesh.py` converts the
  triangle soup to ASE. It is loaded with `#exec NEW STANDALONE
  StaticMeshFactory`, *not* `#exec STATICMESH IMPORT` -- that exec takes
  LightWave `.lwo` only (`UnEdSrvExecImporters.cpp:426`). The ASE binds its
  `*BITMAP` against an already-loaded texture, so mesh imports are emitted after
  the texture imports.
- **Composites cannot be rebuilt**, and a level shot is usually a
  MaterialSequence over two frames. `level_shot` names one plain texture to use
  as the `Screenshot` instead; anything else composite is listed and left, since
  a dangling reference is worse than an honest one. Textures come out through
  umodel, because UCC's BMP exporter reads only P8/RGBA8/G16 and answers a DXT
  texture with a 0-byte file and a success message.

**`Brush=Model'<Map>.ModelNNN'` must be relinked.** Left alone it resolves
against the original package — which is loaded, because the meshes come from it
— and the importer takes that model instead of the inline one, discarding the
rescaled UVs with no error.

**Nothing can set `Detail`, `DetailScale` or `SurfaceType` on an imported
texture, so they move to a Shader.** `#exec TEXTURE IMPORT` has no parameter for
them and only preserves them when overwriting a texture that already exists
(`UnEdSrv.cpp:586-704`). `POKE` cannot reach them either: it lives in
`UObject::StaticExec` (`Core/Src/UnObj.cpp:2999`), but a `.uc`'s `#exec` goes to
`UEditorEngine::SafeExec` (`UnEdSrv.cpp:387`), which has no POKE command and
returns 0 instead of falling through, and whose `OBJ` handler takes only LOAD
and IMPORT. `#exec POKE` and `#exec OBJ POKE` are both silent no-ops -- no
output, no warning, a clean build, every property left at its default.

So `package` emits a `Shader` per affected texture carrying `Diffuse` plus those
three, and `t3d` points the brush polys and `Skins()` at the Shader. The
renderer prefers a Shader's own `Detail` over the diffuse texture's
(`D3D9MaterialState.cpp:1141`), and `SurfaceType` is a `UMaterial` property so
it rides along. The enum goes in by name: UCC silently discards an integer
assigned to an enum in defaultproperties, so `SurfaceType=3` would leave it
`EST_Default`. 

**The detail textures are copied in, and their package is never guessed.** A
defaultproperties object reference resolves at **compile** time, so naming one
that lives in a package nobody loaded fails the build outright with "unresolved
reference to Texture'...'". Rather than loading stock packages to reach a
128x128 grain, `package` copies the ones the map uses into `<Map>Tex` -- a few
tens of KB, uncompressed, because re-compressing grain that was already DXT once
would add a second generation of loss to the one thing whose whole job is high
frequency. They are never *upscaled*: an upscale interpolates the same
128x128 of information over more area, which buys nothing, and the upscaler
flattens grain badly anyway (detail51 lost 76% of its contrast).

**They are synthesised larger instead, and `DetailScale` divided to match**
(`detail_mode`, default `synth`; `detail_factor`, default `scale`). This is
the fix for the one thing a 4x rebuild makes worse: a detail tile's world size
is set by `DetailScale` alone, so the stock grain repeats exactly as often as
it always did -- but the smoothed 4x base no longer carries pixel noise of its
own to camouflage it, and the repeat lattice becomes the thing your eye finds.
Enlarging the tile is the only cure, and it needs new content to fill it.
`uttexture/detail.py` shapes white noise to the source's own radial power spectrum,
measured in cycles per *texel* so a texel of the result carries the same grain
as a texel of the original, then rescales to the source's exact mean and
standard deviation. Built in the frequency domain, so it tiles seamlessly by
construction -- the seam difference matches the interior difference, which the
stock textures do not quite manage.

Two things to keep right. Use **one** noise field for all three channels,
scaled per channel: these grains are greyscale (R=G=B per texel), and an
independent field per channel invents chroma speckle that reads as a tint
crawling over the surface. And phase randomisation keeps the spectrum but not
the *phase*, so clustered structure -- the stock speckle and flecks -- does not
survive; the result is even noise of the same contrast. On DM-Ironic4K 4x was
chosen by eye over 2x and over the stock grain.

The package they come from has to be **resolved, not assumed**. A texture's
`Detail` property names them group-qualified but package-less
(`Texture'DetailTextures.detail40'`), and they do not live beside the base
texture: every detail texture DM-DE-Ironic uses is in `UCGeneric`, while its
bases come from `HumanoidArchitecture`, `HumanoidArchitecture2`,
`ArboreaTerrain` and `HumanoidHardwareBrush`. `uttexture/detail.py` finds the real
one by scanning package name tables (mmapped, so a 74MB `.utx` stays cheap) and
confirming against the export table. Losing these costs the close-range detail grain and the right
footstep and impact sounds -- both invisible in a build log, which is how they
went missing from two shipped packages before anyone noticed the walls looked
flat up close.

**A `.t3d` carries no `LightMapScale`**, so the editor re-defaults it on import
and shadow detail changes. `survey` reports the original so you can set it back
(`POLY SELECT ALL`, then Surface Properties > LightMap Scale).

## Choosing an upscaler

`./uttexture.py compare <Map> <texture>` renders one texture through every installed
method into `maps/<Map>/compare/<texture>/`, numbered in the order they were
tried, with a note on each.

Do not pick on a metric. A high-pass "detail" score ranked the sharpest ESRGAN
variant best on Roughinery's concrete, and the gain it measured *was* the
artifact — flat areas posterized into hard-edged polygons that read as an aerial
view of city blocks. Judge by eye, on flat areas, at 100%, and tiled.

For the record, on Roughinery: the ESRGAN sharpeners (ultrasharp, remacri,
high-fidelity, ultramix) posterize; the DAT transformer lays down a 4px stipple
lattice, one dot per source pixel; HAT-L is clean but barely beats plain x4plus.
`digital-art-4x` was chosen — smooth, and free of every artifact class.

## The torch path

`models/` holds transformer weights loaded through spandrel. On this machine
`python-pytorch-rocm` cannot import unaided: `opencl-amd` declares `provides` for
~30 ROCm package names and conflicts with the real ones, so pacman never
installs the libraries torch links. `.rocmlibs/` supplies them — a real
`libroctx64`, and a no-op stub for `librocprofiler-sdk`, because the real one is
worse than absent (opencl-amd's registrar calls it across a mismatched ABI and
segfaults). `torch_runner` re-execs itself with `LD_LIBRARY_PATH` set.
