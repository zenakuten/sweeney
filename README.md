# Sweeney

An AI agent that knows how Unreal Tournament 2004 actually behaves.

UT2004 runs on Unreal Engine 2, and most of what a modern coding assistant "knows" about
Unreal is wrong here. Worse, UE2's characteristic failure is silence: the compiler
accepts your line and drops it, the package builds clean and the texture is upside down,
the mutator works offline and does nothing on a server. You do not get an error. You get
a clean build and a bug you find in game, hours later.

Sweeney is a Claude Code plugin — and a GitHub Copilot CLI agent — carrying the answers
to those failures, plus the tools to catch them before you build.

## The kind of thing it knows

```unrealscript
defaultproperties
{
    RemoteRole=2        // silently discarded. Your actor never replicates.
}
```

UCC throws away an integer assigned to an enum property. No warning, no error — the
property keeps its inherited default. In ordinary code the same mistake is a loud type
error, which is exactly why this one hides.

A handful of others, all silent:

- A comment containing a backslash **and** an unbalanced quote hangs the compiler on
  `Analyzing...` — forever, with no message. So does the ternary operator, which UE2
  does not support.
- `.uc` files are Latin-1. UCC is not Unicode-aware.
- `#exec TEXTURE IMPORT` flips every TGA vertically, unconditionally, ignoring the
  descriptor byte. Seamless textures still tile, so only directional ones look wrong.
- `ALPHA=1` on import does not mean "has an alpha channel" — it sets a *rendering* flag.
  Drive it from the source file and every DXT5 texture turns translucent.
- Brush UVs are in texels, so a 4× texture at the same `TextureU` reads as zoomed in.
  Static mesh UVs are normalised and unaffected.
- A `.t3d` round-trip silently loses lightmap scale, terrain, UT colour codes, and
  anything after a `"` in a string property.
- Re-running CSG can stop a zone portal sealing, merging two zones. Actor counts match,
  surfaces match, and every actor in the lost zone is lit by the wrong `ZoneInfo`.
- `var config` does nothing without `config(Name)` on the class declaration. The ini
  section is simply inert.

Sweeney reads the UT2004 **script source** to answer questions, and quotes the class and
function it read, so you can check it.

## Tools

**`scripts/uccheck.py`** — catches, before you build, the traps that produce no compiler
message: file encoding, comments that hang the tokenizer, the ternary operator, and enum
properties given integers. It resolves property types through the class hierarchy, so a
name that is an enum on one class and a plain `byte` on another is not misreported.

Calibrated to produce **zero errors across the 2432-file engine source and 472 mod files
that are known to compile.** On its first real run it found a genuinely broken enum
default in a shipped mod.

```
MyMod/Classes/InWarmupMessage.uc:22: error: enum-default: StackMode=2 -- StackMode is the enum EStackMode
    UCC silently discards an int given to an enum property; it keeps its inherited
    default. Write the enum name instead
```

**`scripts/ucc-probe.sh`** — UCC is community-patched and there is more than one of it.
This builds a throwaway package one construct at a time against *your* install and
reports ok / error / hang, so compiler behaviour is measured rather than inherited as
folklore. It restores the install afterwards.

## Skills

| | |
|---|---|
| `unrealscript` | writing `.uc`, the compile loop, the traps above |
| `ut2004-netcode` | replication, roles, relevancy, client vs server code |
| `ut2004-gui` | menus, HUD, Canvas, scoreboards |
| `ut2004-packages` | `.u`/`.utx`, textures, meshes, sounds, the UE2 package format |
| `ut2004-maps` | `.t3d`, UnrealEd, BSP, terrain, collision |
| `ut2004-live-testing` | driving a running server, logs, screenshots |
| `ut3-map-conversion` | converting UT3 maps |
| `ut2004-textures` | remaking a map's textures with an image model |

Plus `reference/` for install layout, a glossary, and prior art worth reading.

## Install

### Claude Code

```bash
git clone https://github.com/zenakuten/sweeney
claude plugin marketplace add ./sweeney
claude plugin install sweeney
```

### GitHub Copilot CLI

```bash
bash sweeney/scripts/setup.sh --install copilot
```

Links the skills into `~/.copilot/skills/` and the agent into `~/.copilot/agents/`. Then
`copilot --agent sweeney`, or pick it with `/agent`.

The skills are plain `SKILL.md` files with no host-specific syntax, so both front ends
read the same copy.

## Setup

```bash
bash scripts/setup.sh
```

This clones the UT2004 script source ([deaod/ut2004](https://github.com/deaod/ut2004),
17MB) to `~/.sweeney/engine/ut2004`, surveys your machine for UT2004 installs, works out
which one can actually build, and writes `~/.sweeney/config.json`. Safe to re-run, and it
never writes into a UT2004 install.

It expects more than one install, because most people have more than one:

```
  found  ~/UT2004_p23win
         v3374, UCC.exe (64-bit), built 2026-07-18 18.30 d1b145e1
  found  ~/UT2004_3374
         v3374, UCC.exe (64-bit), built 2026-03-25 08.17 47d00acd
  found  ~/UT2004_p23
         v3374, native (64-bit), built 2026-07-18 18.42 d1b145e1

  ok    build install: ~/UT2004_p23win
  ok    play install:  ~/UT2004_p23
```

Installs are identified from `System/`, never from the folder name. Two that both read
"v3374" can be months apart, and the patch level is recorded nowhere — so the build date
is what orders them.

**Building on Linux:** `UCC.exe` under Wine is preferred over the native binary, which
means the Linux machine needs a *Windows* 3374 install present. A Linux install ships no
`UCC.exe` and cannot build that way at all.

## Live testing

Online behaviour cannot be verified offline. With
[UT2004MCP](https://github.com/zenakuten/UT2004MCP) loaded — an MCP server that runs
*inside* a UT2004 server — Sweeney can query the game, add bots, switch maps, run
commands as a player, click menu buttons on a client, and take screenshots. `.mcp.json`
is configured for it; see the `ut2004-live-testing` skill.

## Scope

Sweeney covers **engine behaviour and the toolchain** — things that are true for anyone
working on UT2004 and checkable against the script source.

It deliberately does not teach any one mod's design as if it were engine truth. Where a
particular mod solved a hard problem well, `reference/project-catalog.md` says so and
points at it, as prior art to read rather than fact to copy.

The engine's C++ source is not public, so nothing here rests on it.

## Contributing

The agent's instructions live in `reference/agent-instructions.md` and are emitted to
both agent formats — edit that file, then:

```bash
scripts/build-agents.sh     # regenerate
scripts/check-agents.sh     # the one pre-commit check
```

Claims about the engine should be traceable to the script source or to a stated,
reproducible observation. If a check cannot report zero errors across code known to
compile, it is not ready — a linter people learn to ignore is worse than no linter.

## Credits

Built from the accumulated notes of several years of UT2004 modding, distilled with
[Claude Code](https://claude.com/claude-code).

The engine script source comes from [deaod/ut2004](https://github.com/deaod/ut2004).
