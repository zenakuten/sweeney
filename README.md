# Sweeney

A UT2004 UnrealScript and modding agent, packaged as a Claude Code plugin and as a
GitHub Copilot CLI agent + skills.

Sweeney knows how UT2004 actually behaves: the traps in the UCC compiler, how stock
replication works, what a `.t3d` round-trip silently loses, how the texture and
package pipeline fails, and how to drive a running server to test a change. It treats
the UT2004 script source as its source of truth and reads it rather than guessing.

## What it is

| | |
|---|---|
| `agents/sweeney.md` | the agent, for Claude Code |
| `.github/agents/sweeney.agent.md` | the same agent, for Copilot CLI |
| `skills/` | the knowledge, as skills — works in both front ends unmodified |
| `reference/` | cross-cutting docs the agent and skills draw on |
| `scripts/setup.sh` | one-time setup: fetch the engine source, find the install |
| `scripts/uccheck.py` | pre-compile linter for the known UCC hang triggers |
| `.mcp.json` | the UT2004MCP server, for live in-game testing |

## Setup

Sweeney needs the UT2004 script source. Setup fetches it for you:

```bash
bash scripts/setup.sh
```

This clones [github.com/deaod/ut2004](https://github.com/deaod/ut2004) to
`~/.sweeney/engine/ut2004`, looks for a UT2004 install, and writes
`~/.sweeney/config.json`. It is safe to re-run, and it never writes into a UT2004
install. In Claude Code you can run `/sweeney-setup` instead.

Set `SWEENEY_ENGINE` first if you want the source somewhere other than
`~/.sweeney/engine/ut2004`.

## Install

### Claude Code

```bash
claude plugin marketplace add /path/to/sweeney
claude plugin install sweeney
```

### Copilot CLI

```bash
bash scripts/setup.sh --install copilot
```

This links `skills/` into `~/.copilot/skills/` and the agent into
`~/.copilot/agents/`, then prints the MCP block to merge into
`~/.copilot/mcp-config.json`. Run `copilot` and pick `sweeney` with `/agent`, or
`copilot --agent sweeney`.

Copilot CLI also reads `.claude/skills` inside a project, so a project that already
has the skills linked there needs nothing further.

## Live testing

The `ut2004-live-testing` skill drives a running server through
[UT2004MCP](https://github.com/zenakuten) — query the game, add bots, run commands as
a player, click menu buttons on a client, and take screenshots. Load the mutator with
`?Mutator=UT2004MCP.MutMCP` and run UT2004 as a **listen server**, so screenshots land
on the same machine as the agent.

## Scope

Sweeney covers engine behaviour and the toolchain, not any one mod's implementation
choices. A mutator's own solution to its own problem is indexed in
`reference/project-catalog.md` as prior art, not taught as engine truth.

## Contributing

One check before committing — Sweeney must never reference the UT2004 C++ source,
which is not publicly available:

```bash
scripts/check-scrub.sh
```

Findings from that source are welcome; the finding is kept and stated as engine
behaviour, the file/line citation is dropped.
