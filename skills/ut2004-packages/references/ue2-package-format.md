# Reading and rebuilding UE2 packages

## Parsing

A UE2 package (UT2004 is package version 128) is a name table, an import table and an
export table, followed by export data.

**The compact index** is the encoding everything depends on, and most documentation
gets it wrong:

```
byte 0:  bit7 = sign, bit6 = more, bits 0-5 = value
byte n:  bit7 = more,              bits 0-6 = value
```

Docs commonly state bit7=more and bit6=sign for the first byte. That yields negative
garbage indices. Sanity check a parser by decoding `import[0]`, which must come out as
`('Core', 'Package', 'Core')`.

## The tagged-bool trap

In tagged property serialisation, a bool's **value** rides in the info byte's high bit —
the same bit that means "array index follows" for other types — **but the size field is
still written and must still be read.**

Skipping it desyncs the byte stream and every property after the bool is **silently
lost**. There is no error; the property list simply appears to end early.

Symptom to recognise: a property list that ends suspiciously early, always right after a
bool, and an object that looks defaulted when it is not.

Decoding one by hand: info byte `0xd3` is kind 3 (bool), size-code 5 (read a byte for
the size), high bit set (value true).

This hid two things for a long time — that a static mesh's material slots are an
ordinary tagged array and can be read straight from the package with no external tool,
and that a set of colour modifiers that looked like flat colours were really wrapping and
tinting a texture.

## Cross-package references must be public

Saving a package that references a **private** object in another package fails at
`SavePackage`, not at compile:

```
Can't save MyPkg.u: Graph is linked to external private object
```

Exports carry a public flag. A generic name can exist in several packages, public in one
and private in another, so resolving a reference by searching can pick an unsavable one —
and adding an unrelated package to the search order can break a build that had worked for
weeks.

Rank candidates: an entry in the map's own **import** table first (the map already
references it, so it must resolve), then a **public** export. Never offer a private one.
A slot that still cannot be resolved is better dropped with a log line than written — a
missing specular is cosmetic, a package that will not save is not.

## Exporting meshes

**`UCC batchexport` aborts fatally on the first Epic-authored mesh it may not export**,
and every mesh after it is lost — 10 of 28 on one map, with a misleading error:

```
Error exporting StaticMesh DM-1on1-Lea.idomacrate: couldn't open file <path>
```

Nothing to do with the filesystem: the exporter writes nothing for a restricted mesh, the
save of an empty string reports failure, and the commandlet turns that into a fatal error.

**Workaround: create an empty file at the reported path and re-run.** The exporter's
"identical, skip" check compares the empty buffer against the empty file, agrees, and
moves on. Repeat until the run completes.

umodel has no such restriction and exports every mesh — but not smoothing groups, so
both exporters are needed for a full round trip.

## Reading a umodel dump

umodel prints the same field two ways depending on length:

```
Materials[0] = { Material=Texture'trim.dwtrim_base', EnableCollision=true }

Materials[0] =
{
    Material = Shader'DetailArchitecture.MetlPart_U00G425v2Final'
    EnableCollision = true
}
```

A parser that matches only the compact shape returns **no slots at all** for any mesh
with a long material path. The damage is silent and compounding: the material is never
processed, nothing is accrued for it, and the carried mesh ends up naming a material
that does not exist in the rebuild, rendering with the editor's default texture.

Read both shapes, keying the expanded form on the most recently seen `Materials[n] =`
header — the array header always precedes its elements, so "last index seen" identifies
the element being described at any nesting depth.

Audit afterwards: for each mesh material leaf, check the name (or `<name>_SH`) exists in
some built package. A material with no counterpart is a mesh that will render untextured.

## Redirect file lists need the transitive closure

Building a redirect list from each map's top-level package imports is wrong. Shared
packages import *each other* — a shader wrapper points at a detail texture package that
the map itself never names — so a map's import table alone misses real dependencies.

Walk package imports transitively, or clients hit a missing dependency the map never
mentioned.
