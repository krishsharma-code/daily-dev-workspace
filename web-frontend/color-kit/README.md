# color-kit

A small, dependency-free colour utility that runs unchanged in the browser and
in Node. It does the three things you usually reach for a colour library to do:
**parse** colours from any common notation, **convert and derive** new colours,
and **check contrast** for accessibility.

Every function is pure and works on plain objects, so there is no build step and
nothing to initialise. A colour is `{ r, g, b, a }` with `r`, `g`, `b` in
`0..255` and `a` in `0..1`; HSL is `{ h, s, l }` with `h` in `0..360` and `s`,
`l` as percentages.

## What it does

| Function | Purpose |
| --- | --- |
| `parseHex(str)` | Parse `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa` (the `#` is optional). |
| `parseRgb(str)` | Parse `rgb()`/`rgba()` in comma or space/slash form, including percentages. |
| `parseHsl(str)` | Parse `hsl()`/`hsla()`; returns an RGB colour. |
| `parseColor(input)` | Dispatch to the right parser, or accept an already-parsed object. |
| `toHex` / `toRgbString` / `toHslString` | Format a colour as a string (`#rrggbbaa` only when not fully opaque). |
| `rgbToHsl` / `hslToRgb` | Convert between the two models. |
| `rgb(r, g, b, a)` | Build a colour, clamping channels and alpha into range. |
| `withAlpha` | Return a copy with a new alpha. |
| `lighten` / `darken` | Move lightness by a number of percentage points, keeping hue. |
| `rotateHue` / `complement` | Spin the colour wheel, by an angle or by 180°. |
| `mix(c1, c2, weight)` | Blend two colours (`0` → `c1`, `1` → `c2`, default `0.5`). |
| `blendOver(fg, bg)` | Composite a translucent colour over an opaque one. |
| `relativeLuminance` | WCAG relative luminance (`0..1`). |
| `contrastRatio(c1, c2)` | WCAG contrast ratio (`1..21`). |
| `wcag(c1, c2, { large })` | Ratio plus `aa`/`aaa` conformance booleans. |
| `readableTextColor(bg)` | The more readable of black and white for text on `bg`. |

Parse functions throw a `TypeError` on input they cannot understand, rather than
returning a half-valid colour.

## Layout

```
web-frontend/color-kit/
├── README.md
├── color-kit.js          # implementation (ES module)
├── color-kit.css         # styles for the demo
├── index.html            # runnable interactive demo
├── package.json          # `npm test` runs the suite
└── color-kit.test.mjs    # node:test suite
```

## Try the demo

Because it is an ES module, serve the folder over HTTP rather than opening the
file directly (module scripts are blocked on `file://`):

```bash
cd web-frontend/color-kit
python3 -m http.server 8000
# then open http://localhost:8000/
```

Type any colour notation (or use the picker) and the page shows its HEX/RGB/HSL
forms, the contrast ratio against white, black and grey with AA/AAA badges, and
lighten/darken/mix/complement ramps.

## Run the tests

```bash
cd web-frontend/color-kit
node --test
```

No third-party dependencies are required at runtime; Node 18+ is enough to run
the tests.

## Example

```js
import {
  parseColor,
  toHex,
  contrastRatio,
  wcag,
  readableTextColor,
  lighten,
} from "./color-kit.js";

const brand = parseColor("hsl(210, 60%, 40%)");
toHex(brand);                 // "#2966a3"
toHex(lighten(brand, 20));    // a lighter tint, same hue

const ratio = contrastRatio(brand, "#ffffff");
wcag(brand, "#ffffff").aa;    // true — the pair passes AA for body text
readableTextColor(brand);     // "#ffffff" — white text reads best on this blue
```

## Behaviour notes

- **Alpha is preserved, not ignored.** `lighten`, `darken`, `rotateHue`,
  `complement` and `mix` carry the source alpha through; `blendOver` always
  returns an opaque colour, since it composites over an opaque background.
- **Non-finite alpha is treated as opaque.** `rgb(0, 0, 0, NaN)` and out-of-range
  alphas clamp to a sensible value (`NaN` → `1`) instead of poisoning the colour.
- **Hue wraps, saturation and lightness clamp.** `hslToRgb({ h: 480, … })` is the
  same as `hslToRgb({ h: 120, … })`; `s`/`l` outside `0..100` are clamped.
- **Contrast follows WCAG 2.1.** Luminance uses the sRGB linearisation, the ratio
  is `(L1 + 0.05) / (L2 + 0.05)`, and `wcag` applies the 4.5:1 / 3:1 (AA) and
  7:1 / 4.5:1 (AAA) thresholds for normal and large text respectively.
