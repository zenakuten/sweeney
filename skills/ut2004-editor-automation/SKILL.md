---
name: ut2004-editor-automation
description: Driving UT2004 UnrealEd from an agent through the external UnrealEd command bridge — issuing editor console commands, reading their output, and automating map and content workflows. Use when controlling a running UnrealEd, sending OBJ/MAP/ACTOR/BSP/LIGHT/PATHS commands, or building an MCP service around editor automation.
---

# Automating UnrealEd

UnrealEd does not run ordinary placed actors through the gameplay lifecycle while
editing. A placeable UnrealScript actor therefore cannot start a `TcpLink` listener in
edit mode. If the actor starts after **Play Level**, it is running in the game process,
not controlling the editor.

Use the external command bridge instead. `unrealed-send.exe` finds the running
`UnrealEd.exe`, locates the bottom-bar **Command** control, attaches to the editor input
thread and enters one editor command. It uses the control's HWND rather than mouse
coordinates.

## Locate the bridge

Setup records it in `~/.sweeney/config.json`:

```bash
BRIDGE=$(python3 -c "import json,os;print((json.load(open(os.path.expanduser('~/.sweeney/config.json'))).get('tools') or {}).get('unrealed_send') or '')")
```

If the value is empty, the bridge has not been installed or discovered. Do not guess a
path; tell the user to install UT2004MCP and rerun Sweeney setup, or set
`tools.unrealed_send` by hand.

On Linux, run the Windows helper in the same Wine prefix as UnrealEd:

```bash
wine "$BRIDGE" "OBJ LIST CLASS=LEVEL"
```

On Windows, invoke the `.exe` directly. The helper briefly brings UnrealEd to the
foreground because Wine does not reliably dispatch UnrealEd's C++ control callbacks for
cross-process `WM_COMMAND` or `WM_KEYDOWN` messages alone.

## One editor process per map

**Always restart `UnrealEd.exe` before working on a different map.** Do not close one map
and reuse that editor process for the next.

This old editor retains state that is not safely isolated between maps: the shared
`MyLevel` package name, loaded packages and object handles can survive or become stuck.
A command that appears to target the new map can therefore resolve an object left over
from the previous one. Save and close the current map, terminate that specific
`UnrealEd.exe` process, start a fresh editor, and only then load or import the next map.

## Build options are dialog state

Do not try to set UnrealEd's initial **Build Options** through an ini file or by editing
class defaults. The C++ editor code hardcodes the values loaded when the dialog opens, so
those edits do not change the effective build.

For any non-default build setting, open the **Build Options** dialog and change the
control there before starting the build. Automation of build settings must drive that
dialog and verify its displayed values; sending a rebuild command after changing an ini
is not equivalent.

Why, concretely: `FRebuildTools::Init` copies `Current` from a hardcoded
`FRebuildOptions()` whose `LightmapFormat` is `TEXF_DXT3`, and only *then* reads
`[Rebuild Configs]` out of `UnrealEd.ini` — and those are named presets for the dialog's
list, never `Current`. `LightmapFormat` is assigned in exactly one place in the tree, the
Build Options property sheet. There is no exec command for it, so **the bridge cannot set
the lightmap format**; a person has to pick it. It is also dialog state, so it reverts to
DXT3 on every editor restart — set it *after* the last restart, not before.

The **Build All** button is these five execs in order, all of which the bridge can send:

```text
MAP REBUILD
BSP REBUILD
LIGHT APPLY
PATHS DEFINE
FLUID REBUILD
```

`PATHS DEFINE`, not `PATHS BUILD` — the latter erases the whole path network and
regenerates it from scratch.

## Read the log window, not the log file

`System/UnrealEd.log` lags the log **window** by an unbounded amount — a command can be
visibly finished in the window with nothing on disk, and the file then flushes several
commands' output at once. Polling the file therefore cannot tell a slow command from one
that was never delivered.

Start the editor with **`-log`** so the log window opens at once (it also skips the splash
screen, which helps under Wine), and read the window's text with `unrealed-log.exe`, the
companion recorded as `tools.unrealed_log`:

```bash
wine "$LOGTOOL" -tail 20
```

It finds the `UnrealEdUnrealWLog` top-level window of `UnrealEd.exe` and dumps its
`UnrealEdUnrealWEditTerminal` child through `WM_GETTEXT`.

## Prefer window messages to keystrokes

`unrealed-send.exe` types into the Command control, which means it needs the keyboard
focus and its keystrokes can be dropped. Both problems disappear if the editor is driven
by message instead, which is what `unrealed-ui.exe` (`tools.unrealed_ui`) does:

```bash
wine "$UI" --exec "MAP SAVE FILE=\"..\Maps\DM-Deck4K.ut2\""
wine "$UI" --lightmap RGB8          # the combo handler, which is what commits it
wine "$UI" --build                  # the Build Options Build button
wine "$UI" --cancel-dialogs         # dismiss a Save As or Map Check dialog
wine "$UI" --close WBuildPropSheet
```

`--exec` works because the bottom bar subclasses the edit inside the Command combo and,
on `WM_KEYDOWN`/`VK_RETURN`, reads the control's text and calls `GUnrealEd->Exec` on it
(`UnrealEd/Inc/BottomBarStandard.h:74`). Setting the text and posting that one key needs
no focus at all, cannot be dropped, and `SendMessage` does not return until the editor has
finished running the command — so it is its own completion signal, which no amount of log
polling gives you. The same trick reaches the **Build Options dialog**, which keystrokes
never can: Epic's `WWindow` dispatches a control notification sent to the control's parent
to that control's delegate, so `WM_COMMAND(MAKEWPARAM(id, code), hwnd)` runs exactly what
a click runs. Control IDs are in `UnrealEd/Src/res/resource.h`.

Reserve `unrealed-send.exe` for cases where a real keystroke is wanted.

## File > Save always prompts

`MAP LOAD` does not give the level a save filename — the log says "New File, Existing
Package" — so the **File > Save** menu item opens a Save As dialog every time, which then
blocks the editor. Use `MAP SAVE FILE="..\Maps\<Name>.ut2"` instead, which names the
target explicitly, and `--cancel-dialogs` if a prompt is already up.

## Confirm every command; the editor drops them silently

**A command sent while UnrealEd is busy is lost.** The injected keystrokes go nowhere,
`unrealed-send.exe` still exits 0, and no line appears anywhere. The usual cause is
autosave: it fires every five minutes and blocks the editor for around a minute, so the
editor is deaf for a fifth of the time. `MAP REBUILD` and `BSP REBUILD` both vanished that
way before this was understood.

So **turn autosave off before automating** — `AutoSave=False` under both
`[Editor.EditorEngine]` and `[UnrealEd.UnrealEdEngine]` in `System/UT2004.ini`, which
needs an editor restart — and still confirm each command:

1. count the `Cmd: <command>` lines in the log window,
2. send the command,
3. poll the window until the count rises; re-send if it has not after ~20s,
4. only then wait for the command to finish.

`unrealed-send.exe` exiting 0 means the bridge delivered input to the control, nothing
more. Note also that it prints nothing on success, so a piped `grep` returns 1 and that is
the grep's status, not the bridge's — and **never discard its stderr**: exit 6 with "Could
not focus UnrealEd's Command control" means a modeless dialog holds the focus, which looks
exactly like "editor busy" from outside but no amount of retrying will fix it.

Confirming by looking for the `Cmd: <command>` line has one catch: the log window's control
has a **bounded scrollback**, and a chatty command scrolls its own `Cmd:` line away —
`MAP REBUILD` logs a block per brush. Treat any of three things as receipt: the `Cmd:` line
appearing, the window text changing at all, or the process going busy.

## Waiting for a command to finish

There is no completion line to wait for in general, and the log file cannot be trusted for
timing. Use the editor process's CPU: sample `utime + stime` from `/proc/<pid>/stat` a
second apart and treat a sustained drop as done.

**`ps -o pcpu` is useless here** — it reports the average over the process's whole
lifetime, which stays near idle throughout a build and never rises.

None of this is needed when the command goes through `--exec` or `--build`, where
`SendMessage` already blocks until the work is done. Prefer that.

When sampling the process to decide it has finished, do not mistake the gap between one map
and the next for a crash: with a fresh editor per map there are stretches with no
`UnrealEd.exe` at all and a freshly truncated log. Watch the driver, not the editor.

Prefer read-only probes before destructive commands:

```text
OBJ LIST CLASS=LEVEL
OBJ LIST CLASS=TEXTURE
```

Commands such as `MAP SAVE`, package imports, rebuilds and actor deletion modify editor
state. Make the exact target explicit and verify the resulting file, object list or log
output.

## This bridge is not itself MCP

`unrealed-send.exe` sends one command and exits. It has no MCP transport, tool schemas or
result protocol. A proper UnrealEdMCP service should wrap it in a local MCP server and
expose bounded tools rather than an unrestricted command string. The service should:

- use stdio transport so the agent host starts it locally;
- resolve the bridge and UT2004 install from configuration;
- serialize commands because UnrealEd has one command control;
- capture results using unique begin/end markers or purpose-built query commands;
- expose safe tools for object queries, imports, rebuilds and saves;
- require explicit paths and reject commands outside the configured install;
- state that editor focus is temporarily taken while a command is delivered.

Keep MCP client metadata with UT2004MCP. This skill describes how and when to
use it; it must remain independent of Claude Code, Copilot CLI and VS Code config syntax.
