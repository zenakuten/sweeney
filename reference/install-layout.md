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

## Which install is this?

There is no single "UT2004 install". Three flavours are in circulation, and a machine
often has several at once with different jobs.

| | |
|---|---|
| **Retail 3369** | the 2004 release. 32-bit tools. |
| **Windows 3374** | the community patch, Windows build. 64-bit `UCC.exe`. |
| **Linux 3374** | the community patch, native Linux build. No `UCC.exe`. |

**Identify one from `System/`**, never from the folder name:

```
System/Build.ini   ->  [BuildVersion] Label=UT2004_v3374_[2026-07-18_18.30]_d1b145e1
                       [BuildVersion] Label=UT2004_Build_[2005-11-23_16.22]    retail
System/UCC.exe     ->  a Windows install (or a Windows install sitting on a Linux box)
System/ucc-bin     ->  the native binary; on its own, a Linux install
```

`file System/UCC.exe` then says 32- or 64-bit, which is the practical difference: **a
32-bit `UCC.exe` hangs building large packages.** That is the reason to be on 3374 rather
than retail.

### "3374" is not one thing

The patch is maintained, so there are many 3374 builds and an old one is genuinely old.
**Nothing in the install names the patch level** — the `pNN` in a folder name is the
owner's own convention, not something the game records. What `Build.ini` does give is a
build **date** and **commit**, and those order reliably:

```
UT2004_v3374_[2026-07-18_18.30]_d1b145e1     newer patch
UT2004_v3374_[2026-03-25_08.17]_47d00acd     older patch, same 3374
```

Two installs both reading "v3374" can be months apart. When it matters which one you are
in, compare the date — and note that two installs sharing a date and commit are the same
build, regardless of what their folders are called.

`scripts/setup.sh` picks the build install on capability first and build date second, so
it prefers the newest 3374 rather than whichever sorts first. The chosen one's
`build_version`, `build_date` and `build_commit` are in `~/.sweeney/config.json`.

## Building on Linux needs a Windows install

For development on Linux, **`UCC.exe` under Wine is preferred over the native `UCC`**.
That has a prerequisite people miss: the Linux machine needs a **Windows 3374
installation** of UT2004 present, and Wine installed. A Linux 3374 install alone will not
do — it ships no `UCC.exe` at all.

So a Linux modder typically keeps two installs: a Windows 3374 one to build in, and
whatever they actually play on. Do not assume the install you are standing in is the one
that can build.

## Several installs, different jobs

Roles worth telling apart, since they are usually different directories and sometimes
different machines:

- **build** — where the mods are compiled. Needs a usable compiler.
- **play / client** — where you connect from, and where client logs and screenshots land.
- **server** — dedicated or listen; may be either of the above or neither.
- **retired** — older installs kept around. Their logs are stale and will mislead.

`scripts/setup.sh` surveys every install it can find, prints what each one is, and
records the best build install as `install_root` and a play install as
`client_install_root` in `~/.sweeney/config.json`. Check those rather than guessing, and
say which install you mean when it could be ambiguous.

**Never generate content into one install and build in another.** It silently rebuilds
stale inputs.

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
