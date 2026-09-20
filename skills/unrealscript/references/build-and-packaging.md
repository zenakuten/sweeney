# Building and packaging

## The loop

```bash
cd System
rm -f MyMod.u          # not optional -- see below
./UCC.exe make         # wine ./UCC.exe make on Linux
```

Run this yourself and read the output. It is slow under Wine, but it is not
destructive — the only thing it writes is the package.

**`ucc make` skips any package whose `.u` already exists.** If a change appears not to
have taken effect, this is the first thing to check. A build wrapper, if the install has one,
usually deletes them for exactly this reason.

## A per-mod ini is the better arrangement

`ucc make -ini=<path>` swaps the entire ini for that build, so a mod can keep its own
`EditPackages` list beside its source and leave `System/UT2004.ini` alone:

```bash
cd System && rm -f MyMod.u MyMod.ucl && ./UCC.exe make -ini=../MyMod/make.ini
```

The file is a full ini, not a fragment — copy `System/UT2004.ini` and edit it. The parts
that matter:

```ini
[Engine.Engine]
EditorEngine=Editor.EditorEngine

[Editor.EditorEngine]
EditPackages=Core
EditPackages=Engine
...                       ; the system packages, in dependency order
EditPackages=MyMod        ; then yours, last

[Core.System]
Paths=../System/*.u
Paths=../Textures/*.utx
...                       ; content search paths, as in the global ini
```

Why it is worth the copy:

- nothing to add before a build or trim after one
- two mods cannot fight over one list
- the editor's startup list is untouched, so a deleted package cannot break UnrealEd
- the mod's build requirements travel with its source

Delete the `.ucl` along with the `.u`. It is the cache record for the package, and a
stale one outlives the package it describes.

This is a long-standing community convention rather than anything retail ships; UT2004
itself expects the global ini. A mod that carries a `make.ini` and a build script is
using it.

## EditPackages in the global ini

When a mod has no ini of its own, `System/UT2004.ini`, under `[Editor.EditorEngine]`:

```ini
EditPackages=MyMod
```

List the system packages plus **only the package currently being built**. Dependent
packages still have to be built together, but finished ones should be trimmed out:

- UCC loads every package named, including ones it skips compiling.
- UnrealEd loads every entry at startup, by name, and fails hard ("Can't find edit
  package") once a listed package is deleted.

Deleting `UT2004.ini` resets the list to defaults.

There is a separate reason to trim it: when importing content in UnrealEd, an entry for
the package being imported into makes the editor preload it, and the import can lose
materials. Remove the entry before importing.

## Where build output goes

```bash
mv MyPkg.u ../Textures/MyPkg.utx     # move, do not copy
```

`[Core.System]` in `UT2004.ini` lists `Paths=../System/*.u` **before**
`Paths=../Textures/*.utx`. A `.u` left in `System/` shadows the `.utx` of the same
package name, so the engine silently loads the stale one in preference to what
shipped. Copying instead of moving has produced gigabytes of duplicated packages and
builds that tested the wrong file.

This only matters for a package actually referenced at runtime. A map that embeds its
textures names the package nowhere, so its `.utx` is a build input rather than a
runtime asset.

## Server packages

A mutator whose code must run on clients sets, in `defaultproperties`:

```unrealscript
bAddToServerPackages=True
```

rather than adding a `ServerPackages=` line to the ini. See the `ut2004-netcode` skill.

## Compressing for a redirect

```bash
cd System && wine UCC.exe compress ..\Maps\DM-Example.ut2
```

Produces `DM-Example.ut2.uz2` beside the source. Two traps:

- **Forward-slash paths silently fail.** `../Maps/DM-Example.ut2` gives
  `Error occurred opening DM-Example.ut2` — note it echoes only the basename, having
  discarded the directory. Use backslashes, or a full `Z:\home\...` path.
- **The reported percentage overflows a signed 32-bit int** and goes negative once the
  compressed file passes roughly 21MB. `-15%` does not mean the file grew. Compare
  actual file sizes.

Maps compress to roughly 15–25% of original. `.utx` texture packages only reach 40–90%,
their contents already being compressed.

## A redirect needs the transitive closure

A map's import table names the packages it references directly, but those packages have
imports of their own. Walk package imports transitively when working out what to upload,
or clients hit a missing dependency that the map itself never named.
