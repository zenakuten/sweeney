# Limits that UnrealScript cannot work around

Each of these was investigated properly and abandoned. Knowing them up front is worth
more than rediscovering them.

## Third-person pawn animation cannot be improved from script

Investigated for WSUTComp and dropped. Three separate blockers, all in the native
engine:

**Animations are derived on the client, not replicated.** With `bPhysicsAnimUpdate`
set, the engine runs the whole locomotion state machine on each client from the
replicated `Velocity`, `Rotation` and `Physics`. It calls the direction and animation
functions natively, so overriding them in script changes nothing. A dedicated server
never has a pose at all.

**Script replication on a `Pawn` is rate-limited to non-owners.** The engine skips the
script property loop for a Pawn when the last full update was under ~0.09s ago, so
script variables on a Pawn reach other players at roughly 11Hz however often you set
them. Only the engine's own compressed position rides the fast path — meaning that
turning compressed position *off* drops position updates to that same low rate. Owning
clients are exempt; it is only other players' views that are capped.

**Vector and rotator replication is quantised regardless.** Vectors are rounded to
integers and rotators reduced to a byte per axis by the network serialiser, for every
such property. Replicating position data "uncompressed" gains nothing; full precision
needs component-wise floats or ints.

Do not chase the wrong magnitude: velocity rounding is about 0.1% at running speed,
well under a tenth of a degree of direction error. The visible losses are byte-quantised
rotation and view pitch (1.40625° steps), client-side *guessing* of `Physics`, fabricated
`Acceleration`, and dead reckoning with no interpolation.

**The one lever left** is client-side smoothing of `Velocity` and `Rotation` in a
`Pawn` subclass's `Tick`, which runs immediately before the native animation update in
the same frame, so values set there feed it. Zero bandwidth, no rate problem. Never
implemented.

Anything better requires changing the engine *and shipping it to clients*: the wire
format is symmetric and the animation code is client-side, so a server-only change
cannot help. That is what killed the idea.

## What this means in general

Before promising an online fix, establish which side the behaviour actually runs on:

- **Server-side and authoritative** — fixable from a mutator.
- **Client-side, driven by replicated state** — you can only change what is replicated,
  or post-process it on the client if you can get code there.
- **Native, client-side, and not script-dispatched** — not fixable from script at all.

The third case looks like the second until you check, which is what makes it expensive.
