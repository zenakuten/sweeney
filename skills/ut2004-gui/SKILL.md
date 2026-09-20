---
name: ut2004-gui
description: UT2004 menus, HUD and Canvas drawing — GUI2K4/XInterface components, custom styles, scoreboards, and the Canvas quirks that make text and borders render wrong. Use when building or editing a mod menu, drawing on the HUD or a scoreboard, restyling GUI controls, or when a control renders at the wrong size, clips, or reads the wrong value.
---

# GUI, HUD and Canvas

Menus are `GUI2K4`/`XInterface` components; the HUD and scoreboards draw directly on
`Canvas`. Most bugs here are not logic errors — they are engine drawing behaviour that
does not do what the call name suggests.

## Canvas

### `DrawTextJustified` ignores `FontScaleX/Y`

`Canvas.FontScaleX`/`FontScaleY` (default 1.0) scale text and are honoured by
`DrawText`, `DrawTextClipped` and `StrLen`. **`DrawTextJustified` is not** — it forces
the scale to 1.0 internally.

So if you shrink a row by setting `FontScale`, everything scales except the justified
element, which stays full size and overflows.

Replace it with a manual centre, since `StrLen` does honour the scale:

```unrealscript
C.StrLen(Text, W, H);
C.SetPos(X1, (Y1 + Y2) / 2 - H / 2);    // to match DrawTextJustified(..,X1,Y1,X2,Y2)
C.DrawText(Text);                        // Justification 0 (left): X = X1
```

### `int += float` drifts

An accumulator that adds a fractional row pitch to an `int` loses the fraction every
iteration, so an accumulated column separates from one computed as `base + pitch * i`.
Worse with more rows and smaller steps. Declare it `float`. See the `unrealscript` skill.

## GUI styles

### A style texture taller than its element clips the border

Bordered GUI styles render through `Canvas.DrawTileStretched`, a 9-slice: native
corners, stretched edges and centre. It computes the stretched span as
`targetSize - textureSize`. **If the texture is taller than the element it is drawn on,
that span goes negative**, the top and bottom border slices cannot both fit, and the
bottom border renders clipped.

Symptom: a list selection highlight with its bottom border cut off — a 64×64 style
texture drawn on a ~23px list row.

Fix by shrinking the *style texture* below the smallest element it is ever drawn on, not
by adjusting the element. One case went from 64×64 to 16×8, keeping a 2px border and a
dark fill. That is resolution-robust and independent of pixel alignment.

Watch for the latent version: a plain dark-fill style with no hard border has the same
broken geometry, but it is invisible.

**Red herrings for this symptom** — forcing whole-pixel row heights via a
`GetItemHeight` delegate, and snapping the list's `WinTop`. Neither fixes it; it is a
per-element size problem, not a sub-pixel one.

## Porting a menu onto custom components

The pattern used to give a mod its own skin without depending on another mod:

- Copy the `STY_*` style classes and the component wrappers into the mod's own package
  and rewrite the package prefix on every reference. Base classes are engine
  (`GUI2K4`/`XInterface`), so nothing links back.
- Textures go in the mod's own `Textures/`, imported via `#exec TEXTURE IMPORT`.
- **Register every style in each menu's `InitComponent`** or controls fall back to stock.
- When converting a decompiled menu, swap `begin object ... class=XInterface.<stock>` to
  the custom class — **and repoint the inner `ComponentClassName` too**. A checkbox
  block carries its own `ComponentClassName`; miss it and the wrapper's cast fails
  silently, so the checkbox always reads false.

### Translucent styles break overlap tricks

A semi-transparent button style means controls that deliberately overlap — and relied on
opaque buttons to hide the one behind — now show through as doubled text. Fix with
explicit `bVisible` rather than relying on draw order or opacity.

## Scoreboards

The stock per-team arrays are `[32]` *each*, so 64 players fit the data; what overflows
is the drawing. The approach that worked is a `RowScale` that scales row pitch, every
intra-row Y offset, icon sizes and text, computed as a continuous auto-fit clamped to
1.0 — so a normal game is unchanged and rows shrink only as much as needed.

The trap: any **fixed pixel nudge** inside a row (`+16`, `-24` for sub-lines and badges)
must be multiplied by the scale too, or those elements drift into neighbouring rows
once compressed.

## More

- `references/canvas-and-styles.md` — the drawing quirks in full, with the workarounds
- `ut2004-packages` skill — importing the textures a style needs
