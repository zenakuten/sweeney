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

Calibrated to report zero errors across the 2432-file engine source. On mod code it
also finds real bugs that compile: run over the 1062 mod classes of one install, the
`defaultproperties` checks found 17, every one a value UCC stores wrongly without a
word (seven vehicle weapons with `YawBone='Bone_weapon'`, so the bone is an apostrophe).

## The traps

### `.uc` files are Latin-1, never UTF-8

UCC reads a `.uc` as Latin-1 bytes. ASCII is safe, being a subset. Introduce high
bytes only as Latin-1.

What UTF-8 actually does, measured on the retail 32-bit UCC and a 64-bit 3374 build.
**None of it hangs**, despite the long-standing belief that it does:

| | UCC |
|---|---|
| UTF-8 in a comment | fine |
| UTF-8 in a string | **silent mojibake**: `"café"` is stored as `cafÃ©`, each byte its own character |
| UTF-8 BOM | retail: `Error, Unexpected 'ï'` on line 1. 3374: accepted |
| UTF-8 in an identifier | `Missing ';' before 'Ã'` |
| UTF-16 with a BOM | fine, read as Unicode: `"café"` is stored correctly |

So the real hazard is text: player-visible strings typed in a UTF-8 editor come out
garbled, with no error. Save as Latin-1 (or UTF-16) when a file needs accents.

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

`a ? b : c` is not supported. There are zero uses in the whole engine source. Use
`if`/`else` into a local.

It is an ordinary compile error, **not a hang**, but the message never mentions `?`, so
it reads like a different problem. It depends on where the `?` sits:

| Context | UCC says |
|---|---|
| `return b ? 1 : 0;` | `Type mismatch in 'Return'` |
| `x = b ? 1 : 0;` | `Type mismatch in '='` |
| `Log(b ? "a" : "b");` | `Call to 'Log': Bad '?' or missing ')'` |
| `if (b ? x : y)` | `Missing ')' in 'If'` |
| `A[b ? 0 : 1]` | `Type mismatch in array index` |

Measured across 14 contexts on the retail 32-bit UCC and two 64-bit 3374 builds, using
`tools/uparse_oracle/sandbox.py`; none hung. (Earlier versions of this skill said it
hangs analysis. That was never measured, and it is wrong.)

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

### `defaultproperties` values: quotes, spaces and literals

`defaultproperties` is not script. It is parsed by a separate text importer with its
own rules, and several natural-looking values compile cleanly and store something else.
All measured (`tests/uparse/probes/dp-*`):

```unrealscript
N=Foo                     // name: correct
N="Foo Bar"               // name: correct; double quotes allow spaces
N='Foo'                   // SILENT: stores the name ' (a lone apostrophe)
N=name'Foo'               // SILENT: stores the name Name
N=Foo Bar                 // SILENT: stores Foo, cut at the space
S='Foo'                   // string: Error, Missing '"' in string default properties

V=(X=1,Y=2,Z=3)           // correct
V=(X=1, Y=2, Z=3)         // Error, Unknown member  Z in V -- and no line number
V=vect(1,2,3)             // SILENT: stores nothing; same for col() and rot()
C=(G=128)                 // stores R, B, A as 0 -- not the parent's values
```

In `'Foo'` script syntax is a name; in `defaultproperties` it is not. Inside a struct,
`(N='Foo')` fails loudly (`Bad termination`) rather than silently. Whitespace in a struct
value is fatal only before a member name or between the name and its `=`; before `,` or
`)`, and after `=`, it is accepted.

`uccheck.py` flags all of these. Errors raised while importing defaults carry no line
number, so a failing build with `line 0` points here.

## Diagnosing a hang

Give the build a time limit if your shell has one — `timeout 60 wine UCC.exe make` on
Linux — otherwise watch it and interrupt once it has clearly stopped progressing.

The last `Parsing <Class>` / `Compiling <Class>` line on stdout names the file it died
in — or, if it reached `Analyzing...`, the package. Bisect from there by stubbing out
functions and comments. Run `uccheck.py` on that file first; it usually finds it
outright.

## The build

Most modders build on Windows, so that is the default here. Check the install rather
than assuming — playing and hosting are split fairly evenly between Windows and Linux,
and it is only the build side that leans one way.

```bat
cd System
del MyMod.u MyMod.ucl
ucc make
```

Run it yourself and read the output. Some installs add a wrapper — a `build.bat` beside
the mod's source is the usual shape — so use one if it is there, but do not assume it.

**Better, where the mod has its own ini:**

```bat
cd System
del MyMod.u MyMod.ucl
ucc make -ini=..\MyMod\make.ini
```

`-ini=` replaces the ini for that build, so `EditPackages` lives with the mod instead of
in the global `System/UT2004.ini`. Nothing to add before a build or trim after one, and
two mods cannot fight over the list. Drop the `.ucl` alongside the `.u` — a stale cache
record outlives the package it describes.

### On Linux

**Prefer the Windows `UCC.exe` under Wine over the native `UCC`.** That needs a *Windows*
UT2004 install (3374) present on the Linux machine, plus Wine — a Linux install ships no
`UCC.exe`, so a Linux-only box cannot build this way until one is added. Check
`install_root` in `~/.sweeney/config.json`; setup picks the install that can actually
build, which is often not the one you play on.

Then:

```bash
cd System && rm -f MyMod.u MyMod.ucl && wine UCC.exe make -ini='Z:\path\to\MyMod\make.ini'
```

**Arguments still have to look like Windows paths**, because the program is a Windows
program whatever is hosting it. Under Wine that means the `Z:` drive mapping and
backslashes — a Unix path is not understood, and `ucc compress` in particular fails with
a misleading error that echoes only the basename. A `build.bat` will not run here, so
read it for intent and issue the commands yourself.

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

## Releasing under a new package name

Every rebuild gets a new package GUID, and a client that already has a different build
of the same package name fails to join. That's why mods that ship more than once put
the version in the package name (`MyMod_V30`). The package name is the source folder's
name, but the source also refers to its own objects by path, and those must be renamed
to match. Renaming the text naively breaks the things that must stay the same:

| Renamed | Kept |
|---|---|
| `class'MyMod.Foo'`, `Texture'MyMod.Tex.X'` | `config(MyMod)` -- the ini file name; rename it and every player's settings reset |
| `"MyMod.FooPickup"`, `DynamicLoadObject("MyMod.Foo", ...)` | display strings: `GameName="MyMod Deathmatch"`, URLs, key-bind labels |
| `ScoreBoardType=MyMod.Foo` in defaultproperties | |
| `#exec ... PACKAGE=MyMod`, `EditPackages=MyMod` | |

The rule that separates them: **rename the name only where it is a package qualifier**,
meaning followed by `.` and an object name, or the value of `PACKAGE=` / `EditPackages=`.
A wrong `config()` name is not an error. The game runs, reads a fresh ini and saves
there, so a test passes while every setting has quietly reset.

```bash
python3 <sweeney>/scripts/uscript_rename.py MyMod MyMod MyMod_V30 [--dry-run]
python3 <sweeney>/scripts/uscript_rename.py MyAddon MyAddon MyAddon_V30 --also MyMod=MyMod_V30
```

It copies the tree beside the source and applies that rule to `.uc`/`.uci` (comments are
left alone), `.ini` and `.int`. Then it lists every use of the old name it left alone,
for you to read over. `--also` renames references to a dependency released alongside.

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
