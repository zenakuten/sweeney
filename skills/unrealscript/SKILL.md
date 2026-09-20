---
name: unrealscript
description: Writing and compiling UnrealScript for UT2004 / Unreal Engine 2 — .uc files, the UCC compile loop, and the language traps that hang the compiler or fail silently at runtime. Use when editing or creating .uc, when a build hangs or fails, when a property or config setting appears to have no effect, or when setting up EditPackages and the build.
---

# UnrealScript (UT2004 / UE2)

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

Catches the four traps below. Errors are real; warnings appear throughout code that
compiles. It resolves property types through the class hierarchy using the engine
source, so run setup first for the enum check to work properly.

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

Both conditions are needed for the hang — a backslash *and* an unbalanced quote.
Unbalanced quotes alone (ditto marks, prose across two comment lines) are tolerated.

### Do not end a comment with a backslash

```unrealscript
// this comment ends with a backslash \
Log("next line");
```

A trailing backslash reads as a line continuation, which would pull the following line
into the comment. Measured against v3369 it does **not** — the engine source contains
six of these and compiles, including one directly above an opening brace, where a
swallowed line would unbalance the braces and fail loudly.

Treat it as a warning rather than a rule: it is a long-standing suspect, it means
nothing at the end of a comment, and it costs nothing to remove. It *is* an error when
the following line leaves a quote open, since that combination would produce an
unterminated string and appears nowhere in the engine source to prove otherwise.

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

**Do not run the build yourself — ask the user to run it** and report what came back.

```bash
cd System && ./makeit          # or: rm -f MyMod.u && ./UCC.exe make
```

- **`ucc make` skips a package whose `.u` already exists**, so deleting it first is not
  optional. A "no change" build is usually this.
- **`EditPackages` in `System/UT2004.ini` should list the system packages plus only the
  package being built.** UCC loads every package listed, even ones it skips compiling,
  and the editor loads them all at startup — a stale entry slows startup and breaks the
  editor outright once the package is deleted. Dependent packages still have to be
  built together. Deleting `UT2004.ini` resets the list to defaults.
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

- `references/build-and-packaging.md` — EditPackages, `makeit`, `UCC compress`, and
  moving output into place
- `references/silent-failures.md` — the full list, with symptoms to recognise them by
- `ut2004-netcode` skill — replication, and why `RemoteRole` matters
