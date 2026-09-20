# UT2004 install layout

## The folders that matter

```
<install>/
  System/           UCC.exe, the .u packages, ini files, logs
  Maps/             .ut2 maps, plus the editor's Auto<n>.ut2 autosaves
  Textures/         .utx texture packages
  StaticMeshes/     .usx
  Sounds/           .uax
  Animations/       .ukx
  Cache/            packages downloaded from servers, named by GUID
  <ModName>/
    Classes/        .uc source -- this is what UCC compiles
    Textures/       source art for #exec TEXTURE IMPORT
```

Note the asymmetry with the **engine script reference checkout**, which is flat —
`<Package>/<Class>.uc`, no `Classes/` directory. Install source lives in
`<Package>/Classes/`.

## Ini files

- `System/UT2004.ini` — the game and the **editor**. `EditPackages` under
  `[Editor.EditorEngine]` controls what `ucc make` builds. `[Core.System]` `Paths=` lines
  set package search order.
- `System/ut2004-win64.ini` — **what a Win64 server actually reads.** `UT2004.ini` exists
  alongside it and is ignored by that server. Confirm from the log's `Init: Ini:` line.
- `System/User.ini` — keybinds and input.
- `System/MCP.ini` — `UT2004MCP`'s `ListenPort`.
- `System/CacheRecords.ucl` — the master cache the menus read for gametypes, mutators,
  weapons. UTF-8 **with BOM**.

Both game inis are **CRLF**. Preserve `\r\n` when editing.

## Package search order

`[Core.System]` lists `Paths=../System/*.u` **before** `Paths=../Textures/*.utx`. A `.u`
left in `System/` shadows a `.utx` of the same package name, and the engine silently loads
the stale one. Always `mv` build output into place, never `cp`.

## Dev, server and client are separate installs

A working setup commonly has three roles, sometimes on different machines:

- **development + dedicated server** — where the mods are built and run
- **client** — where you connect from, and where client logs and screenshots land
- **retired installs** — keep none in play; their logs are stale and will mislead

**Never generate content into one install and build in another.** It silently rebuilds
stale inputs. Keep all work in one install.

`~/.sweeney/config.json` records `install_root` and, if set, `client_install_root`.

## Platform

UT2004 is a Windows game. Most installs, most servers and nearly all modders are on
Windows — assume that unless what you can see says otherwise.

Linux hosts exist, and there is a native Linux binary, but running the Windows build
under Wine is common and is what the 64-bit community patch provides. When that is the
setup:

- `UCC.exe` is still a Windows program, so **its arguments must be Windows paths** —
  backslashes, and absolute paths through Wine's drive mapping (`Z:\home\you\...`).
  Passing a Unix path usually produces a misleading error rather than a clear one.
- `.bat` wrappers beside a mod's source do not run; read them for intent.
- A Win64 server reads `ut2004-win64.ini`, not `UT2004.ini` — see below.

## UCC.exe is not one binary

`UCC.exe` is community-patched and differs between builds — a 32-bit and a 64-bit build
have different sizes, architectures and build dates. **A 32-bit UCC has about 2GB of
address space and hangs building large packages**, which covers every package
`EditPackages` names, not just those being compiled, because UCC loads already-built
packages to resolve references.

Setup records `ucc_bits` and `ucc_id` so a claim about compiler behaviour stays attached to
a specific binary. To measure behaviour rather than assume it, use `scripts/ucc-probe.sh`.

## Logs

| | |
|---|---|
| Server | `System/server.log` |
| Client | `System/UT2004.log` |
| Editor | `System/UnrealEd.log` |

`log("...")` from UnrealScript appears as `ScriptLog:`. Logs are overwritten on restart, so
capture them straight after a run.

## Production servers

A live server's config is production. Read and diagnose freely; **propose** changes and
wait for an explicit go-ahead before writing, even when the fix is obviously correct.

A local `liveserver/`-style directory holding a remote server's config is the same thing at
one remove — the user edits and uploads it.
