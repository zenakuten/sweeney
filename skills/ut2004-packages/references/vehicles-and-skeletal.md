# Vehicles, Karma and skeletal meshes

General engine behaviour, drawn from a UT3 → UT2004 vehicle port. The port's own gait
and stepping design is not here — it is specific to that vehicle.

## KarmaParams are in Karma scale, not Unreal units

`KCOMOffset` and other `KarmaParams` distances reach Karma by a raw copy with **no length
scaling**, so **1 Karma unit = 50 Unreal units**. Stock vehicles are all fractions —
`ONSRV` is `(X=-0.25, Z=-0.4)`.

Rule of thumb: any `KCOMOffset` component outside `-1..1` is probably a units mistake.

**Exception:** tall hovering or legged vehicles deliberately hang the centre of mass far
below their contact points, to act as a self-righting pendulum — values of -10 to -20.
`bKStayUpright` is not a substitute; raising `UprightStiffness` to 20000 still leaned
badly.

**`KMass` in defaults is never the real mass.** `SVehicle` overwrites it at runtime with
`KSetMass(VehicleMass)`. That makes a `KAddImpulse` velocity `Impulse / (100 * VehicleMass)`.

## Bone control from script

More capable than it looks. `GetBoneCoords` reads the animated pose;
`SetBoneRotation`, `SetBoneDirection`, `SetBoneLocation` and `SetBoneScale` write
modifiers applied **after** animation evaluation, each with its own alpha, up to 256 of
them.

Three things that are not obvious:

- **`SetBoneRotation`'s `Space` argument does nothing.** It is always the local
  composition. Only `SetBoneDirection` honours its `Space`.
- **`SetBoneDirection` replaces** the bone's world orientation rather than adding to it,
  so it wipes any animation on that bone. `SetBoneRotation` composes with the animation,
  and is usually the right tool.
- **You cannot iterate a solver against the engine within a frame.** A written rotation
  does not appear in `GetBoneCoords` until the next evaluation, so CCD or FABRIK would
  need forward kinematics written in script. A single-step controller works instead:
  `GetBoneCoords` already includes last frame's correction, so what it measures is the
  residual error, and accumulating that converges rather than chasing.

**`SetBoneRotation`'s delta is in the bone's own frame**, not the parent's. This is easy
to get wrong and the error hides: small corrections that are similar on every limb look
right in the parent frame too. It shows up when a shared parent bone is involved, because
a delta expressed in it lands differently on each child according to which way that child
points.

**A second `SetBoneRotation` on the same bone replaces the first, it does not add.** Where
one bone carries both yaw and pitch, compose the two angles into a single rotator.

## Bone rotations from an aim are negated

The engine's own convention, seen in the native code that aims a weapon's mesh:

```unrealscript
SetBoneRotation(YawBone,   FRotator(0, -CurrentAim.Yaw, 0), 0, 1)
SetBoneRotation(PitchBone, FRotator(-CurrentAim.Pitch, 0, 0), 0, 1)
```

while the surrounding coordinate maths uses `+CurrentAim`. The flip belongs to
`SetBoneRotation`, not to any particular vehicle. **Anything driven from an aim needs the
same negation.**

This can hide for a long time if the bone being rotated carries little geometry —
rotating a 44-point leaf bone is invisible. The first visibly bone-driven thing is where
the sign error surfaces.

## Overriding a state can silently delete an implementation

A class may declare a function as an **empty stub at class scope** and implement it only
inside one of its states. Replacing that state replaces the implementation with the stub,
and calls to it quietly do nothing.

That is exactly what happened to a hit-effect function: the beam damaged enemies without
leaving a mark on them, with no error anywhere.

**Before overriding a state, check what the stock state actually carries.** Anything it
implemented is gone unless you restore it.

## Holding a reference to an effect means owning its lifetime

Several stock effects destroy *themselves* on a condition — a beam effect's `Tick` opens
by destroying itself when its instigator has no controller. If a weapon holds references
to effects across a burst, a driver leaving mid-burst makes those effects vanish while
the weapon still points at them, and the engine trips over the dead actors:

```
Critical: CheckDeleted
Critical: ULevel::CleanupDestroyed
Critical: ULevel::Tick
```

Two rules:

- **If something holds a reference to an effect, that something owns when it dies.** Make
  the effect not self-destruct, and clear it from every exit path — state labels,
  `EndState`, and `Destroyed`.
- **Check `bDeleteMe`, not just `!= None`.** A reference is not cleared the instant an
  actor is destroyed; it survives until the level's next cleanup pass. In between it is
  non-None and already dead, and calling `Destroy` on that turns a stale reference into a
  crash.

## Projectors do not belong on moving actors

A scorch decal is a `Projector`, so one placed on a vehicle or a player projects onto
whatever moves out from under it. Keep decals to world geometry and use a hit effect for
actors — which also puts it on the damage cadence rather than a per-frame visual trace.
