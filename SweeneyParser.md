# SweeneyParser — an UnrealScript front end that agrees with UCC

## Goal

A Python parser for UT2004 UnrealScript (`scripts/uparse/`) that, given a `.uc` file
and the packages it depends on, predicts what `UCC make` will do with it. One of:

- **ok**: compiles. We also produce a typed AST and a symbol table.
- **error**: the line UCC reports, and its message.
- **hang**: UCC will sit on `Analyzing...`. This is the most valuable prediction,
  because UCC itself never reports it.
- **silent discard**: it compiles, but something is thrown away. Today that means a
  `defaultproperties` value dropped, `var config` with no `config()`, and similar.

It has to be **faithful to UCC, quirks included**. A cleaner grammar that accepts what
UCC rejects, or rejects what UCC hangs on, is worse than useless to Sweeney. That's why
this isn't the existing ANTLR or tree-sitter UnrealScript grammars. They were written
for highlighting and navigation, not for agreeing with UCC. We should still benchmark
against them in P0 (see Prior art).

Once it exists, these build on it:

- `uccheck.py` gets checks that need types: enum-int defaults resolved properly,
  `int += float`, an unresolved class in `class'X'`, a non-public cross-package ref.
- `uscript_rename.py` renames on the AST instead of with regexes.
- Fast error checking without wine/UCC: milliseconds per file, no hang to time out on.
- Later, if wanted, a language server (go-to-definition, find references) for any editor.

## Why it is automatable now

"Weeks" was the estimate for writing this by hand from the source. What changes the
picture is that **we have a perfect oracle that runs locally: `UCC.exe` itself**. We can
also read its output (`.u` packages) with the UE2 package reader we already have in
`tools/uttexture/uttexture/ue2.py`. So almost every question of the form "is the parser
right?" can be answered by a machine, without a human judging it. Agents can then burn
tokens in a loop: generate cases, ask UCC, diff, fix, ratchet.

That gives us four oracles, from cheap to deep:

| # | Oracle | What it checks | Cost |
|---|---|---|---|
| 1 | **Positive corpus**: 2432 engine `.uc` plus every mod tree we have (WSUTComp, WS3SPN, WSUTCompWeaponConfig, Zound55, UT2004MCP, DarkWalker, ...) | parses with zero diagnostics | free, offline |
| 2 | **UCC on probes and mutants** | outcome class (ok/error/hang), error line, error message | ~seconds per run, parallel |
| 3 | **`.u` reflection diff**: properties, functions, flags, params, structs, enums, consts, states, replication, from the compiled package | the declaration pass and type resolution are exactly right | one build per package |
| 4 | **Bytecode diff**: the compiled function bodies | operator overload choice, implicit conversions, call resolution | needs a bytecode decoder |

## Decision needed first: the C++ source rule

`AGENTS.md` says **don't build on the UT2004 C++ source**. Sweeney is public
(github.com/zenakuten/sweeney), the source isn't, and transliterating Epic's compiler
into a public repo is a copyright problem on top of the checkability one.

The compiler here is `Editor/Src/UnScrCom.cpp`: 7.3k lines, hand-written recursive
descent, 272 `appThrowf` error sites. `defaultproperties` import is
`UObject::ImportProperties` in Core. That would be the best possible map of what to
test. Proposed resolution, which keeps the rule intact:

- **The C++ is a test-generation guide, never an implementation source.** Agents may
  read it on this machine to learn *which behaviours exist*: every error site, every
  quirk branch, the order of the passes. They turn each one into a **probe case**.
- **The evidence is the UCC result, recorded in the repo.** Every behaviour the parser
  implements is pinned by a probe whose expected outcome came from running UCC. Anyone
  with a UT2004 install can re-record it with `uparse-oracle --refresh`. That's exactly
  the "stated, reproducible observation" `AGENTS.md` asks for.
- No C++ is copied, no C++ identifiers or line references go in comments, and no code
  is structured by porting function by function. The parser is written against the
  probe suite.
- Error message *strings* are observable UCC output, so they get harvested from the
  probe results, not from the source.

*Settled:* `AGENTS.md` now allows this use: reading the C++ to decide which test
cases to write, with UCC's recorded result as the evidence.

## Architecture

Pure Python 3.8+, stdlib only, so it runs under Git Bash on Windows like the rest of
`scripts/`. Modules are split so parallel agents rarely touch the same file:

```
scripts/uparse/
  lexer.py       tokens with UCC's quirks: strings tracked inside comments, name
                 literals, #exec/#error/#call lines, cpptext/cppstruct raw blocks
  decl.py        pass 1: class header, var/struct/enum/const, function and state
                 signatures, replication, defaultproperties captured raw; bodies
                 skipped by brace matching, as UCC's first pass does
  scope.py       class/package symbol tables; packages with source come from .uc,
                 binary-only ones (System/*.u) from the package reader
  types.py       property types, conversion costs, operator tables read from
                 Object.uc's operator declarations (public script)
  stmt.py expr.py pass 2: statements and expressions to a typed AST
  check.py       semantic rules: the error catalogue, specifier legality,
                 replication/state/iterator rules
  defaults.py    defaultproperties import semantics, and the silent-discard report
  diag.py        UCC-format diagnostics: File.uc(LINE) : Error, message
  cli.py         uparse check|ast|symbols|explain
tests/uparse/
  probes/        curated .uc cases + recorded UCC outcome (golden)
  corpus.txt     paths of positive corpora (resolved via ~/.sweeney/config.json)
tools/uparse-oracle/
  sandbox.py     N throwaway build sandboxes, run UCC with a time limit, parse output
  mutate.py      mutant and grammar-guided generators
  reflect.py     .u reflection dump (oracle 3), extending ue2.py
  bytecode.py    function body decoder (oracle 4)
  score.py       the scoreboard and divergence clusters
```

The pass structure has to mirror UCC's: all classes are parsed (signatures) before any
body is compiled, and parents before children. Which error UCC reports first depends on
that order, so getting it wrong shows up as line mismatches.

## The oracle harness (P0, build this first)

Everything else depends on it, so it gets the most care.

- **Sandboxes.** Each one is a directory with a `System/` (symlinks to the install's
  `UCC.exe`, `*.dll`, `*.u`, plus its own `make.ini` and a `UT2004.ini` copy) and a
  probe package dir. Run 8–16 in parallel under the same wine prefix. Measure first;
  wineserver contention may cap it. Put sandboxes on disk, **not `/tmp`** (it's tmpfs).
- **Run.** `rm Probe.u Probe.ucl; wine UCC.exe make -ini=Z:\...\make.ini -silentbuild`,
  using Windows paths. Time limit 30s, with `timeout` → `hang`. Capture stdout and parse
  `Error:` / `Warning:` lines into `(file, line, message)`. Keep the `.u` on success for
  oracles 3/4.
- **Cache.** Results keyed by `sha256(sources) + ucc_id` in a sqlite file outside the
  repo. Mutation produces many duplicates, and UCC time is the bottleneck.
- **One error per run.** UCC stops at the first error in a class, so error probes are one
  class with one fault. Ok-probes can be batched many classes per package.
- **Seeds.** Mutants need classes that compile standalone in a probe package. Find them
  automatically: try every corpus class alone (rewritten to package `Probe`) and keep
  the ones UCC accepts. Expect hundreds, mostly non-native engine leaf classes and mod
  classes.
- **Scoreboard** (`score.py`), run after every change:
  - corpus: files parsed clean / total
  - probes: outcome-class agreement, line agreement, message agreement
  - hang suite: recall and precision
  - reflection diff: mismatched fields over the corpus packages
  - divergences clustered by signature `(ucc_outcome, ucc_msg_template, our_outcome,
    our_msg_template)`, largest cluster first, with the 3 smallest examples each
- **Ratchet.** The scoreboard is committed (`tests/uparse/score.json`). A change that
  lowers any number is rejected. That's what keeps unattended agents from
  thrashing.

## Phases

Each phase ends when its scoreboard line hits its target, not on a date.

**P0: Harness and baseline.** Sandbox, cache, scoreboard, seed finder, corpus list.
Benchmark one existing open grammar on the corpus to get a feel for the gap. Measure
UCC runs per minute. *Done when:* `score.py` runs end to end on an empty parser.

**P1: Lexer.** No UCC token dump exists, so validate indirectly. The corpus must
round-trip (concatenated tokens reproduce the file byte for byte). The comment/string
hang behaviour gets a dedicated fuzz suite: random comments with quotes, backslashes and
escapes, labelled by UCC as hang/ok. Fold in what `uccheck.py` already knows and has
measured. *Target:* corpus round-trip 100%, comment-hang suite 100% agreement.

**P2: Declarations (pass 1) and the probe catalogue.**
- Parse every top-level construct, skipping bodies.
- Build the **error catalogue**: one or more probes per UCC error, each a minimal class
  that triggers it, generated by agents working through the compiler's error sites (see
  the C++ decision) and through the language reference. About 272 errors, 300–500 probes.
- Grammar-guided generation: every specifier combination on class/var/function/state/
  struct, labelled by UCC.
- *Target:* corpus 100%. Reflection diff (oracle 3) zero for declarations on every corpus
  package: same properties in the same order, same flags, same function signatures and
  flags, same struct and enum shapes.

**P3: Scopes and types.** Resolve every type across class, package and dependency,
loading binary-only packages via `reflect.py`. Resolve `within`, `dependson`,
`config()` inheritance and hidecategories. *Target:* reflection diff zero including
resolved types, and the declaration-level probe suite in agreement.

**P4: Statements and expressions (pass 2).** Full AST: all statements, labels/`goto`,
`foreach` iterators, `switch`, `assert`, state code, `default.`/`static.`/`super(X).`/
`global.`, `class'X'`, dynamic casts, array syntax, `new`. Generate token-level mutants
from the seeds (delete, duplicate or swap a token; drop `;`, `)` or `}`; replace an
identifier with a keyword). *Target:* corpus 100%, outcome-class agreement ≥ 99% and
line agreement ≥ 97% on syntax mutants.

**P5: Type checking.** Name resolution order (local, class, outer, globals), operator
overload resolution by conversion cost using the operator declarations in `Object.uc`,
function call matching (`optional`, `out`, `coerce`, `skip`), `const`/`static`/`final`
rules, replication condition rules, state legality. Semantic mutants: swap an
argument's type, drop an argument, call a non-static statically, assign to a const.
Bytecode oracle (P5b): decode compiled bodies (the opcode table is documented publicly
by UELib/UE Explorer). Then compare our lowered AST's operator choices, conversion casts
and call targets against UCC's. *Target:* semantic mutants ≥ 98% message agreement,
bytecode-shape diff zero on the corpus.

**P6: `defaultproperties`.** Import semantics: enum names vs ints, struct and array
syntax, object refs, quoting, dynamic array `Prop(n)=`, localized strings, subobjects
(`Begin Object`). Oracle 3 is strong here: read the serialized defaults from the `.u`
and diff against what we predict. Every place where we predict a value but UCC stored
the inherited one is a **silent discard**, and that list is the product.
*Target:* predicted defaults equal serialized defaults across every corpus package.

**P7: Ship.**
- `uparse check` emits diagnostics in UCC's format, so existing error parsing works.
- `uccheck.py` gains the type-aware checks.
- Rewrite `uscript_rename.py` on the AST.
- `skills/unrealscript/SKILL.md` documents the tool, with the calibration numbers quoted
  like uccheck's are now.
- Record which `ucc_id` the goldens came from. 3369 vs 3374 vs 32-bit UCC differences
  become a probe-suite diff rather than folklore.

## How the agents run it

The loop is mechanical once P0 exists:

1. `score.py` produces the divergence clusters.
2. An orchestrator (a `/loop`, or a cron'd `claude -p` script) hands the top K clusters
   to worker agents, one cluster each, in separate git worktrees.
3. A worker adds the cluster's examples as golden probes, fixes the parser, and runs the
   scoreboard. It returns only if its cluster shrank **and** no other number dropped.
4. The orchestrator merges green workers in turn, re-scoring after each merge. A merge
   that regresses is dropped and the cluster requeued.
5. Generators run continuously in the background, feeding new labelled cases into the
   cache. UCC time is the scarce resource, so keep the sandboxes busy.

Agent roles, each a prompt plus a scope:

| Role | Writes | Reads |
|---|---|---|
| probe author | `tests/uparse/probes/` | compiler source (locally), language docs, UCC results |
| generator | `tools/uparse-oracle/mutate.py` | grammar, divergence report |
| fixer | one or two `scripts/uparse/*.py` modules | its cluster, probes |
| oracle maintainer | `reflect.py`, `bytecode.py` | package format docs, `ue2.py` |
| reviewer | nothing, rejects merges | diff, scoreboard, the C++ rule |

Guards for unattended running:

- Workers may not edit goldens' expected outcomes. Only `--refresh` from UCC can do that.
- Workers may not special-case a probe's file name or contents. The reviewer checks for
  this, and a held-out mutant set that workers never see catches overfitting.
- Every worker commit must leave the scoreboard monotonic. Commit locally only; the
  user does the pushing.

## Risks

- **Hang semantics.** Some hangs may depend on what follows the fault in the file, or on
  memory. Classify by time limit, and re-run any `hang` once at 2× the time limit before
  trusting it.
- **Sandbox fidelity.** A probe package compiled in a sandbox must behave like the same
  code in a real mod: same `EditPackages` prefix, same ini. Verify by building
  WSUTComp in a sandbox and in the real install and diffing the `.u` reflection.
- **First-error ordering** across classes in a package. Single-fault probes avoid it.
  Multi-file mod builds are a separate, later "which error comes first" suite.
- **Defaults import** lives outside the compiler proper and has its own quirks. It may
  be P6's whole budget.
- **Overfitting to mutants.** Mutants are syntactically close to valid code. Real
  mistakes (decompiled code, half-edited files) belong in the held-out set too:
  collect broken intermediate states from mod repos' git history where possible.

## Prior art to check in P0

- Eliot Van Uytfanghe's UnrealScript language server / UELib (VS Code extension, ANTLR
  grammar, UE1–3). Good for navigation. UELib also documents the UE2 bytecode format,
  which oracle 4 needs. Check its licence before borrowing anything.
- tree-sitter UnrealScript grammars, for highlighting only.
- `scripts/uccheck.py` and `scripts/ucc-probe.sh`: the measured traps and the probe
  pattern this generalises. `ucc-probe.sh` is effectively a 1-sandbox, hand-written
  version of P0.

## First concrete steps

1. ~~Settle the C++ decision above.~~ Done.
2. Build `sandbox.py` and time a single probe build. That number sizes everything else.
3. Run the seed finder over the engine corpus and WSUTComp.
4. Extend `ue2.py` into `reflect.py` far enough to dump one compiled class's properties
   and functions, and diff it by hand against its `.uc`.
5. Stand up `score.py` on an empty parser and commit the baseline.
