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

## Verify every command

Open **View > Log** in UnrealEd to read immediate command output:

```text
Cmd: OBJ LIST CLASS=LEVEL
Log: Objects:
...
Log: 3 Objects (0.014M / 0.015M)
```

The visible console can update before `System/UnrealEd.log` is flushed. Do not report a
command as failed merely because a fresh line is not yet present on disk. Conversely, a
zero exit code only means the bridge delivered the input; verify the editor's output or
the requested persistent change.

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
