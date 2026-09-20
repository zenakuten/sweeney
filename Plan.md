# Sweeney — UT2004 modding agent (plugin)

## Context

Years of UT2004 UnrealScript work (WSUTComp, WS3SPN, WSZound, DarkWalker, UT2004MCP)
and two large Python content toolchains (ut3converter, utupscaler) have produced a
body of hard-won, non-obvious knowledge — UCC compiler traps, replication limits,
`.t3d` round-trip losses, package-format rules, texture-import quirks. Today it is
scattered across:

- `~/.claude/projects/the dev install project/memory/` — 74 memory files (the richest source)
- `~/.claude/projects/-data-dev-UT2004/memory/` — 39 files, an older subset
- ~10k lines of project docs (`DarkWalker.md` 2650, `ut3converter/PLAN.md` 3549,
  `ut3converter/FORMAT.md` 840, READMEs)
- `~/UT2004_p23win/CLAUDE.md` — build/encoding/testing rules

That knowledge is bound to one project directory and one machine, so it is lost the
moment work starts somewhere new. **Sweeney** packages it as a Claude Code plugin:
an agent, a set of skills, and distilled reference docs, installable into any UT2004
project, with the engine script source as its source of truth.

**Hard constraint:** the UT2004 C++ source tree is private — it sits on the author's
machine and is available to nobody else — so Sweeney must never reference or mention
it. 136 citations of it exist across the source docs and must be scrubbed during
distillation: the *finding* is kept and restated as plain engine behaviour, the
file/line citation is dropped. `scripts/check-scrub.sh` enforces this and runs over
this plan too, which is why no such path appears here.

## Decisions (settled)

| | |
|---|---|
| Form | Claude Code plugin at `/data/dev/sweeney`, plus a local marketplace so it installs anywhere |
| Engine source | Setup clones `github.com/deaod/ut2004` to `~/.sweeney/engine/ut2004`. **Clean install only** — it never looks for a clone the author happens to have; the only path it reuses is its own from a previous run |
| Python toolchains | Skills that document and drive them; code stays in the install where its relative paths work |
| Knowledge | Distilled into topic reference docs, de-duplicated, C++ citations removed |
| Copilot CLI | Supported as a second front end — see below |

## Scope principle: engine knowledge, not mod implementations

Sweeney is a general UT2004 scripting and modding agent. The line every distillation
step is held to:

- **In scope** — how the engine behaves, what the toolchain does, and traps that bite
  anyone (UCC hangs, `.t3d` round-trip losses, texture import flips, replication
  limits). These generalise, and are checkable against the script source.
- **Out of scope** — how one of these mods chose to solve its own problem. WSUTComp's
  ping compensation is the clearest case: a rewind/fake-projectile system particular
  to that mod. Teaching it as if it were engine behaviour would make Sweeney confuse
  a design decision for a fact.

The same cut applies elsewhere. From `DarkWalker.md`, the reusable half is kept
(Karma params are in Karma scale, what bone control UE2 script actually exposes,
overriding a state can silently delete an implementation, holding a reference to an
effect means owning its lifetime); the walker's gait and stepping tables are not.
From the mod READMEs, the release-note style and project descriptions are kept; the
feature histories are not.

Mod-specific work stays where it is and is named in `reference/project-catalog.md` as
prior art — "if you ever need X, WSUTComp solved it, here is where" — so nothing is
lost, but nothing masquerades as engine truth either.

## Copilot CLI compatibility

Copilot CLI reads skills from `~/.copilot/skills`, `~/.agents/skills` (personal) and
`.github/skills`, **`.claude/skills`**, `.agents/skills` (project), using the same
`SKILL.md` + YAML frontmatter format, with `name` and `description` required. So the
skills — the bulk of this work — are portable verbatim. Custom agents are
`.agent.md` files in `.github/agents/` or `~/.copilot/agents/`, with
`name`/`description`/`tools` frontmatter; MCP servers go in
`~/.copilot/mcp-config.json`. Plugins and `commands/*.md` are Claude-Code-only and
have no Copilot equivalent.

Consequences for the design:

- **The `skills/` tree is the shared core and must stay front-end-neutral.** No
  `${CLAUDE_PLUGIN_ROOT}`, no Claude-only tool names in frontmatter, no dependence on
  a slash command having run. Every path is resolved through `~/.sweeney/config.json`,
  which the setup script writes — this is exactly why that indirection is there.
- **The agent body lives in one place and is emitted twice.** `agents/sweeney.md`
  (Claude) and `.github/agents/sweeney.agent.md` (Copilot) share the same instruction
  text; only frontmatter differs. Keep the body in
  `reference/agent-instructions.md` and have both files include it verbatim, so it
  cannot drift.
- **`AGENTS.md`** at the repo root gives Copilot (and any other AGENTS.md-aware tool)
  the project orientation that `CLAUDE.md` gives Claude.
- **`/sweeney-setup` is Claude-only**, so `scripts/setup.sh` must be runnable directly
  (`bash scripts/setup.sh`) and do the whole job without the command wrapper. The
  command file is a thin shell around it.
- MCP config for Copilot ships as `agents/copilot-mcp-config.json`, mirroring the
  existing `~/UT2004_p23win/UT2004MCP/Agents/copilot-cli-mcp-config.json`.

## Target layout

```
/data/dev/sweeney/
  .claude-plugin/
    plugin.json                  name, description, author
    marketplace.json             single-plugin marketplace for local install
  agents/
    sweeney.md                   Claude subagent (frontmatter + shared body)
    copilot-mcp-config.json      UT2004MCP entry for ~/.copilot/mcp-config.json
  .github/agents/
    sweeney.agent.md             Copilot CLI agent (frontmatter + same shared body)
  AGENTS.md                      repo orientation for Copilot / AGENTS.md-aware tools
  commands/
    sweeney-setup.md             thin wrapper over scripts/setup.sh (Claude only)
  skills/                        FRONT-END-NEUTRAL: works in Claude Code and Copilot CLI
    unrealscript/                writing + compiling .uc          (+ references/)
    ut2004-netcode/              stock engine replication          (+ references/)
    ut2004-gui/                  menus, HUD, Canvas                (+ references/)
    ut2004-packages/             .u/.utx, textures, meshes, sounds (+ references/)
    ut2004-maps/                 .t3d, UnrealEd, BSP, collision    (+ references/)
    ut2004-live-testing/         UT2004MCP + server/client logs
    ut3-map-conversion/          drives ut3converter
    ut2004-texture-upscaling/    drives utupscaler
  reference/
    agent-instructions.md        the shared agent body, included by both agent files
    install-layout.md            dev vs client install, System/, ini files
    project-catalog.md           the existing mods, what each is, what to crib from
    glossary.md                  UT2004/UE2 terms
  scripts/
    setup.sh                     clone/locate engine source, write config
    uccheck.py                   pre-compile lint for the known UCC hang triggers
  .mcp.json                      UT2004MCP at http://localhost:6900/mcp
  README.md
```

**Engine source layout** (confirmed against a fresh clone): the repo is *flat* —
`<Package>/<Class>.uc`, e.g. `Engine/Actor.uc`, `XGame/xPawn.uc`, with **no
`Classes/` subdirectory**, unlike a UT2004 install where source lives in
`<Package>/Classes/`. 2432 `.uc` files across 31 packages. Every skill and every
grep example must use the flat form.

Config written by setup to `~/.sweeney/config.json`:
`engine_source`, `install_root`, `client_install_root`, `mcp_url`, `tools.ut3converter`,
`tools.utupscaler`. Every skill resolves paths through this file, never hardcodes them.

## Phases

### Phase 0 — Skeleton

`git init` the directory. Write `plugin.json` / `marketplace.json`, `README.md`,
`.mcp.json` (mirror `~/UT2004_p23win/UT2004MCP/Agents/claude-code.mcp.json`).

### Phase 1 — Setup command and config

`scripts/setup.sh`, driven by `commands/sweeney-setup.md`:

1. Engine source — **assume nothing is on the machine.** If
   `~/.sweeney/engine/ut2004` already exists (a previous setup run), `git pull`;
   otherwise `git clone --depth 1 https://github.com/deaod/ut2004
   ~/.sweeney/engine/ut2004`. `$SWEENEY_ENGINE` overrides the destination for people
   who want it elsewhere, but there is **no probing of other directories** — a clone
   that happens to sit somewhere on the author's disk is not Sweeney's to find, and
   relying on one would make setup work here and fail everywhere else.
   Verify the result by checking `Engine/Actor.uc` exists.
2. Detect the UT2004 install by looking for `System/UCC.exe` + `System/Engine.u`,
   walking up from `$PWD` and checking `~/UT2004*`. Record whether `UCC.exe` is
   32- or 64-bit (32-bit hangs on large packages — a real failure mode here).
3. Detect `ut3converter/ut3conv.py` and `utupscaler/utup.py` under the install.
4. Write `~/.sweeney/config.json`; print what was found and what was not.
5. `--install copilot` (optional): symlink `skills/` into `~/.copilot/skills/` and
   `.github/agents/sweeney.agent.md` into `~/.copilot/agents/`, and print the MCP
   block to merge into `~/.copilot/mcp-config.json` rather than editing that file.

Re-runnable and non-destructive: it never writes into a UT2004 install, and never
edits an existing config it did not create.

### Phase 2 — The agent

Shared body in `reference/agent-instructions.md`, emitted as:

- `agents/sweeney.md` — frontmatter `name: sweeney`, a description triggering on
  UT2004 / UnrealScript / mutator / `.uc` / UCC / `.t3d` / UnrealEd work, tools
  `Bash, Read, Write, Edit, Grep, Glob, WebFetch` plus the `ut2004` MCP tools.
- `.github/agents/sweeney.agent.md` — same body, Copilot frontmatter
  (`name`, `description`, `tools`).

The working model:

- **Source of truth.** Answer engine questions by grepping the script source at
  `engine_source`, and quote the class/function. Never guess an API.
- **Never mention or read the C++ source.** Not available to anyone else.
- **Scope greps to mod folders**, not the whole install — an install is tens of GB.
- **Don't build; ask the user to run `makeit`.** Building is theirs to trigger.
- **Never write into `System/liveserver/`** — that is production; propose instead.
- **Commit, don't push.**
- **Release notes only on release**, not for in-progress work, and in the existing
  terse lowercase one-liner style at the top of the mod's `README.md`.
- Before proposing `.uc`, run the `unrealscript` skill's checklist.

### Phase 3 — Core skills

Each is a `SKILL.md` (when to use, the rules that bite, pointers) plus `references/`
for the long-form material. Distilled from the inputs named below.

**`unrealscript`** — the compile loop and the language traps.
Sources: `~/UT2004_p23win/CLAUDE.md`; memories `ucc-drops-int-enum-defaults`,
`uscript-config-specifier`, `uscript-int-accumulator-drift`,
`editpackages-only-current-build`, `ucc-compress-commandlet`,
`ue2-cross-package-refs-need-public`, `exec-poke-not-obj-poke`, `dont-run-makeit`,
`grep-scope-mod-folders`, `utx-build-output-use-mv`.
Must cover: Latin-1 only, never UTF-8/BOM; UCC hangs on "Analyzing..." instead of
erroring, triggers being backslash escapes in comments and the ternary operator;
`timeout 60 ./UCC.exe make` + last `Parsing` line to bisect a hang; enum defaults
silently dropped when given as ints; `var config` inert without `config(Name)`;
`EditPackages` must list only the package being built; `rm` the `.u` before `make`.
Ships `scripts/uccheck.py`: flags non-Latin-1 bytes, BOMs, backslashes in comments,
ternaries, and `Prop=<int>` in `defaultproperties` where the property is an enum.

**`ut2004-netcode`** — **stock engine replication**, not any one mod's netcode.
The subject is what a modder must know to write a mutator that works online:
the server-authoritative model, `Role`/`RemoteRole` and the role enum, `replication`
blocks and when a variable is actually sent, `reliable`/`unreliable` and
`simulated` functions, `bNetOwner`/`bClientAuthoritative`, relevancy and
`bAlwaysRelevant`, actor channels, and where client-side code is allowed to live.
Built primarily by reading the engine script source at `engine_source`
(`Engine/Actor.uc`, `Engine/PlayerController.uc`, `Engine/GameInfo.uc`,
`Engine/Mutator.uc`, `Engine/ReplicationInfo.uc`), so it generalises and stays
verifiable.

Layered on top, the general lessons already paid for:
`pawn-anim-netcode-constraints` (why third-person pawn animation cannot be fixed from
script, and that script replication on a Pawn is capped to non-owners — a genuine
engine limit worth knowing before starting), `ut2004-client-package-cache` (server `.u`
lands in the client `Cache/` under a GUID), `controllerlist-vs-teaminfo-size`,
`playerrecordclass-mod-naming`, and from `mcp-mutator-project` the reusable pattern for
running code on a client from a server-side mutator — a `LinkedReplicationInfo` in the
`PlayerReplicationInfo.CustomReplicationInfo` chain, plus `bAddToServerPackages=true`
rather than a `ServerPackages=` ini line.

**Explicitly out of scope:** WSUTComp's ping compensation. `WSUTComp/Docs/ping-compensation.md`
documents one mod's rewind/fake-projectile implementation, not engine behaviour, and
carrying it would teach Sweeney a specific mod as if it were the engine. Reference it
from `reference/project-catalog.md` as prior art to read if a similar system is ever
wanted, and otherwise leave it in WSUTComp where it belongs.

**`ut2004-gui`** — menus, HUD, Canvas.
Sources: memories `canvas-fontscale-drawtextjustified`, `gui-drawtilestretched-9slice`,
`scoreboard-64-player-rowscale`, `wsutcomp-gui-components-in-wszound`, `utcomp-menu-f5`;
`WSUTComp/Docs/client/user-guide.md`; `/data/dev/UT2004/Claude/docs/Skins.md`.

**`ut2004-packages`** — the content pipeline and the UE2 package format.
Sources: memories `ut2004-tga-import-flips`, `ut2004-texture-import-alpha-flag`,
`shader-ignores-texture-alpha`, `generated-materials-are-class-subobjects`,
`ut2004-meshemitter-skins`, `ue2-tagged-bool-has-size-field`, `ue2-package-diff-technique`,
`umodel-dump-has-two-struct-formats`, `ut2004-batchexport-dies-on-restricted-mesh`,
`redirect-needs-transitive-closure`, `ut2004-effects-need-high-detail`,
`zound55-decompiled-nfun`, `zound55-binary-source-files`;
`ut3converter/FORMAT.md` (package/format sections, C++ citations stripped).

**`ut2004-maps`** — `.t3d`, UnrealEd, BSP, terrain, collision.
Sources: memories `t3d-*` (7 files), `ue2-brush-uvs-are-texels`,
`unrealed-drops-cacherecords-gametypes`, `mcd-hull-orientation-and-lookup`,
`usesimpleboxcollision-gates-the-hull`, `ase-roundtrip-*` (2),
`carried-mesh-slots-need-package-names`, `carried-assets-must-not-reference-source-map`,
`4k-maps-max-lightmaps`, `aerowalk-cannot-survive-t3d-roundtrip`;
`ut3converter/FORMAT.md` geometry/zone/physics sections.
Key content: what a `.t3d` round-trip silently loses (LightMapScale, terrain, UT colour
codes, quoted values, zone separation), that `Brush=Model` refs override rewritten
geometry, that brush UVs are texels so an Nx texture needs `TextureU/V` ×N, and that
`UseSimpleBoxCollision=False` meshes must not get an MCD hull.

### Phase 4 — Tooling skills

**`ut2004-live-testing`** — test against a running server.
The `ut2004` MCP tools (`query_game_info`, `list_players`, `say`, `switch_map`, `kick`,
`add_bot`, `remove_bots`, `player_input`, `screenshot`, `gui_click`) and the
screenshot → `magick bmp png` → read loop; run UT2004 as a **listen server** so
screenshots land on the agent's machine. Log locations and the "capture right after
the run, they get overwritten" rule; use a greppable prefix for temporary `log()`.
Sources: `UT2004MCP/README.md`, `UT2004MCP/Agents/README.md`, memories
`mcp-mutator-project`, `restart-server-after-mcp-rebuild`, `win64-server-ini`,
`dont-edit-liveserver-without-prompting`, `mkg-bot-roster-setup`; CLAUDE.md testing section.

**`ut3-map-conversion`** — drives `ut3converter/ut3conv.py` (`t3d`, `assets`, `info`,
`classes`, `imports`; `tools/` helpers). The phase model, what converts and what does
not (UT3 maps are static-mesh-based, so BSP-only output is an empty shell), and the
import-into-UnrealEd procedure. Sources: `ut3converter/README.md`, `PLAN.md` §3–4,
memories `ut3-to-ut2004-map-converter`, `converted-maps-batch-regen`, `ut3-install-and-toolchain`.

**`ut2004-texture-upscaling`** — drives `utupscaler/utup.py`
(`survey`/`extract`/`upscale`/`package`/`t3d`/`all`, then the `EditPackages` + `mv`
build step). The survey-twice rule, the shared content store, textures vs composites
vs referenced-but-never-drawn, and the tmpfs-scratch memory trap.
Sources: `utupscaler/README.md`, `roughinery/README.md`, memories `utupscaler-*` (3),
`new-map-needs-the-content-step`, `detail-texture-synthesis`, `tmp-is-tmpfs-scratch-costs-ram`,
`torch-rocm-opencl-amd-stub`, `roughinery-4k-texture-project`.

Vehicle/Karma/skeletal material from `DarkWalker.md` (bone control available in UE2
script, negated aim rotations, `KarmaParams` in Karma scale where 1 unit = 50uu,
overriding a state silently deleting an implementation, effect lifetime ownership)
goes into `ut2004-packages/references/vehicles-and-skeletal.md` rather than its own
skill — it is reference knowledge, not a workflow.

### Phase 5 — Cross-cutting reference, scrub, verify

- `reference/install-layout.md` — dev vs client install, `System/`, why the server
  reads `ut2004-win64.ini`, `makeit`, `ServerPackages`, where logs land.
- `reference/project-catalog.md` — one paragraph each on WSUTComp, WSUTCompWeaponConfig,
  WS3SPN, WSZound, DarkWalker, UT2004MCP, ut3converter, utupscaler, roughinery:
  what it is, what pattern is worth cribbing. Local paths marked as
  "on the author's machine", so the plugin stays useful without them.
- `reference/glossary.md`.

## Verification

1. **Scrub check (must be clean):** `scripts/check-scrub.sh` exits 0. It greps the
   whole repo — including this plan — for the private source path and for C++
   file/line citation shapes, and fails with the offending lines if any survive.
   Named in `README.md` as the one pre-commit check. Only `Proto.md` is exempt: it is
   the original brief, not agent-facing content.
2. **Setup, from nothing:** with `~/.sweeney` absent, `bash scripts/setup.sh` clones
   the engine source itself, detects the p23win install, and writes a
   `~/.sweeney/config.json` whose `engine_source/Engine/Actor.uc` exists.
   Re-run to confirm it is idempotent (pull, not re-clone). Then grep the whole repo
   for `/data/dev/github` — it must not appear anywhere; setup must not know about
   any pre-existing clone.
3. **Install (Claude):** `claude plugin marketplace add /data/dev/sweeney` then
   `claude plugin install sweeney`. Start Claude in `~/UT2004_p23win` and confirm
   `/sweeney-setup` and the skills are listed.
4. **Install (Copilot CLI):** `bash scripts/setup.sh --install copilot`, then in
   `~/UT2004_p23win` run `copilot`, check `/agent` lists `sweeney`, and confirm a
   UnrealScript prompt pulls in the `unrealscript` skill. Also grep the skills tree
   for `${CLAUDE_PLUGIN_ROOT}` and Claude-only frontmatter keys — it must be clean,
   or the skills are not portable.
5. **Linter:** run `scripts/uccheck.py` over `~/UT2004_p23win/WSUTComp/Classes/*.uc`
   and `WS3SPN/Classes/*.uc` — expect no false positives on ~470 files that are known
   to compile. Then run it on a deliberately broken file (backslash in a comment, a
   ternary, `RemoteRole=2`) and confirm all three are flagged.
6. **Agent smoke test**, in `~/UT2004_p23win`: ask Sweeney "how does a mutator replicate
   a per-player actor to the client?" — it should cite `Engine/Mutator.uc` /
   `LinkedReplicationInfo` from the engine clone, name the
   `PlayerReplicationInfo.CustomReplicationInfo` chain and `bAddToServerPackages`, and
   never mention C++.
7. **MCP:** with a server running the mutator,
   `curl -s -X POST http://localhost:6900/mcp -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`
   lists the ten tools, and `query_game_info` returns through the agent.

## Open items

- `/data/dev/sweeney` is not a git repo yet — Phase 0 runs `git init`; no remote is
  added and nothing is pushed.
- The two memory sets overlap heavily (39 of the 74 are older duplicates). Distill from
  the p23win set; consult the `/data/dev/UT2004` set only where a file is absent there.
- The existing memories stay where they are. Nothing under `~/.claude/projects/` or
  inside any UT2004 install is modified by this work.
