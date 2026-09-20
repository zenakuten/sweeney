# Canvas and GUI style details

## FontScale coverage

| Call | Honours `FontScaleX/Y` |
|---|---|
| `DrawText` | yes |
| `DrawTextClipped` | yes |
| `StrLen` | yes |
| `DrawTextJustified` | **no** — forced to 1.0 |

Because `StrLen` honours the scale, a manual centre reproduces `DrawTextJustified`
faithfully at any scale:

```unrealscript
// equivalent of DrawTextJustified(Text, Just, X1, Y1, X2, Y2) at arbitrary FontScale
C.StrLen(Text, TextW, TextH);
Y = (Y1 + Y2) / 2 - TextH / 2;
switch (Just)
{
    case 0:  X = X1;                          break;   // left
    case 1:  X = (X1 + X2) / 2 - TextW / 2;   break;   // centre
    case 2:  X = X2 - TextW;                  break;   // right
}
C.SetPos(X, Y);
C.DrawText(Text);
```

## DrawTileStretched and 9-slice

The 9-slice draws native-size corners, stretches the four edges, and stretches the
centre. The stretched span in each axis is `target - texture`. Negative span means the
two border slices — each half the texture size — cannot both fit in the element, and the
trailing border is clipped.

So the constraint is simply:

> **a bordered style texture must be smaller than the smallest element it will ever be
> drawn on**, in both axes.

A list row at 1080p is around 23px tall, so a 64×64 style texture cannot work there.
16×8 does, and still carries a 2px border.

When auditing a style set, check every style against the shortest element it is applied
to. Styles with no hard border hide the same breakage — worth fixing if the fill ever
gains a border.

## Converting a decompiled menu

```unrealscript
begin object class=XInterface.GUICheckBoxButton name=MyCheck
    ...
end object
```

Two places name a class, and both must be repointed:

1. the `class=` on the `begin object` line
2. an inner `ComponentClassName="..."` inside wrapper controls

Missing the second leaves the wrapper's cast to its component failing silently — the
control draws, and always reads its default value. A checkbox that is permanently false
is the usual sign.

Register the styles in `InitComponent`:

```unrealscript
Controller.AddStyle(...)     // or the menu's own style-registration path
```

Unregistered styles do not error; the control just renders stock, which reads as "my
skin did not apply".

## Scoreboard scaling

```unrealscript
RowScale = FMin(1.0, (BottomLimit - TopLimit) / (RowHeight * NumRows));
```

Clamping at 1.0 makes it a no-op for ordinary player counts, so existing behaviour is
untouched. Apply it to:

- the row pitch
- every Y offset *within* a row
- icon and badge sizes
- text, via `Canvas.FontScaleX/Y` — remembering `DrawTextJustified` will ignore it
- **every hardcoded pixel nudge**, which is the one that gets missed

Known remaining gaps in the WSUTComp family: the CTF board is a separate richer layout
capped at 8 per team, the non-team DM board is capped by fixed `[32]` arrays, and the
spectator array is filled unbounded so more than 32 spectators overflow it. A live
server's player count can exceed those array bounds.
