# Sweeney

An AI agent that knows how Unreal Tournament 2004 actually behaves.

UT2004 runs on Unreal Engine 2, and most of what a modern coding assistant "knows" about
Unreal is wrong here. Worse, UE2's characteristic failure is silence: the compiler
accepts your line and drops it, the package builds clean and the texture is upside down,
the mutator works offline and does nothing on a server. You do not get an error. You get
a clean build and a bug you find in game, hours later.

Sweeney is a Claude Code plugin — and a GitHub Copilot agent, in the CLI or VS Code — carrying the answers
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

### VS Code (GitHub Copilot Chat)

```bash
bash sweeney/scripts/setup.sh --install vscode
```

VS Code reads personal skills and agents from `~/.copilot/` as well, so this is the same
install as the Copilot CLI one. It also prints the `ut2004` entry to merge into VS Code's
user `mcp.json` (**MCP: Open User Configuration**). Then pick **sweeney** from the agent
dropdown in the Chat view.

The skills are plain `SKILL.md` files with no host-specific syntax, so every front end
reads the same copy.

## Setup

```bash
bash scripts/setup.sh
```

**umodel** is needed for texture and mesh work only. It is
[Gildor's UE Viewer](https://github.com/gildor2/UEViewer), it is not
redistributable, and Sweeney can fetch it for you:

```bash
bash scripts/setup.sh --install-umodel
```

That reads the current download off
[gildor.org](https://www.gildor.org/en/projects/umodel) and unpacks it into
`~/.sweeney/umodel`. Off Windows it takes the **Windows** build and runs it through
`wine` — Gildor's Linux build is 32-bit against `libpng12` and will not start on a
current distro. Setup records the path as `tools.umodel`; you can point that at your own
copy instead.

**Requirements:** Python 3.8+, git, and a bash. On Windows that means **Git Bash**, which
ships with [Git for Windows](https://git-scm.com/download/win) — the scripts are written
to need nothing beyond what it provides. Python is found as `python3`, `python` or `py`,
so a normal Windows Python install works. `scripts/uccheck.py` is pure Python and needs
no shell at all.

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

## Updating

```bash
cd sweeney && git pull
bash scripts/setup.sh
```

Re-running setup is the part people skip. A pull brings the skills and tools, but only
setup writes new keys into `~/.sweeney/config.json` — a release that adds a tool or a path
leaves the config a version behind until it runs. It is safe to re-run and keeps anything
you set by hand.

Then, per front end:

| | |
|---|---|
| **Claude Code** | `claude plugin update sweeney` |
| **Copilot CLI / VS Code** | usually nothing — see below |

`setup.sh --install` **symlinks** the skills into `~/.copilot/skills/`, so on Linux and
macOS a pull updates them where they stand.

**On Windows it usually copies instead.** Creating a symlink needs Developer Mode or an
elevated shell, and without either the install falls back to copying — which means a pull
updates the clone and leaves `~/.copilot/skills/` on the old version. Setup tells you when
this has happened:

```
  warn  copied rather than linked (no symlink support) -- re-run after updating Sweeney
```

If you saw that line when you installed, re-run the install step after every pull:

```bash
bash scripts/setup.sh --install copilot     # or: --install vscode
```

Turning on Developer Mode (Settings > System > For developers) before installing avoids
this for good: setup will link instead of copy, and pulls become enough. Everything runs
under **Git Bash** either way, as at install time.

### If you pull and forget to re-run setup

Nothing breaks. Anything Sweeney cannot find in the config falls back to a sensible
default — a tool on `PATH`, a work directory inside the install, the bundled toolchain
under the plugin. Re-running setup makes the config say so explicitly, which is worth
having when something does go wrong.

## Example: restyle a map with an image model

Rebuilding textures is not only for upscaling. This runs every texture in **DM-Rankin**
through an anime GAN and ships the result as **DM-RankinAnime**, at the *original*
resolution — the look changes, the sizes do not.

**1. Describe the project.** One file, in the work directory Sweeney set up
(`paths.texture_work`, by default `<install>/texture-rebuild`):

```bash
mkdir -p "$UT2004/texture-rebuild/maps/DM-Rankin"
cat > "$UT2004/texture-rebuild/maps/DM-Rankin/config.json" <<'JSON'
{
  "map_name": "DM-RankinAnime",
  "package":  "DMRankinAnimeTex",
  "scale": 1,
  "restyle": true,
  "restyle_factor": 4,
  "model": "realesrgan-x4plus-anime",
  "models_dir": "/usr/share/realesrgan-ncnn-vulkan/models",
  "level_shot": "shot1",
  "detail_mode": "copy",
  "shared_content": false
}
JSON
```

The project is named for the **source** map; `map_name` is what it ships as.

`restyle_factor` defaults to 4 and is shown only to be explicit; the pipeline drops keys
that match the default when it rewrites the config.

`scale: 1` with `restyle: true` is the whole trick. An image model has a fixed factor —
the anime model is 4× — so there is no "run it at 1×". The texture goes up by
`restyle_factor` and comes back down to its original size, which applies the model's look
without changing a single dimension. Without `restyle`, `scale: 1` copies every texture
untouched.

`detail_mode: copy` keeps the stock detail textures: they are close-range grain, and
restyling them buys nothing.

**2. Run the pipeline.** Needs `umodel` (see Setup), ImageMagick, and a model runner —
here `realesrgan-ncnn-vulkan`. From the toolchain (`tools.uttexture` in
`~/.sweeney/config.json`):

```bash
./uttexture.py all DM-Rankin
```

`all` is survey, extract, upscale, package and both `.t3d` variants. For Rankin — 57
textures and 49 meshes — that was **10m21s** on one GPU, most of it in the model.

```
  57 textures to upscale, 49 meshes
  [ 1/57] anubis-water   512x512 restyled via 4x +alpha
  [ 3/57] bas01HA       1024x1024 restyled via 4x
  ...
  23 of the map's meshes carry a collision hull
  57 textures, 147.0 MB of TGA -> <install>/DMRankinAnimeTex/Textures
  36 Shader wrappers carry Detail/SurfaceType
```

**3. Build the package.** Exactly as the tool prints it:

```bash
# EditPackages must list DMRankinAnimeTex and nothing else already built
cd "$UT2004/System" && rm -f DMRankinAnimeTex.u && ./UCC.exe make
mv DMRankinAnimeTex.u ../Textures/DMRankinAnimeTex.utx
```

Rankin came out at **32.2 MB**, against roughly 500 MB for the same map at 4×.

**4. Import the map.** Take the `EditPackages` line back out *before* starting the editor
— left in, UnrealEd preloads the package under its own name and every material imports as
NULL.

Start UnrealEd and **File > New**, then open the console: **View > Log**. The log window
has a command box along the bottom — that box *is* the editor console. Load the package's
contents into the map:

```
OBJ LOAD FILE=..\Textures\DMRankinAnimeTex.utx PACKAGE=MyLevel
```

`PACKAGE=MyLevel` is the whole point: the objects come in under `MyLevel`, so the saved
map carries its own textures and meshes instead of depending on a separate package. It
has to happen **before** the import, because the `-embed.t3d` names everything as
`MyLevel.<Group>.<Name>` — import first and every material resolves to NULL, silently.
Check the Texture Browser lists `MyLevel` and is populated before going on.

Then **File > Import** the `-embed.t3d` (not File > Open), Build Geometry, Lighting and
Paths, and save as `DM-RankinAnime`.

**5. Check it.** These catch the failures that build clean and are wrong in game:

```bash
python3 tools/check_map.py   DM-Rankin     # actor counts, NULL surfaces, lost zones
python3 tools/check_hulls.py DM-Rankin     # collision hulls against the source
```

### Variations

| Want | Change |
|---|---|
| 4× upscale instead of restyle | `"scale": 4`, drop `restyle` |
| A different look | `model` — any model in `models_dir` |
| One texture kept as-is | add it to `skip_upscale` |
| One texture through a different model | `model_overrides: {"BrickWall12c": "realesrgan-x4plus"}` |

The anime model flattens photographic grain into painterly strokes, which is the point
here but a defect when upscaling. `tools/check_detail.py <Map> --try <model>` grades how
much detail each texture kept and prints a ready-made `model_overrides` block.

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
