# Glossary

Terms that mean something specific in UT2004 / Unreal Engine 2.

**`.uc`** — UnrealScript source. Latin-1, compiled by UCC into a `.u`.

**`.u`** — a compiled code package. Also carries any content `#exec`'d into it.

**`.utx` / `.usx` / `.uax` / `.ukx`** — texture, static mesh, sound and animation packages.
Built as a `.u` and renamed on the way to its folder.

**`.ut2`** — a map. **`.t3d`** — a *text* export of a map or of objects in it. The text
format cannot represent everything a `.ut2` holds.

**`.upl`** — a character record file: name, mesh, voice, `BotUse` weighting.

**`.ucl`** — a cache record file. `CacheRecords.ucl` is what the menus read.

**`.uz2`** — a compressed package for a redirect server.

**UCC** — the command-line tool: `make` compiles, `batchexport` exports, `compress` makes
`.uz2`, `server` runs a dedicated server.

**Mutator** — a server-side class that hooks gameplay: `CheckReplacement` to swap actors in
as they spawn, `ModifyPlayer`, `Mutate` for console commands. The standard entry point for
a mod.

**GameInfo** — the gametype. Exists only on the server. **GameReplicationInfo (GRI)** is its
replicated shadow, which clients do see.

**Controller / Pawn** — a Controller is the mind (player or bot), a Pawn is the body. A
Controller persists across deaths; Pawns do not. `Level.ControllerList` is the reliable
roster.

**PlayerReplicationInfo (PRI)** — per-player replicated state: name, score, team, ping. Its
`CustomReplicationInfo` field is the supported hook for a mod's own per-player replicated
data.

**LinkedReplicationInfo** — an abstract ReplicationInfo forming a per-player linked list,
the route for running client-side code from a server-side mutator.

**Role / RemoteRole** — what this machine and the other end have over an actor.
`ROLE_Authority` decides; `ROLE_None` means it is never sent.

**Relevancy** — the server's decision about whether a client needs to know an actor exists
at all. `bAlwaysRelevant` opts out.

**Interaction** — a client-side input/render hook, how a mod draws or captures keys on the
client.

**Canvas** — the immediate-mode drawing surface for HUD and scoreboards.

**GUI2K4 / XInterface** — the menu framework, and the styles that skin it.

**BSP / CSG** — the brush-built solid geometry. Brushes are added and subtracted; a
**Rebuild** re-runs CSG and renumbers things.

**Zone / ZoneInfo / zone portal** — a region of the map with its own ambient light, sound,
fog and gravity, separated by portal surfaces. Zones are an attribute layer, not geometry.

**Mover** — an animated brush or static mesh: lifts, doors. A Mover with
`DrawType=DT_StaticMesh` draws a mesh and needs its `Skins()` like any other actor.

**Static mesh** — prebuilt geometry placed in a map. Its `CollisionModel` (an **MCD** hull)
is a separate object beside it, and `UseSimpleBoxCollision` decides whether that hull or the
exact triangles are used.

**Karma** — the rigid-body physics used by vehicles and ragdolls. **Karma units are not
Unreal units**: 1 Karma unit = 50uu.

**Emitter / ParticleEmitter** — effects. A `MeshEmitter` takes its material from the
Emitter actor's `Skins[0]`.

**Shader / Combiner / TexPanner / FinalBlend** — materials that compose other materials.
Collectively "composites": they cannot be upscaled, only rebuilt.

**`#exec`** — a directive in a `.uc` that runs at compile time to import content or load a
package. It reaches the editor's exec handler only — `POKE` is not available from it.

**`defaultproperties`** — the class's default values, written at the end of a `.uc`. Enum
values must be written by name; an integer is silently discarded.

**ServerPackages** — packages a server sends to clients. A mutator sets
`bAddToServerPackages=True` instead of an ini line.

**Redirect** — an HTTP server holding `.uz2` files so clients download from it rather than
the game server.

**umodel** — a third-party package viewer/exporter. Useful for dumping properties and
exporting meshes that `batchexport` refuses.
