---
name: ut2004-live-testing
description: Testing UT2004 mods against a running server — the UT2004MCP in-game MCP server, driving a real client, reading server and client logs, and server configuration. Use when a change needs verifying in game, when a mod works offline but not online, when reading server.log or UT2004.log, when adding bots or switching maps for a test, or when changing server config.
---

# Testing against a running server

Online behaviour cannot be verified offline. A practice session is one machine, and every
replication bug is invisible there. Test on a real server, and read the logs.

## UT2004MCP

`UT2004MCP` is a mutator that runs an MCP server **inside** the UT2004 server, so an agent
can query and control the running game without a player slot. Load it with:

```
?Mutator=UT2004MCP.MutMCP
```

It listens on TCP **6900** by default (`ListenPort` in `System/MCP.ini`), separate from the
game port, and **has no authentication** — firewall it and treat it as LAN-only.

| Tool | Args | Does |
|---|---|---|
| `query_game_info` | — | gametype, map, player counts, score and time limits |
| `list_players` | — | connected players: score, deaths, ping, team, flags |
| `say` | `message` | broadcast to all players |
| `switch_map` | `map` | travel all players to a map (name or URL) |
| `kick` | `player` | kick by exact name |
| `add_bot` / `remove_bots` | `count?` | add or remove bots |
| `player_input` | `player`, `command` | run a bound command as a player (`Jump`, `Fire`, `MyMenu`) |
| `screenshot` | `player` | capture a player's view to `ScreenShots/ShotNNNNN.bmp` |
| `gui_click` | `player`, `caption` | click a menu button on a player's client |

Quick check without a client:

```bash
curl -s -X POST http://localhost:6900/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Run it as a listen server

`screenshot` writes the file on the machine **the player's client runs on**. So run UT2004
as a listen server on the same machine as the agent, and screenshot the host player —
otherwise the images are on someone else's disk and unreachable.

### Driving a client

`player_input` + `screenshot` + `gui_click` together let you read and operate a real
client: screenshot to see the screen, click a button by caption, screenshot again.

```bash
magick shot.bmp shot.png     # then read the png
```

A keybind is just a command, so `player_input` with `MyMenu` opens the mod menu.
`gui_click` is fire-and-forget — confirm with a screenshot rather than assuming.

Server-authoritative actions work for remote players; **movement does not**, because the
client's own moves overwrite it. Bots cannot screenshot.

### Rebuild means restart

The server loads the mutator's `.u` at startup. After a rebuild, **ask the user to restart
the server and wait for them to confirm** before connecting. Connecting first only reaches
the stale build and is misleading.

## Logs

| | |
|---|---|
| Server | `<server install>/System/server.log` |
| Client | `<client install>/System/UT2004.log` |

UnrealScript `log("...")` appears as `ScriptLog:` lines.

**Capture logs right after a test run** — they are overwritten when the client or server
restarts.

Give temporary diagnostics a **greppable prefix** — `log("MYTAG ...")` — so they can be
found in a log thousands of lines long, and stripped again afterwards.

Do not read logs from retired installs; they are stale and will mislead.

## Server configuration

**A Win64 server under Wine reads `System/ut2004-win64.ini`, not `UT2004.ini`.** The
server log's `Init: Ini:ut2004-win64.ini` line confirms which file is live. `UT2004.ini`
exists but is unused, so editing it has no effect.

Every server-side setting — `ServerPackages`, downloads and redirect, mutators, netspeed —
goes in the win64 ini. A characteristic failure: custom packages not reaching clients
because `ServerPackages=` was only ever added to `UT2004.ini`.

Both inis are **CRLF**; preserve `\r\n` when editing. Restart the server to load
`ServerPackages` changes.

> **Never edit a production server's config without asking.** Diagnose freely, then show
> the exact proposed change and wait for an explicit go-ahead — even in accept-edits mode,
> even when the fix is obviously correct.

## Bots

Random bot selection is weighted by each `.upl` record's `BotUse`. To restrict the roster
to a custom set, zero `BotUse` on the stock characters and leave the custom ones at 1 —
this works across every gametype and every voted map, with no per-map URL needed.

Note that stock `.upl` files are restored by a game reinstall or patch.

Bots getting the right **names** but wrong **skins** is a different problem — see the
`ut2004-netcode` skill on `PlayerRecordClass` packages.

## What to check when something works offline but not online

1. Is the actor actually replicated? `RemoteRole` written as an enum name, not an int.
2. Is the package on the client? `bAddToServerPackages=True`.
3. Is the code running on the side you think? See the `ut2004-netcode` skill.
4. Is the client running the build you just made, or a cached older one?
