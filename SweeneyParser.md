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
  with a UT2004 install can re-record it with `uparse_oracle --refresh`. That's exactly
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
tools/uparse_oracle/
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

*Built:* `tools/uparse_oracle/sandbox.py` (`init`, `run`, `bench`, `selftest`,
`clean`; `Oracle.ask` / `ask_many` from Python). Measured on the 64-bit 3374 UCC under
wine, 32 cores:

| sandboxes | mean build | throughput |
|---|---|---|
| 1 | 0.85s | ~70/min |
| 8 | 1.08s | ~440/min |
| 16 | 1.22s | ~770/min |
| 24 | 1.52s | ~930/min |

A hang costs the time limit twice (it's re-tried at double), so hang-heavy suites should
run with a short `--timeout`. 10s is enough here, where a real build takes about 1s.

- **Sandboxes** live in `~/.sweeney/oracle/sb-NN/`. `System/` symlinks every install
  file except `*.ini` (copied, since UCC needs them and may write them) and `*.log`
  (skipped), and the content dirs are symlinked too. Nothing is written into the
  install. Symlinks fall back to hardlinks, then copies, on Windows.
- **Run.** `wine ./UCC.exe make -ini=Z:\...\sweeney-make.ini` with `WINEDEBUG=-all`.
  The ini is the install's `UT2004.ini` with EditPackages replaced by the stock list
  from `Default.ini`, then any dependencies, then the probe packages. The install's
  `UT2004.ini` carries mod entries, so it can't supply the list. Diagnostics come back
  in three formats, and the parser has to reproduce all three:
  - `Probe.uc(3) : Error, Missing ';' before 'function'`: ordinary compile errors,
    followed by a `Failure - N error(s)` summary line in the same format, which gets
    filtered out
  - a bare line before `Compile aborted due to errors.`: errors raised while importing
    `defaultproperties`, e.g. `Bindings::ImportText: Bad termination in: ...` or
    `ObjectProperty X.Y: unresolved reference to ...`. These have no line number, which
    is recorded as line 0
  - a bare line before `History:` / `Exiting due to error`: fatal errors such as
    `Superclass X of class Y not found`, printed straight after `Analyzing...` with no
    line break in between

  `--keep-u` saves the `.u` for oracles 3/4.
- **Measured already:** the ternary is an *error* on this UCC, not a hang. `return b ?
  1 : 0;` gives `Type mismatch in 'Return'`, and other contexts give `Bad '?'` or
  `Missing ')'`. The retail 32-bit UCC agrees. The skill's "hangs analysis" claim has
  been corrected.
- **Cache.** Results keyed by `sha256(sources) + ucc_id` in a sqlite file outside the
  repo. Mutation produces many duplicates, and UCC time is the bottleneck.
- **One error per run.** UCC stops at the first error in a class, so error probes are one
  class with one fault. Ok-probes can be batched many classes per package.
- **Seeds.** Mutants need classes that compile standalone in a probe package.
  *Built:* `tools/uparse_oracle/seeds.py`. It renames each corpus class `Foo` to
  `Seed_Foo`, including the class's unqualified references to itself in code and
  `defaultproperties`, drops `#exec` lines, and builds each one alone. A mod whose
  `.u` is built is loaded as a dependency. Results go to `~/.sweeney/oracle/seeds.json`
  and the accepted sources to `~/.sweeney/oracle/seeds/`.

  | corpus | seeds | rejected |
  |---|---|---|
  | engine source | 2302 / 2432 | 129 |
  | mods in this install | 684 / 1062 | 378 |

  The full corpus takes 1.5 minutes on 24 sandboxes, or 12s when cached. Native classes
  seed fine: the renamed class keeps `native` and UCC never checks for the C++.
  Rejections are mostly a mod's classes referring to its own unbuilt package, or code
  passing `Self` where another class expects the original type. Each rejection is real
  code with UCC's verdict attached, which makes it probe data too.
- **The engine source is a dump, not Epic's files.** Some `defaultproperties` strings
  have unescaped quotes (`Plain="""` in `PlaylistParserBase`) that the real source
  can't have had, so a few engine classes don't compile exactly as written. The
  positive-corpus oracle has to allow for this: "every engine file parses clean" is
  the target only for code, not for `defaultproperties` text.
- **Scoreboard.** *Built:* `tools/uparse_oracle/score.py`. It runs offline in about 3
  seconds; every answer it compares against was recorded from UCC earlier. It scores
  the parser interface in `scripts/uparse/__init__.py` (`predict`, `check_file`,
  `declarations`, each allowed to answer "don't know", which never counts as
  agreement):
  - `probes.*`: the goldens in `tests/uparse/probes/`. Each is a `.uc` plus a `.json`
    that only `probes.py` (i.e. UCC) writes. Scored on outcome, plus first error line
    and message where UCC reported an error. 24 to start: the measured traps, the
    ternary contexts, and the rules found so far.
  - `probes.outcome.ok|error|hang`, and the same for seeds: agreement per UCC outcome.
    Most cases compile, so a parser that answers "ok" to everything scores 85% on seed
    outcomes overall, but 0% on `seeds.outcome.error`.
  - `hang.probes.*` / `hang.seeds.*`: recall and precision of hang predictions.
  - `corpus.engine` / `corpus.local`: files `check_file` parses clean.
  - `decl.engine|local.names|full`: classes whose `declarations()` match the compiled
    class from `reflect.py`, by field names and kinds, and in full (flags, types,
    super, class flags, config). Field order is ignored for now. Checked by feeding
    it reflect's own views, which score 2222/2222.
  - `seeds.*`: UCC's verdict on all 3493 corpus classes built alone.
  - Disagreements are clustered by `(section, ucc outcome, ucc message template,
    predicted outcome, predicted message template)`, largest first, with the 3
    smallest examples each.
- **Ratchet.** `score.py --check` fails if any number drops below its ratchet, and
  `--update` refuses to write one that dropped. Only reproducible numbers go in the
  repo (`tests/uparse/score.json`: probes and the engine source). Local mods (listed
  in `~/.sweeney/oracle/corpus-local.txt`) and seeds go in
  `~/.sweeney/oracle/score-local.json`. The baseline is committed at zero everywhere.

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

*Known so far:* defaults have to be decoded by type, not by trusting each tag's size
field. In 2 of ~5000 classes a struct or array value's tag size is smaller than its
data: `XInterface.HudBCaptureTheFlag` (`NewFlagWidgets`, Epic's own build) and
`WSUTComp.UTComp_Menu_Crosshairs` (`UTCompNewHairs`, the 3374 UCC). Struct values
are nested tagged lists with their own terminator, so a type-aware reader doesn't
need the size. Those two are P6's first test cases. `reflect.py` flags them as
`defaults_desync` and keeps their declarations.

*Measured: `defaultproperties` syntax traps* (`tests/uparse/probes/dp-*`). Goldens for
probes that compile record the decoded defaults UCC stored, and the parser is scored
on them (`probes.defaults`), so silent failures count:

| Written | UCC |
|---|---|
| `N=Foo`, `N="Foo"`, `N=Foo;` | stores `Foo` (double quotes also allow spaces: `"Foo Bar"`) |
| `N='Foo'` | **silent**: stores the name `'`, a lone apostrophe. The same in a dynamic array (`AN(0)='Foo'`) |
| `N=name'Foo'` | **silent**: stores `Name` |
| `N=Foo Bar` | **silent**: stores `Foo`, truncated at the space |
| `S=(N='Foo',I=3)` | error, line 0: `S::ImportText: Bad termination in: ...` (only inside a struct is it loud) |
| `C=(R=255, G=128, ...)` | error, line 0: `Unknown member  A in C`. A space after a comma, before `=` or after `(` fails the whole struct, and the message names one member with the space in it |
| `V=(X=1,Y=2,Z=3 )` | fine: a space before `)` is accepted |
| `C=col(...)`, `V=vect(...)`, `R=rot(...)` | **silent**: compiles and stores nothing, so the property keeps its default |
| `C=(G=128)` | stores G=128 and the other members as 0, not the parent's values |
| `RemoteRole=2` (an enum) | **silent**: nothing stored |
| `X=true ? 1 : 0` | **silent**: nothing stored |

Errors raised while importing defaults carry no line number (line 0), so the parser
has to report them that way too. `reflect.decode_defaults` decodes 96% of the
install's stored defaults (names, strings, Color/Vector/Rotator binary layouts,
nested-tag structs, arrays of simple types). What's left is arrays whose element type
is declared in another package, delegates, and the one desync.

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
| generator | `tools/uparse_oracle/mutate.py` | grammar, divergence report |
| fixer | one or two `scripts/uparse/*.py` modules | its cluster, probes |
| oracle maintainer | `reflect.py` | UELib (MIT), `ue2.py` |
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
  grammar, UE1–3). Good for navigation. UELib (github.com/EliotVU/Unreal-Library, MIT)
  is where `reflect.py` takes its object and bytecode layouts from. Every layout is
  checked by the reader consuming exactly each export's recorded size.
- tree-sitter UnrealScript grammars, for highlighting only.
- `scripts/uccheck.py` and `scripts/ucc-probe.sh`: the measured traps and the probe
  pattern this generalises. `ucc-probe.sh` is effectively a 1-sandbox, hand-written
  version of P0.

## First concrete steps

1. ~~Settle the C++ decision above.~~ Done.
2. ~~Build `sandbox.py` and time a single probe build.~~ Done: ~1s per build, ~800/min
   in parallel.
3. ~~Run the seed finder over the engine corpus and WSUTComp.~~ Done: 2986 seeds.
4. ~~Extend `ue2.py` into `reflect.py` far enough to dump one compiled class's
   properties and functions, and diff it by hand against its `.uc`.~~ Done; see
   *Oracle 3* below.
5. ~~Stand up `score.py` on an empty parser and commit the baseline.~~ Done.

Next is P1, the lexer.

## P1: the lexer (done)

`scripts/uparse/importer.py` and `scripts/uparse/lexer.py`, wired into `predict` and
`check_file`. UCC reads a class in two stages, and the lexer mirrors both:

- **The importer** is line-based. It splits the file into script text,
  `defaultproperties` text and `cpptext`, and finds the class and parent names. Its
  quirks:
  - Lines end at CR, LF or CRLF.
  - The class name is the word after the first `class` (any case, at a word start)
    on a line that isn't a comment.
  - `cpptext` lines become placeholder comments, but **`defaultproperties` lines are
    dropped**, so compiler line numbers after that block are short by its length
    (probe `lex-line-after-defaultproperties`: file line 7 is reported as 3).
  - **The hang lives here.** To strip `//` safely, each script line is scanned for
    the end of its first string. The scan steps past escaped quotes (`\"`), and if
    the line's last quote is escaped with nothing after it, it never advances: UCC
    spins on `Analyzing...`. That covers code lines as well as comments, and never
    `defaultproperties` lines, which are scanned differently. Epic's code cuts lines
    at 4095 characters; the 3374 UCC doesn't (probe `lex-line-over-4095`).
- **The tokenizer** runs on the importer's script text, so its lines are the ones UCC
  prints. Its quirks:
  - Block comments nest and are removed character by character, so `a/**/b` is the
    identifier `ab`.
  - `*/` outside a comment is an error anywhere.
  - Strings end at the line; a backslash takes the next character literally, so
    `"a\nb"` is `anb`.
  - Names allow spaces.
  - A number is a digit followed by any of `0-9 . X A-F`: a float if it has a `.`,
    hex if it has an `X`, else atoi. So **`1e5` is the int 1** and `12ab` is 12.
  - A sign joins a number only in operand position (the parser's job).
  - `Type'Pkg.Name'` is read as an object path when the quoted run isn't a valid
    name, except after `return`/`case`/`goto`.

Measured, all against UCC:

- **Hangs:** 30 of 30 predicted with no false alarms, over the 64 probes plus a
  generated 300-case suite (`tests/uparse/suites/quotes.jsonl`, from
  `gen_suites.py quotes`). The suite places quotes and backslashes in line comments,
  block comments, code strings and `defaultproperties` strings. Of its 300 cases, 29
  hang, none of them in `defaultproperties`, as predicted.
- **Lexer-level errors:** all of them match UCC on line and message, over 29 `lex-*`
  probes. Those probes also check literal values through the bytecode: goldens now
  record each function's compiled constants (`literals`), and `1e5`→1, `1.5e3`→1500,
  `0x1f`→31 and `"a\nb"`→`anb` all agree.
- **Corpus:** 2928 files and 1.58M tokens lex with no errors, and the tokens cover the
  text with only whitespace and comments between (`lexcheck.py`), in about 4 seconds.
- **Scoreboard:** `hang.probes` 100/100, `probes.outcome.error` 30/69, `corpus.*` 100%.
  No wrong predictions.

What P1 can't see is any error a parser would raise before the lexer gets there.
P2 starts on that.

## P2: declarations

`scripts/uparse/decl.py` is UCC's first pass: the class header, `var`/`enum`/`struct`/
`const`, function, event, delegate and operator signatures with their `local`s, states
with `ignores`, and the replication block. Bodies are skipped. `context.py` indexes
every other class (from source where we have it, else from the compiled packages,
which is what UCC loads; intrinsic classes like `Font` come from import tables).
`resolve.py` turns the parse into the compiled shape: types, paths, inheritance.
`#include` is spliced the way the compiler does it.

**Scoreboard:**

| | names | full |
|---|---|---|
| engine (1839 classes, vs the 3369 retail packages) | **100%** | **99.9%** |
| local mods (496, vs the 3374 install) | 99.8% | **99.2%** |

The engine source is a v3369 dump, so it's scored against the matching retail
packages (`engine_reference_install` in `~/.sweeney/config.json`, here
`/data/dev/UT2004`), with the parser's context reading that same install. Against the
3374 packages it scored 94%, and every names-level miss was a member 3374 added or
changed. Two rules had been fitted to that drift and were wrong: a `pointer` keeps
`const` and isn't implied `native` (only `transient`), and `safereplace` isn't set on
native classes. The engine's 2 remaining misses are one-off native flags
(`CacheManager`, `ObjectPool`). The mods' 4 misses are stale compiled packages:
`UT2004MCP.u` predates its source, and `WS3SPN.u` was built against an older
`WSUTComp.u`. `decldiff.py` diffs a class or tallies a corpus.

**What UCC stores, measured from the compiled packages**, and now reproduced:

- **Class flags.** A class inherits only from its **parent**, so `notplaceable`,
  `noteditinlinenew` and `dontcollapsecategories` cut a flag off for the whole
  subtree; `WeaponPickup`'s descendants stay unplaceable under a placeable `Pickup`.
  - **`config` means "has config properties", own or inherited.** `config(Name)` only
    names the ini: `VoiceChatRoom` declares `config(User)` with no config vars and
    has no flag.
  - `localized` is set by a localized property, or by a property holding a struct
    (or array of one) with a localized member. Declaring such a struct isn't enough:
    `GUI` declares some and isn't localized; `CrosshairPack` holds one and is.
  - `cacheable` (0x2000000) and `safereplace` are set by the engine's C++ on native
    classes, so they come from the compiled package, as UCC reads them from the
    binaries.
  - `instanced` sets `editinlinenew` plus 0x200000.
- **Property flags.**
  - `automated` implies `edit editinlinenew needctorlink` (1,275 engine fields).
  - `editinlineuse` and `editinlinenotify` imply `editinline`; `globalconfig`
    implies `config`; `export editinline` together add `needctorlink` (one case:
    `Actor.KParams`).
  - A `pointer` is always `transient`.
  - A state with no `extends` continues the same-named state up the hierarchy, even
    when the class redeclares it (`BS_xPlayer`'s own `Spectating`).
  - `native` strings and arrays get no `needctorlink`; a struct holding a string or
    array gets it.
  - An object property of an `instanced` class gets `editinline exportobject`.
  - Locals declared *after a statement* (UCC allows it) get no `needctorlink`.
- **`ignores`.** A name in the engine's probe range clears a bit in the state's probe
  mask; any other name adds an empty stub that copies the target's parameters. The 47
  probe names (`Tick`, `Timer`, `Touch`, `BeginState`, ...) were measured by
  ignoring every non-final function of `Object`, `Actor`, `Pawn`, `Controller`,
  `PlayerController` and `AIController` in a probe state.
- **Which function a function overrides (`super`)** depends on declaration order:
  - A state function links to the state it extends (following `extends` chains
    across classes), then the same-named state up the hierarchy, then the class's own
    function **only if declared earlier in the file**, then the ancestors'.
    `Console.Typing.KeyEvent` gives `Console.KeyEvent`, but `Pawn`'s `AnimEnd` comes
    after `state Dying`, so `Pawn.Dying.AnimEnd` gives `Actor.AnimEnd`.
  - Overrides inherit `net`/`netreliable` from wherever up the chain they were
    replicated.
- **Operator names.** One word per symbol character (`!=` is `NotEqual`), `Pre` for a
  preoperator, then the parameter types. A qualified struct uses its bare name, and an
  array uses its element type.
- **Names compare without case.** UCC stores a name in whichever spelling its global
  name table saw first (`HUD` against `Hud`), which no parser can predict.

### The declaration error catalogue

`tests/uparse/suites/declerrors.jsonl` and `declerrors2.jsonl` hold about 150 minimal
classes, one per declaration error (harvested from the compiler's error sites under
the C++ rule), each labelled by UCC. `decl.py` raises the ones that need only the
file. `resolve.deferred_errors` raises the ones that need other classes: unknown
types, overriding a final function, `ignores` targets, replicated names, state
`extends`. Each carries its token position, so the first error in the file wins, as
in UCC. `uparse.analyze` runs the stages in UCC's order: importer hang, importer
errors (`Bad class definition`, `Script vs. class name mismatch`), lexer,
declarations, deferred checks.

Measured along the way:

- **The comment hang has a lookalike.** For a `native` class, UCC writes a C++
  header and, if one exists, asks `Do you want to overwrite the existing version?
  (Y/N)` on stdin. With an open stdin it waits forever, which looks exactly like a
  hang. The harness now gives UCC an empty stdin, and `probes.py` re-checks any hang
  on its own before recording it, since a stall under full parallel load can also
  pass for one.
- **Some errors print no `File.uc(N)` line.** Class-name mismatches print
  `Script vs. class name mismatch (Probe/Wrong)` bare. A struct with an unknown
  parent prints `Cast of NULL to Struct failed` glued onto `Parsing Probe`. A
  duplicate `const` and an empty `dependson()` crash UCC outright (a backtrace, and
  `General protection fault!`).
- **Line rules.** Most errors land on the offending token's line. An unknown
  `#directive` reports the line *after* the `#`. "Unexpected end of script" reports
  the line after the last.
- **What isn't an error:** redeclaring a parent's var (`var int Tag;`), `var int A[]`,
  `var string[32] S` (obsolete size, ignored), a trailing comma in an enum, an empty
  struct. `reliable`/`unreliable` aren't function modifiers in UE2, so `reliable
  client function` is `Unexpected 'reliable'`.

Also measured in the second batch:
- Locals use the plain `Variable declaration` prefix; parameters use
  `Function parameter`. A local can't reuse a parameter's name.
- Limits: 16 parameters and 2048 array elements (`A[-1]` reports -1).
- An unknown return type is `Bad function definition` (UCC reads the word as the
  function's name), and an operator with an unusable symbol is
  `Bad preoperator definition`.
- Replicating a *parent's* variable is `Bad variable or function 'Tag' in replication
  definition`: only the class's own vars and functions can be listed.
- A stray `;` after a function body is `Unexpected ';'`, and a `local struct` gives
  the enum message.

**Scoreboard:** `probes.outcome.error` rose from 30 to 153 of 185, all on the exact
line and message. Seed classes UCC rejects: 164 of 507 predicted exactly, with **no
error predicted for any class UCC accepts** (corpus and `ok` seeds alike). What's left
is mostly errors in function bodies (P4) and in `defaultproperties` import (P6).

## P6: `defaultproperties` (done)

`scripts/uparse/defaults.py` runs UCC's defaults import: the defaults text, line by
line, onto a copy of the parent's defaults, then stores only the values that differ.
The parent's defaults come from the compiled packages, merged from `Object` down,
which is what UCC copies. Errors are predicted the way UCC's output shows them: line
0, and the **last** line logged, since the import keeps going after most errors.

`tests/uparse/suites/defaults.jsonl` (65 cases) pins the import rules. Measured:

- **Coercion:** `I=3.9` stores 3, `I=12abc` 12, `Y=300` 44 (a byte wraps), `F=2e3`
  2000.
- **Dropped without failing the build:** `I=0x10`, `I=+5`, `B=yes`, a struct value
  with no parentheses, a `class<Pawn>` given a `Light`, an unknown property, a missing
  `=`, an out-of-range index.
- **Strings:** `"a\41b"` stores `a41b` (a backslash takes the next character), and an
  unquoted string fails (`Missing '"' in string default properties`).
- **Line splitting:** `|` splits a line outside quotes, so `I=1|J=2` sets both.
- **Struct members you don't mention** keep the current value, the parent's or zero.
  A nested-tag struct stores every member, zeros included.
- **Object references:**
  - The quoted form `Type'Pkg.Name'` loads the package from disk.
  - A path may begin with a group or class inside *any loaded package*
    (`Sounds.HeadShotted` finds `WSUTComp.Sounds.HeadShotted`), and `Pkg.Name`
    finds an object inside a group.
  - "Loaded" means the stock packages, the dependencies, and everything their import
    tables pull in (`UTDiscordBridge.u` brings `LibHTTP4`).
- **Errors name a property by the class that declares it**
  (`ClassProperty Engine.Inventory.AttachmentClass`).

Also now predicted: `Type'Pkg.Name'` literals in *code*, compiled before defaults
are imported, which only find already-loaded objects (`Can't find Sound 'Pkg.Name'`).
And the parser's context is limited to what each build loads, so a mod built without
WSUTComp doesn't see WSUTComp's classes.

**Scoreboard:** `probes.defaults` went from 0 to **380/380 (100%)**: the stored defaults
of every compiling probe, silent discards included, predicted exactly. Probe errors:
170/194. Seed errors predicted exactly: 164 to 228 of 507. No error predicted for any
class UCC accepts. The corpus metric counts a predicted error as agreement when the
seed run recorded UCC rejecting that file with the same message (three engine dump
files have broken `defaultproperties`).

## Oracle 3: `reflect.py`

`tools/uparse_oracle/reflect.py` reads every script object in a `.u`: classes, states,
functions, structs, enums, consts, every property type, and the class defaults (raw
for now). It also decodes function bodies into a token tree, which UE2 forces: a body's
size on disk isn't recorded, because the object and name references inside it are
compact indices, so the only way past a body is to decode it. That makes oracle 4's
decoder mostly done already.

`reflect.py --verify` checks that every object parses to exactly its recorded size. On
this install it passes for **97,333 script objects in 67 packages**, with every function
body decoded, in about 2 seconds. Building it also fixed a bug in `ue2.py`, shared with
uttexture: a tagged property's array index has its own 1/2/4-byte encoding and isn't a
compact index. The two agree below 64, which is why texture work never hit it.

A hand diff of a probe class against its source shows what the declaration pass has to
reproduce, all measured:

- `Children` lists fields in **reverse** declaration order.
- Names keep the **first spelling the package saw**: `Sum` came out as `sum`, because
  that spelling was already in the name table. The diff must ignore case.
- The replication block adds flags after the fact: a replicated var gains `net` and a
  `rep_offset`, and a replicated function gains `net netreliable`.
- A `const` stores its raw text, leading space included (`MaxItems` gives `' 8'`).
- `var()` with no category gets the class name as its category.
- A delegate adds a hidden `DelegateProperty` named `__<Name>__Delegate`.
- `string` vars and parms carry `needctorlink`, and a return value is `parm out return`.
- Defaults store only values that differ from the parent's; `bOn=False` is absent.

UCC rules the probe hit along the way, for the error catalogue:
`After an optional parameters, all other parmeters must be optional` (UCC's own
spelling), and `Unexpected 'reliable'` for the UE3-style `reliable client function`.
