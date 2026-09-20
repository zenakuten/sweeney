# Working on Sweeney

Sweeney is a UT2004 modding agent, shipped as a Claude Code plugin and as a Copilot
CLI agent + skills. This file is for working **on** Sweeney. The agent's own
instructions are `reference/agent-instructions.md`.

## Where things live

| Path | |
|---|---|
| `reference/agent-instructions.md` | the agent body — **the only place to edit it** |
| `agents/sweeney.md` | generated, Claude Code |
| `.github/agents/sweeney.agent.md` | generated, Copilot CLI |
| `skills/<name>/SKILL.md` | the knowledge, one skill per topic |
| `reference/` | cross-cutting docs the skills draw on |
| `scripts/` | setup, checks, the `.uc` linter |

## Rules

**Never reference the UT2004 C++ source.** It is private and available to nobody else.
Findings from it are welcome — state them as engine behaviour, drop the file/line
citation. `scripts/check-scrub.sh` enforces this over the whole repo.

**Skills must stay front-end-neutral.** They run in both Claude Code and Copilot CLI,
so: no `${CLAUDE_PLUGIN_ROOT}`, no Claude-only frontmatter, no assuming a slash command
ran. Resolve paths through `~/.sweeney/config.json`, which `scripts/setup.sh` writes.
Required frontmatter is `name` and `description` — both front ends need them.

**Edit the agent body, not the generated files.** After editing
`reference/agent-instructions.md`, run `scripts/build-agents.sh`.

**Engine knowledge, not mod implementations.** Sweeney covers how UT2004 behaves and
how the toolchain works. A specific mod's solution to its own problem belongs in
`reference/project-catalog.md` as prior art, not in a skill as if it were engine truth.

**Claims are checkable.** Anything asserted about the engine should be traceable to the
script source (`<Package>/<Class>.uc` in the reference checkout — it is flat, with no
`Classes/` subdirectory) or to a stated, reproducible observation.

## Installing a change

`claude plugin install` **copies** the repo into
`~/.claude/plugins/cache/sweeney/sweeney/<version>/`. It does not run from
`/data/dev/sweeney`, so editing here changes nothing in Claude Code until the cache is
refreshed — and `claude plugin update` is keyed on the version, so it reports "already at
the latest version" and does nothing if the version has not moved.

So, to ship a change:

```bash
# bump "version" in .claude-plugin/plugin.json, then
claude plugin update sweeney@sweeney
```

Then **restart Claude Code** — plugins, agents and skills are loaded at session start, so
a change never takes effect in the session that made it.

Copilot CLI is different: `scripts/setup.sh --install copilot` **symlinks**, so edits are
live immediately. Re-run it only when a skill is added, removed or renamed.

## Before committing

```bash
scripts/check-scrub.sh     # no C++ source references
scripts/check-agents.sh    # generated agent files match the shared body
```
