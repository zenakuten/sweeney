---
name: ut2004-maps
description: UT2004 map work — .t3d export and import, UnrealEd, BSP and CSG, zones, terrain, brush UVs and static mesh collision. Use when rewriting or importing a .t3d, rebuilding geometry, when a rebuilt map is missing meshes or looks zoomed, when players snag on invisible walls, when zones or lighting change after a rebuild, or when the gametype list goes empty.
---

# Maps, .t3d and UnrealEd

A `.t3d` is a **text** export of a map. That one fact explains most of this skill: the
format cannot represent everything a `.ut2` holds, and what it cannot represent is lost
silently on every round trip. Nothing errors; the map builds, saves and plays.

**Verify a round trip by comparing the result against the source map** — actor counts per
class, NULL surface materials, zone assignments. Never judge an import by whether the
editor finished without complaining.

## What a round trip loses

| Lost | Consequence |
|---|---|
| `LightMapScale` | the editor re-defaults it; lighting resolution only |
| Terrain sector data | the editor crashes drawing the level — see below |
| UT colour codes in level text | markup shows literally instead of colouring |
| Anything after a `"` in a string property | truncated author/title, silently |
| Collision hulls on static meshes | players snag, or gaps seal — see below |

Level text has to be copied **byte for byte out of the source package** afterwards; it
cannot be recovered from the export. Colour codes are an escape byte plus three RGB
bytes, which the exporter writes as a literal `(#FF0101)` — and since an author could
legitimately type that, the import cannot reverse it. String properties are written with
**no escaping**, so `Scott "Goose" McGregor` imports as `Scott `.

## Traps when rewriting a .t3d

### `Brush=Model` silently wins over your edits

A leftover `Brush=Model'<Map>.ModelNNN'` property **resolves against the original
package**, and the importer takes that model instead of the inline brush — discarding
every edit to the geometry or its UVs. It resolves because the original package is
usually loaded anyway, so it never fails the way a missing reference would, and nothing
is logged.

**Drop that scaffolding before importing**, along with baked per-instance lighting and
reachspecs, which the editor rebuilds anyway.

This is the trap that hides the next one: UVs that were rescaled but never took effect
look exactly like UVs that were never rescaled.

### Brush UVs are texels

A UE2 brush polygon's texture coordinates are in **texels**, not normalised:

```
texel = (Vertex - Origin) dot TextureU
```

So a texture N times larger covers N times more world space at the same `TextureU`, and
every surface reads as zoomed in. Multiply `TextureU` and `TextureV` by the same factor
the texture grew.

`Origin` needs no adjustment — brush polys carry no Pan (it is folded into Origin), and
Origin is a world point on the surface.

**Static meshes are exempt**; their UVs are normalised.

### Carried assets must not reference the source map

A rebuilt map must reference **only** the package built alongside it. A leftover
`StaticMesh'<SourceMap>.Stuff.X'` resolves locally and looks fine — but the server's net
package map recurses the import table, so the source map is advertised to every client,
matched **by GUID**. A client without that exact build must download it, and if downloads
are disallowed the connection is refused.

For a retail map that is a wasted download. For a community map it is a broken server.
After generating, `grep -c "<SourceMap>\." out/*.t3d` must be 0.

## Static mesh collision

Two opposite failures, and one flag decides which applies.

`CollisionModel` is a **separate object in the package**, not part of the mesh. An ASE
round trip produces none, so the engine falls back to colliding against the render
triangles.

- **Some meshes need the hull.** A thin, rotated, non-uniformly scaled prop catches a
  player capsule on its end faces without one — planks that became impassable. The
  geometry checks all pass, because the geometry *is* identical; the missing data sits
  beside the mesh.
- **Some meshes must not have one.** `UseSimpleBoxCollision` defaults to **true** and
  decides whether an extent trace hits the hull or the exact triangles. Retail packages
  set it **false** on selected meshes, and an ASE round trip cannot carry that flag — the
  rebuilt mesh always comes back true. Emitting a hull for such a mesh makes the engine
  honour one it never used, **filling every gap the exact geometry left open**. An
  invisible wall.

So: carry the hull only when the source mesh does not have `UseSimpleBoxCollision=False`.

Writing a hull into an ASE: a second `*GEOMOBJECT` whose `*NODE_NAME` starts with
**`MCDCX`** is read as collision geometry — plain `MCD` is ignored. Triangulate and write
each triangle **reversed**, since the importer rebuilds every poly back to front and the
winding decides inside from outside.

## Carried meshes come back untextured

Two causes, both silent, both leaving a handful of meshes in flat grey default texture
while BSP surfaces look fine:

1. **An ASE binds `*BITMAP` by object name** against already-loaded materials, so each
   slot must name what the rebuilt package actually contains — `bas08go_SH`, not
   `bas08go`. Meshes therefore have to be built *after* the wrappers exist. A material
   left in its own foreign package has nothing to bind to and needs an explicit `Skins()`
   with the full original path.
2. **Any actor can draw a static mesh.** A `Mover` with `DrawType=DT_StaticMesh` is how
   lifts and doors are built. Injecting `Skins()` only into `StaticMeshActor` leaves every
   lift and door untextured.

## Rebuilding geometry

### CSG precision can merge zones

Re-running CSG shifts surface planes by thousandths of a unit, and a zone portal that
exactly met its surrounding geometry can stop sealing. The two zones **merge** and one
`ZoneInfo` governs nothing. Actors in the lost zone answer to the wrong `ZoneInfo` —
wrong ambient light, sound, culling and gravity.

Nothing obvious breaks: actor counts match, surfaces match, the BSP has no holes, every
portal surface is still present.

**The engine reports it directly**, after Build > Geometry and before the slow lighting
pass:

```bash
grep "BuildZoneInfo:" System/UnrealEd.log | tail -1
# Log: BuildZoneInfo: 15 ZoneInfo actors, 2 duplicates, 0 zoneless
```

"duplicates" **is** the merge count. Zero means every zone survived.

**The fix is Portal Bias** in the Rebuild dialog, and counter-intuitively you **lower it,
not raise it** — the default is 70; 50 and 1 have worked where 100 did not. A higher bias
makes portals cut through adjacent geometry, and that extra cutting is what breaks the
seal.

Zone *indices* shuffle between builds because CSG numbers them in build order, so always
compare which `ZoneInfo` each index maps to, never the numbers.

### Surface count is not a damage signal

CSG re-merges coplanar surfaces, so the count drifts on its own — most maps land within
2%, and a -10% change has been verified as perfectly fine. Judge an import by **actor
counts and NULL materials**, not surface count.

### An import can fail partway through

The editor can start logging `Can't find file for package <X>` for packages that are
present on disk; every reference after that point comes back NULL, and on save the editor
**destroys** each `StaticMeshActor` and `Mover` whose mesh is NULL. The map still opens
and plays, quietly missing hundreds of meshes.

It is intermittent and the cause is not known — the same file has imported cleanly
minutes earlier. **Retrying the import is the fix.** Check the editor's own
`Maps/Auto<n>.ut2` autosaves before overwriting; one of them may hold the last good
import.

## Terrain does not survive an export

Terrain **in** a generated `.t3d` is fine. Exporting terrain **out of** a built map is
not: the map carries a separate `TerrainSector` object that `batchexport ... Level T3D`
does not write, so the import has a `TerrainInfo` pointing at a heightmap with no sector
data, and the editor faults the first time it draws the level.

This is the format's limit, not a pipeline bug — importing the *stock* map's own
unmodified export reproduces the identical crash. Establish that with a control import
before blaming a conversion.

To carry it yourself, read the heightmap and each layer's alpha map out of the source
package and re-import them:

```
#exec TEXTURE IMPORT NAME=x GROUP=Terrain FILE=Terrain\x.bmp MIPS=off
#exec TEXTURE IMPORT NAME=x GROUP=Terrain FILE=Terrain\x.tga ALPHA=1 MIPS=off
```

A G16 heightmap only comes from the 16-bit BMP path, and `MIPS=off` matters because these
are read as data, not drawn. Then Rebuild All.

## Saving a map can empty the gametype list

**Symptom:** the game starts, the menu appears, and there are no gametypes at all.

**Cause:** `System/CacheRecords.ucl` is the master cache the menus read, and **UnrealEd
rewrites it when saving a map and can drop all 12 `Game=` records** while leaving every
other record type intact.

```bash
grep -c "^Game=" System/CacheRecords.ucl    # must be 12
```

Zero means this. The 12 are all stock classes, so they copy cleanly from any working
install. The file is UTF-8 **with BOM**, one record per line, sorted by key.

## More

- `references/t3d-round-trip.md` — the full checklist and the package-rewrite technique
- `ut2004-packages` skill — building the content a map references
