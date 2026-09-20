# .t3d round trip: checklist and mechanics

## Before importing a rewritten .t3d

- [ ] Drop `Brush=Model'<Map>.ModelNNN'` — otherwise it silently overrides your geometry
- [ ] Drop baked per-instance lighting and reachspecs; the editor rebuilds them
- [ ] Rescale `TextureU`/`TextureV` by the texture size factor (brushes only)
- [ ] Inject `Skins()` into **every actor that names a mesh**, not just `StaticMeshActor`
- [ ] `grep -c "<SourceMap>\." out/*.t3d` must be 0

## After importing and saving

- [ ] Compare actor counts per class against the source map
- [ ] Count NULL surface materials
- [ ] `grep "BuildZoneInfo:" System/UnrealEd.log | tail -1` — duplicates must be 0
- [ ] Restore level text (Title, Description, Author) from the source package
- [ ] `grep -c "^Game=" System/CacheRecords.ucl` — must be 12
- [ ] Reset `LightMapScale` if parity with the original matters

Import in UnrealEd with **File > Import**, not File > Open. Then Build Geometry, Lighting
and Paths.

Rebuilding geometry in the **already-imported** map avoids the whole round trip and the
level-text restoration with it, where that is an option.

UnrealEd can crash if you rebuild and save repeatedly in one session — restart between
attempts.

## Resetting LightMapScale

A `.t3d` carries no `LightMapScale`; the exporter never writes it and the parser never
reads one, so the editor supplies its own default. Stock initialisation uses 32.0, but a
patched editor has been seen assigning 4.0 — eight times finer per axis, growing the level
model from 3.0MB to 4.3MB even with fewer BSP nodes.

In UnrealEd: `POLY SELECT ALL`, then Surface Properties (F5) > Pan/Rot/Scale > LightMap
Scale > 32.0, then Build > Lighting. That handler writes both the surface and its master
brush poly, so it survives a later Build Geometry.

To check it, read the level model's surface array and histogram the value — it serialises
as the last float of each surface record.

This is a lighting-resolution difference only. Do not reach for it to explain a texture
that looks wrong; that is usually a flipped TGA or unscaled brush UVs.

## Rewriting a package in place

Both level-text restoration and author tagging rewrite the built package directly. A UE2
package stores **export data before the import and export tables**, so growing an export
in place would shift every later export *and* every lazy-array skip offset — which are
absolute file positions embedded in each texture export. That is silent corruption.

Instead, append the rewritten exports after the import table and move the export table to
the end, so no existing offset moves:

```
[header][names][export data ...][imports][new exports][export table]
```

Two traps when walking tagged properties by hand:

- the property-type constants are `PT_BOOL = 3` and `PT_STRUCT = 10` — guessing 1 and 3
  desyncs at the first bool
- an array property writes its element index **after** the size and **before** the value

See the `ut2004-packages` skill for the tagged-bool size field, which is the other half of
this.

## Judging whether geometry actually changed

Surface counts drift harmlessly. If the geometry itself is in doubt, sample solid space in
both builds — walk the playable volume and compare. Use a **binary search on the surface
position**, not a fixed grid, or grid alignment will fool you.

For scale: two builds once disagreed at exactly one place, a diagonal wall displaced by
0.00013uu. Player collision radius is 17. It only showed up because the original wall sits
on exact integer coordinates, so the sample landed precisely on the plane.

## When a map simply cannot survive the round trip

At least one map has been established as unable to survive a `.t3d` import at all —
geometry is lost, BSP settings do not help, and it rebuilds perfectly in place. If the
evidence points that way, rebuilding in the existing map is the answer; do not keep
retrying the round trip.

Establish the limit with a **control import**: export the stock map with no rewriting at
all and import that. If the control fails the same way, the pipeline is not the problem
and no amount of work on it will help. That one experiment has settled hours of wrong
hypotheses.
