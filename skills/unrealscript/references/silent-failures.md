# Things that compile and then do nothing

Grouped by the symptom you are likely to see first. In every case the build is clean.

## "My actor never replicates"

**An enum property given an integer in `defaultproperties` is silently discarded.**

```unrealscript
RemoteRole=2                      // ignored -- stays at the inherited default
RemoteRole=ROLE_SimulatedProxy    // correct
```

The property keeps whatever it inherited, so an `Info` subclass stays `ROLE_None` and
the client never receives the actor. In ordinary code the same assignment is a loud
type error; only `defaultproperties` swallows it.

Worst on decompiled code: UE Explorer emits *every* enum default as a raw int. One
package had `RemoteRole`, `TextAlign`, `ImageStyle`, `ImageRenderStyle`, `FontScale`
and `ComponentJustification` all silently dropped at once.

`=0` is not automatically safe. `GUIMenuOption` defaults `ComponentJustification` to
`TXTA_Right`, so a dropped `=0` flips justification to left.

Note that ints *are* valid for genuine int properties. Only enums are affected, which
is why `uccheck.py` resolves the property's declared type through the class hierarchy
rather than matching on name.

## "Changing the ini does nothing"

**`var config` needs `config(Name)` on the class declaration.**

```unrealscript
class Foo extends Bar config(MyMod);
```

Without it the variables are read from the default game ini, the mod's `[Pkg.Class]`
section is inert, and the values stay at their `defaultproperties`. No compile warning,
no log line.

The nastiest version of this is a *diagnostic* flag that never turns on, which makes it
look like the code under test is broken rather than the switch.

Subclasses inherit the specifier, so this bites new standalone classes (an `Emitter`,
`Projector` or `WeaponPawn` subclass) rather than ones extending an already-config
class.

```bash
grep -L "config(" $(grep -rl "var()* *config" *.uc)
```

## "The build worked but the package will not save"

**A package cannot save a reference to a *private* object in another package.**

```
Can't save MyPkg.u: Graph is linked to external private object
```

It fails at save, not at compile, so the build looks fine right up to the end. Objects
carry a public flag; a name that exists in several packages may be public in one and
private in another, so resolving a reference by searching can pick an unsavable one.
Prefer an object the package already imports, then a public export.

## "Two things that share a spacing slowly drift apart"

**`int += float` truncates on every assignment.**

```unrealscript
local int Y;
Y += C.ClipY * 0.0275;      // advances by floor() each step, losing the fraction
```

The loss is permanent and cumulative, so an accumulated position separates from one
computed fresh (`base + pitch * i`). Worse with more iterations and smaller steps —
halving a row height doubles the per-row loss.

Declare the accumulator `float`, or compute the value fresh each iteration.

## "The compiler just sits there"

See the SKILL for the three triggers: a comment that leaves a quote open, the ternary
operator, and multi-byte UTF-8. Diagnose with:

```
cd System && ucc make          # timeout 60 wine UCC.exe make, on Linux
```

and read the last `Parsing` / `Compiling` line.
