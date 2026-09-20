# Getting code and content onto the client

## The LinkedReplicationInfo pattern

The supported hook is on `PlayerReplicationInfo`:

```unrealscript
var LinkedReplicationInfo CustomReplicationInfo;   // for use by mod authors
```

`LinkedReplicationInfo` is an abstract `ReplicationInfo` with a `NextReplicationInfo`
field, replicated on `bNetInitial`, forming a linked list per player.

**Server side**, in the mutator:

1. In `CheckReplacement` (or on login), spawn your subclass **owned by `PRI.Owner`** —
   ownership is what makes `bNetOwner` true for that player.
2. Link it into the player's `CustomReplicationInfo` chain rather than overwriting,
   so several mods can coexist.

**Client side**, declare functions as:

```unrealscript
reliable if ( Role == ROLE_Authority && bNetOwner )
    ClientDoThing;
```

and inside them reach the local player with `Level.GetLocalPlayerController()`. That
one detail is what makes the same code work for a remote client *and* for the host of
a listen server.

**Teardown is automatic.** The `PlayerController`'s `Destroyed()` tears down the whole
chain, so there is nothing to unlink by hand.

**The package has to be on the client.** Set `bAddToServerPackages=True` in the
mutator's `defaultproperties`; `Engine/Mutator.uc` registers the package at load time.
A `ServerPackages=` ini line is the older, manual alternative and is not needed.

## Custom character skins

Pushing a custom character to clients that do not have its `.upl` has exactly one
engine fallback, in `XGame/xUtil.uc`'s `FindPlayerRecord`:

```unrealscript
DynamicLoadObject(CharName $ "mod." $ CharName)
```

So the engine looks for a package named **`<CharName>mod`** containing a class
**`<CharName>`** — a `PlayerRecordClass` subclass — **one package per character**.
`xPawn.PostNetReceive` calls this; if the record comes back with `Species == None`, the
pawn falls back to the default character, which is the symptom when it is wrong.

Two rules follow:

1. **One package per character, never a bundle.** A single combined package holding
   every class is never looked up, because the name is derived from the character name.
2. **`<CharName>` must be a valid class identifier**, so no hyphens. `Kung-Lao` has to
   become `KungLao` in the class name *and* in the `.upl` `DefaultName`.

The `<CharName>mod` packages and the mesh/texture packages all go in `ServerPackages` so
clients download them.

Note that bot *names* replicate directly via `PRI.CharacterName` and work even when the
skin lookup fails — hence the characteristic "right names, wrong skins".

## Where the client's copy of a package lives

In the client's `Cache/`, named by cache-key GUID. Not `System/<Package>.u`. See the
SKILL — this repeatedly sends debugging down the wrong path.
