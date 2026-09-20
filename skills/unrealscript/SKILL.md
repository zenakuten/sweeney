---
name: unrealscript
description: Writing and compiling UnrealScript for UT2004 / Unreal Engine 2 — .uc files, the UCC compile loop, and the language traps that hang the compiler or fail silently at runtime. Use when editing or creating .uc, when a build hangs or fails, when a property or config setting appears to have no effect, or when setting up EditPackages and the build.
---

# UnrealScript (UT2004 / UE2)

## Which UCC is this?

UCC is community-patched and **there is more than one of it**. A 3369 install and a
3374 install carry different binaries — different sizes, different architectures, built
years apart. 3374 is intended to be fully compatible with 3369 and adds enhancements on
top, so differences are not expected, but "not expected" is not "measured".

The engine script source Sweeney reads is a **v3369 dump**, and it was compiled by
Epic's own compiler, not by either patched UCC. So "it appears in the engine source and
compiles" is evidence about a *third* compiler.

Consequences, and they matter:

- Compiler behaviour below is **reported with its evidence**, so you can tell a measured
  fact from received wisdom.
- When it matters, measure it on the install in front of you:
  `scripts/ucc-probe.sh --install <root> --yes` builds a throwaway package one construct
  at a time and reports ok / error / hang. It restores the install afterwards.
- `~/.sweeney/config.json` records `ucc_bits` and `ucc_id` for the detected install.
  Quote them when reporting a compiler behaviour, so the claim stays attached to a
  binary.
- **A 32-bit `UCC.exe` hangs building large packages.** This is why the 3369 install was
  retired in favour of the 64-bit one. Never generate into one install and build in
  another: it silently rebuilds stale inputs.

UCC, the UT2004 script compiler, has two failure modes that cost far more time than
ordinary compile errors:

1. **It hangs instead of erroring.** On several constructs it never reports anything —
   it sits on `Analyzing...` forever.
2. **It accepts things and discards them.** The build is clean, and the property you
   set is simply not set.

Neither shows up as a compile error, so check for them before building.

## Check before you build

```bash
python3 <sweeney>/scripts/uccheck.py MyMod/Classes
```

Catches the traps below. Errors are real; warnings appear throughout code that
compiles. It resolves property types through the class hierarchy using the engine
source, so run setup first for the enum check to work properly.

Calibrated to report zero errors across the 2432-file engine source and 472 mod files
that build under a 64-bit 3374 UCC.

## The traps

### `.uc` files are Latin-1, never UTF-8, never a BOM

UCC is not Unicode-aware. A BOM or multi-byte UTF-8 can hang it on `Analyzing...`.
ASCII is safe, being a subset of Latin-1. Introduce high bytes only as Latin-1.

In practice a good deal of shipped code does contain stray UTF-8 in comments and
compiles anyway — so this is a hazard, not a certainty. Do not introduce more.

### A comment must not leave a quote open

UCC tracks string literals *inside comments*, which it should not. A backslash escape
that leaves a `"` unclosed opens a string that never ends; the parser runs off the end
of the file and hangs.

```unrealscript
// ends with \" and more            <- HANGS: the " opens a string, nothing closes it
// ex: PlayStream("D:\\a.mp3",0)    <- fine: opens and closes
// \r\n is stripped from the line   <- fine: backslash, no quote
// the "name" field                 <- fine: balanced
```

Both conditions are needed — a backslash *and* an unbalanced quote. Unbalanced quotes
alone (ditto marks, prose split across two comment lines) appear throughout the engine
source and in shipped mod code that builds under 3374.

### No ternary operator

`a ? b : c` is not supported and hangs analysis. There are zero uses in the whole
engine source. Use `if`/`else` into a local.

### Enum properties silently ignore integers

In `defaultproperties`, an enum property assigned an integer literal is **discarded
without warning** — it keeps its inherited default:

```unrealscript
RemoteRole=2                      // SILENTLY IGNORED, stays ROLE_None
RemoteRole=ROLE_SimulatedProxy    // correct
```

In ordinary code the same mistake is a loud type error, which is why this hides. It is
the classic cause of "my actor never replicates". `=0` is not safe either: it is only
harmless when the *parent's* default is also index 0.

This bites hardest on decompiled code — UE Explorer emits every enum default as a raw
int, so a decompiled package can carry dozens at once.

## Diagnosing a hang

```bash
cd System && timeout 60 ./UCC.exe make
```

The last `Parsing <Class>` / `Compiling <Class>` line on stdout names the file it died
in — or, if it reached `Analyzing...`, the package. Bisect from there by stubbing out
functions and comments. Run `uccheck.py` on that file first; it usually finds it
outright.

## The build

```bash
cd System && rm -f MyMod.u && ./UCC.exe make      # wine ./UCC.exe make on Linux
```

Run it yourself and read the output. Some installs add their own wrapper around this —
use it if it is there, but do not count on one.

**Better, where the mod has its own ini:**

```bash
cd System && rm -f MyMod.u MyMod.ucl && ./UCC.exe make -ini=../MyMod/make.ini
```

`-ini=` replaces the ini for that build, so `EditPackages` lives with the mod instead of
in the global `System/UT2004.ini`. Nothing to add before a build or trim after one, and
two mods cannot fight over the list. Drop the `.ucl` alongside the `.u` — a stale cache
record outlives the package it describes.

- **`ucc make` skips a package whose `.u` already exists**, so deleting it first is not
  optional. A "no change" build is usually this.
- **Prefer a per-mod ini over editing the global one.** `ucc make -ini=<path>` takes a
  whole replacement ini, so a mod can carry its own `make.ini` with its own
  `EditPackages` list and never touch `System/UT2004.ini`. See below.
- **If you do use `System/UT2004.ini`, `EditPackages` should list the system packages
  plus only the package being built.** UCC loads every package listed, even ones it
  skips compiling, and the editor loads them all at startup — a stale entry slows
  startup and breaks the editor outright once the package is deleted. Dependent
  packages still have to be built together. Deleting `UT2004.ini` resets the list to
  defaults.
- **Move build output into place, do not copy.** `[Core.System]` searches
  `../System/*.u` *before* `../Textures/*.utx`, so a leftover `.u` silently shadows the
  `.utx` that shipped. `mv MyPkg.u ../Textures/MyPkg.utx`.

## Other things that fail silently

- **`var config` does nothing without `config(Name)` on the class declaration.**
  `class Foo extends Bar config(MyMod);` — without it the ini section is inert and
  values stay at their `defaultproperties`. No warning, no log line. Subclasses inherit
  the specifier, so it bites new standalone classes. Find them with:
  `grep -L "config(" $(grep -rl "var()* *config" *.uc)`
- **`int += float` truncates every iteration.** An accumulator loop drifts by the lost
  fraction each step. Declare the accumulator `float`, or compute the value fresh per
  iteration.
- **A cross-package reference must be to a *public* object**, or `SavePackage` fails at
  the very end of a build that looked fine.

## More

- `references/build-and-packaging.md` — EditPackages, `UCC compress`, and moving output
  into place
- `references/silent-failures.md` — the full list, with symptoms to recognise them by
- `ut2004-netcode` skill — replication, and why `RemoteRole` matters
