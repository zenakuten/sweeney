---
name: ut2004-netcode
description: UT2004 / Unreal Engine 2 replication — roles, replication blocks, reliable and simulated functions, relevancy, and how to run code on a client from a server-side mutator. Use when a mutator works offline but not online, when an actor or variable does not reach the client, when deciding what runs server-side vs client-side, or when a change must be tested on a dedicated server.
---

# Replication (UT2004 / UE2)

The one sentence that explains most online bugs: **the server is authoritative, and a
client only sees what the server chose to send it.** Offline, everything is one machine
and every bug here is invisible. A mutator that works in a practice session and fails on
a dedicated server has almost always assumed otherwise.

Read the engine source for specifics — `Engine/Actor.uc` carries the role enum and the
replication block every actor inherits.

## Roles

```unrealscript
enum ENetRole
{
    ROLE_None,              // no role at all
    ROLE_DumbProxy,         // dumb proxy of this actor
    ROLE_SimulatedProxy,    // locally simulated proxy
    ROLE_AutonomousProxy,   // locally autonomous proxy
    ROLE_Authority,         // authoritative control
};
var ENetRole RemoteRole, Role;
```

`Role` is what *this* machine has; `RemoteRole` is what the other end has. On the server
an actor is normally `Role=ROLE_Authority` with `RemoteRole` describing the client's
copy. **An actor with `RemoteRole=ROLE_None` is never sent to clients at all.**

> `Info` defaults to `RemoteRole=ROLE_None`. A subclass that needs to reach clients must
> set it — and must write the enum *by name*. `RemoteRole=2` is silently discarded by
> UCC and leaves it at `ROLE_None`, which is the single most common cause of "my actor
> never replicates". See the `unrealscript` skill.

## What actually gets sent

A variable is replicated only if a `replication` block says so, the condition holds, and
the actor is relevant to that client:

```unrealscript
replication
{
    reliable if ( bNetInitial && (Role == ROLE_Authority) )
        NextReplicationInfo;
}
```

- `Role == ROLE_Authority` — only the authority sends. Nearly every condition includes it.
- `bNetInitial` — the actor's first update to that client. Right for values that never
  change; wrong for anything later.
- `bNetOwner` — that client owns the actor. Used to send a value to one player only.
- `reliable` — resent until acknowledged, and ordered. `unreliable` may be dropped.
- Replication is **server to client for variables**, and function calls go the direction
  their specifier names.

Variables replicate only from the authority downward. To send information *up*, a client
calls a `reliable server` function on an actor it owns — typically its
`PlayerController`.

## Running client-side code from a server-side mutator

A mutator runs on the server. The standard way to reach a client is a
`LinkedReplicationInfo` hung off the player's `PlayerReplicationInfo`:

```unrealscript
// Engine/PlayerReplicationInfo.uc
var LinkedReplicationInfo CustomReplicationInfo;   // for use by mod authors
```

In `Mutator.CheckReplacement`, spawn your `LinkedReplicationInfo` subclass owned by
`PRI.Owner` and link it into the `CustomReplicationInfo` chain. Client functions are
declared:

```unrealscript
reliable if ( Role == ROLE_Authority && bNetOwner )
    ClientDoThing;
```

On the client, reach the local player with `Level.GetLocalPlayerController()` — this
makes the same code work on a remote client and on a listen-server host.

Unlinking is automatic: the `PlayerController`'s `Destroyed()` tears down the whole
chain.

**The package must reach the client.** Set `bAddToServerPackages=True` in the mutator's
`defaultproperties` rather than adding a `ServerPackages=` ini line —
`Engine/Mutator.uc` handles the registration at load time.

## Client package delivery is not your bug

Clients receive the server's `.u` files into the **client's `Cache/` folder, named by
cache-key GUID** — not `System/<Package>.u`, and not under the package name at all.

So `ls ClientInstall/System/MyMod.u` returning nothing is the expected, working state.
Do not conclude the package failed to transfer, and do not "fix" it by copying `.u`
files into the client's `System/`. Debug the actual client-side path — interaction
registration, replication conditions, client config — starting from the client log.

## Count rosters from ControllerList, not bookkeeping

`Level.ControllerList` is maintained natively: a Controller is added in its constructor
and removed in its destructor, so it is always an accurate roster.

`TeamInfo.Size` is plain script bookkeeping and **drifts**. Stock `TeamGame.ChangeTeam`
clears a spectator's `PRI.Team` with no matching `RemoveFromTeam`, leaking a count
permanently, and it is stale during teardown because `RemoveController()` runs before
`RemoveFromTeam`. A stale `Size` in `PickTeam` sends joiners to the team that is already
bigger, stacking teams over a map.

Walk `Level.ControllerList` and skip `PRI == None`, `PRI.Team == None` and
`bOnlySpectator`. A Controller mid-join is on the list with `Team == None`, so it is
correctly excluded.

## Limits worth knowing before you start

Some things cannot be fixed from UnrealScript. See
`references/what-script-cannot-do.md` — most importantly that third-person pawn
animation is derived client-side from replicated movement rather than replicated, and
that script replication on a `Pawn` is rate-limited to non-owning clients. Both have
cost real time to rediscover.

## "Only the remote client is broken" is a diagnosis, not a symptom

When a fault appears for a network client but **not** offline, **not** for the
listen-server host and **not** for bots, the map data is already ruled out — every side
loads the same file. What differs is the question being asked of it.

Ordinary movement is swept: `MoveActor` traces from A to B. A network client additionally
*places* its pawn, because `ClientAdjustPosition` calls `SetLocation`, and only an
autonomous proxy ever runs that path. Geometry that answers those two questions
differently — a collision hull that encloses no volume is the case seen in practice —
breaks the client alone. `SetLocation` fails, the correction is never applied, the client
falls further behind every tick, and the player warps between two positions.

Reach for this before diffing map data. Ask who is affected first: host and bots are
server-side and unpredicted, so if they are clean the server's collision is clean.

Instrumenting beats guessing here. A temporary probe in the player controller that traces
the player box when a correction cannot be applied, and logs the actor it hits, named the
culprit in one run after days of static comparison found nothing. See the
`ut2004-maps` skill for what the culprit turned out to be.

## Testing

Online behaviour must be tested online — a practice session proves nothing about it.
Use the `ut2004-live-testing` skill.

## More

- `references/what-script-cannot-do.md` — the hard limits, and why
- `references/client-side-patterns.md` — the `LinkedReplicationInfo` pattern in full,
  custom character skins, and other client-delivery mechanics
