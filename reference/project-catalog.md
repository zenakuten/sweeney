# Prior art

Mods and tools that solved particular problems. **This is prior art, not engine
behaviour** — how one project chose to solve its own problem. Point at it when the same
problem comes up; do not present its design as a fact about UT2004.

Paths are on the author's machine and may not exist elsewhere. Where a project is public,
that is noted.

## Mods

**WSUTComp** — the base mod the others depend on. Enhanced net code, brightskins,
hitsounds, a Clan Arena gametype, and a large custom GUI. Entry class `MutUTComp`.
Descends from UTComp via UTCompOmni (both on GitHub).

Worth reading for: a full custom GUI component and style library; scoreboards that scale to
64 players; `BS_xPlayer`'s use of the `PlayerReplicationInfo` chain. Its
`Docs/ping-compensation.md` is a thorough write-up of a rewind-and-fake-projectile latency
compensation system — **that is this mod's design, not engine behaviour**, but it is the
place to start if a similar system is ever wanted.

**WSUTCompWeaponConfig** — companion mod for changing weapon properties.

**WS3SPN** — TeamArenaMaster, ArenaMaster and Freon gametypes. `TAM_Mutator` derives from
`MutUTComp` to inherit WSUTComp's features. Worth reading for: the team balancer, which
equalises player *count* first and only uses score/Elo to pick who moves, and which counts
teams live from `ControllerList` rather than trusting `TeamInfo.Size`.

**WSZound** — an un-source-stripped Zound 5.5 with client-side muting. Worth reading for:
how a decompiled package was rebuilt and verified (name-table diffing against the original),
and for a self-contained copy of the WS GUI components with no dependency on WSUTComp.

**DarkWalker** — a UT3 vehicle ported to UT2004: procedural legged locomotion, bone control
from script, Karma tuning, custom effects. Its `DarkWalker.md` is a long engineering log.
The reusable engine findings are already in the `ut2004-packages` skill's
`vehicles-and-skeletal.md`; the gait and stepping design is specific to this vehicle.

**UT2004MCP** — a mutator that runs an MCP server inside a UT2004 server, so an agent can
query and control a live game. Covered by the `ut2004-live-testing` skill. Worth reading
for: JSON-RPC over HTTP implemented directly on `IpDrv.TcpLink` using the listen/accept
pattern from `UWeb.WebServer` (not UWeb itself — the stock web connection rejects
non-form-urlencoded POST bodies), and the `LinkedReplicationInfo` route for client-side GUI
actions.

## Tools

**ut3converter** — UT3 → UT2004 map and content conversion. See the `ut3-map-conversion`
skill. `FORMAT.md` documents UE2 map and package structure in depth.

**utupscaler** — rebuilds any map's textures through an image model, at any scale. See the
`ut2004-textures` skill. Its `utup/ue2.py` is a working UE2 package reader.

**roughinery** — the shipped 4K rebuild of DM-1on1-Roughinery, and utupscaler's worked
example. Consult for how a finished map turned out, not as the current pipeline.

## Where the general lessons went

Everything from these projects that generalises has been distilled into the skills. If you
find yourself reading one of these projects to answer a general question, the answer
probably belongs in a skill instead.
