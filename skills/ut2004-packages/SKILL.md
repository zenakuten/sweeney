---
name: ut2004-packages
description: UT2004 content packages — importing textures, meshes and sounds with #exec, materials and Shaders, the UE2 package format, and verifying a build actually contains what you think. Use when building a .utx/.u content package, importing or replacing textures, when a surface renders transparent, upside down, flat or untextured, or when checking whether a rebuilt package kept everything the original had.
---

# Packages and content

Content reaches a package through `#exec` directives in a generated `.uc`, compiled by
UCC. Almost every failure here is silent: the build is clean and the result is wrong in
the game.

## Texture import

### `ALPHA=` is a rendering decision, not a storage one

Two different parameters that are easy to confuse:

- **`DXT=5`** (or 3) stores the alpha *channel*.
- **`ALPHA=1`** sets the texture's `bAlphaTexture` flag and nothing else, which makes the
  renderer alpha-blend the surface.

So driving `ALPHA=` from "does the source have an alpha channel" makes every DXT3/DXT5
texture translucent. **Follow the source texture's own `bAlphaTexture`** and let `DXT=`
handle the channel. `MASKED=1` sets `bMasked` separately.

Plenty of stock textures are stored DXT5 with `bAlphaTexture=false`. The symptom of
getting this wrong is surfaces rendering see-through with only their detail texture
visible — which reads as bad BSP, and is not.

### TGAs must be written bottom-up

`#exec TEXTURE IMPORT` of a `.tga` **flips the image vertically, unconditionally** — it
copies texture row `Y` from file row `Height - Y - 1` and never reads the TGA descriptor
byte, so the top-down bit has no effect.

Write rows bottom-up with descriptor `0x00`. A top-down file imports upside down.

Seamless tiling textures still tile, so only *directional* content looks wrong — a dark
base band appearing at the top. That makes it easy to misread as a lighting or UV
problem. DDS is unaffected (it carries its own orientation); BMP is natively bottom-up.

### `#exec` cannot set Detail, DetailScale or SurfaceType

There is no import parameter for them, and `POKE` is unreachable from a `.uc` — `#exec`
goes to the editor's own exec handler, which has no POKE command and does not fall
through. `#exec POKE ...` and `#exec OBJ POKE ...` are both **silent no-ops**: clean
build, properties left at defaults.

**The fix is a `Shader` wrapper.** Give the texture a Shader with
`Diffuse=Texture'...'` plus `Detail`, `DetailScale` and `SurfaceType`, and point surfaces
at the Shader. Write the enum by name — `SurfaceType=EST_Metal`, never `=3`.

What it costs when missed: close-range detail grain, and correct footstep and impact
sounds. Both silent.

> A `defaultproperties` object reference resolves at **compile** time, so a detail
> texture must already exist or the build fails loudly — unlike the rest of this.
> Copy detail textures into your own package rather than reaching for a stock one, and
> never assume which package one lives in: the `Detail` property names it
> group-qualified but package-less, and detail textures do not live beside the base
> texture.

### A Shader ignores the texture's own transparency

Wrapping a transparent texture in a Shader makes it **opaque**. The Shader does not
consult the diffuse's `bAlphaTexture` or `bMasked`; transparency comes from the Shader's
own `Opacity` slot, and `Opacity=None` takes the opaque path.

- source had `bAlphaTexture` → `Opacity=Texture'...'`, leave `OutputBlending=OB_Normal`
- source had `bMasked` → `Opacity=Texture'...'` **plus** `OutputBlending=OB_Masked`

Symptom: a cut-out drawn as a solid rectangle — a moon becoming an opaque quad against
the sky. This collides directly with the Shader wrapper above, since the reason to wrap
is Detail/SurfaceType and the cost is transparency.

## Referring to generated materials

Materials written as `Begin Object Class=Shader Name=X` in a generated package's
`defaultproperties` are **subobjects of the generated class**, not of the package root,
so their real path repeats the package name.

**Reference them by the short path** (`MyLevel.bas08go_SH`). The full path does not
resolve — name resolution walks intermediate components as packages, and a class does
not match.

> `Failed to load 'Material MyLevel.MyLevel.X_SH'` in `System/UnrealEd.log` is **normal
> and harmless**. Stock maps produce the same warnings. Do not chase it. Verify an
> import by loading the *saved* map and counting NULL surface materials, and comparing
> export counts against the `.utx`.

Textures are different — `#exec TEXTURE IMPORT ... GROUP=Bases` puts them in an ordinary
group package. Where a material reduces to something a texture can express (a colour
modifier over nothing is just a flat colour), emit a small solid TGA instead; a texture
is a plainer thing to resolve than a class subobject.

## Effects

A `MeshEmitter` draws a static mesh, and that mesh takes its material from the **Emitter
actor's `Skins[0]`**, not from anything on the emitter. When copying an effect between
mods, `Skins(0)` is not safe to drop.

If emitters or locker contents appear missing, suspect the client's **detail settings**
before the code — several stock effects require high detail to spawn at all.

## Verifying a build

A package can compile with zero errors and still be missing real content. After a
rebuild, **diff the name table against the original**: a name present only in the
original means something did not survive. It is fast and needs no bytecode parsing.

That technique caught both of a decompiled package's losses — enum defaults dropped by
UCC, and 30 embedded sound assets that the decompiler never extracted, because
decompilers recover classes and not embedded assets.

Size differing is expected: a rebuild embeds script text that a stripped original does
not.

## More

- `references/ue2-package-format.md` — parsing packages, the tagged-property traps,
  exporting meshes, and the redirect closure
- `references/vehicles-and-skeletal.md` — Karma, bone control, and effect lifetime
- `ut2004-maps` skill — getting this content into a map
