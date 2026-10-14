---
name: ut2004-live-testing
description: Testing UT2004 mods against a running server — the UT2004MCP in-game MCP server, driving a real client, reading server and client logs, profiling script with PROFILESCRIPT, and server configuration. Use when a change needs verifying in game, when a mod works offline but not online, when reading server.log or UT2004.log, when adding bots or switching maps for a test, when changing server config, or when measuring the script cost of a change or finding what is slow.
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
| `profile_script` | `action` | `start`, `stop` or `reset` the script profiler on the server (see below) |

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

## Profiling script

The engine has a built-in UnrealScript profiler, driven by a console command:

| Command | Does |
|---|---|
| `PROFILESCRIPT START` | begin recording every script function call and engine event, with its time |
| `PROFILESCRIPT RESET` | discard what has been recorded so far, keep recording |
| `PROFILESCRIPT STOP` | write `ut2004-<date>_<time>.uprof` into `System/` and stop |

It profiles **the process that runs it**. Typed in a client console, it measures that
client's script (HUD, menus, client-side effects). For the server, use UT2004MCP's
`profile_script` tool, which sends the same command server-side. The file lands in
that process's `System/` folder, so the newest `.uprof` there is the capture.

Read it with `scripts/uprof.py`:

```bash
python3 <sweeney>/scripts/uprof.py                       # newest capture in the install
python3 <sweeney>/scripts/uprof.py --client              # newest in the client install
python3 <sweeney>/scripts/uprof.py -m WSUTComp           # only paths matching a regex
python3 <sweeney>/scripts/uprof.py --packages            # self time per package
python3 <sweeney>/scripts/uprof.py --func MutUTComp.Tick # callers and callees
python3 <sweeney>/scripts/uprof.py --compare before.uprof after.uprof
```

Each row is one function as `Package.Class.Function` (with the state between class and
function for state code): **self** time in the function itself, **incl** time with
everything it called, **calls**, and the same per **frame** (one server or client tick).
Compare captures per frame, since no two captures are the same length.

What it does and doesn't see, as measured on a capture:

- **Natives are not recorded.** `InStr`, `Mid`, `VSize` and the like never appear;
  their cost is in the self time of the script function that called them. A function
  with high self time and few lines is usually a native-heavy one.
- Only script. Rendering, physics, collision and replication cost nothing here, so a
  slow frame with little script time is somewhere else.
- Clock resolution under Wine is 0.1 us (the header prints it). Times of a few
  tenths of a microsecond per call are noise.
- A dedicated server with no human connected never starts the match: the bots don't
  spawn and the capture is the idle server. Have a player connected, or the profile
  measures nothing of the gameplay.

To measure a script change: capture the same situation (same map, same bot count,
similar length) before and after, `RESET` just as the situation starts so loading and
warm-up are not in it, and run `--compare`. The `.uprof` is also the input to Epic's
`ueScriptProfiler.exe` viewer, where it is installed.

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
