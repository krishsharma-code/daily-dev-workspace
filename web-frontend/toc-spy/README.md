# toc-spy

A table of contents generated from the headings already in your page, with the entry for the
section the reader is currently looking at highlighted while they scroll.

Hand-written tables of contents go stale the moment a heading is renamed, and they never say
where the reader is. `toc-spy` reads the headings, gives them stable anchor ids, renders nested
navigation, and tracks the active section with a scroll listener that only does real work on
animation frames.

No dependencies, no build step: one ES module and one stylesheet.

## Files

```
toc-spy/
├── README.md
├── index.html         # runnable demo
├── toc-spy.js         # module: pure helpers + DOM controller
├── toc-spy.css        # default styles (all classes namespaced)
└── toc-spy.test.mjs   # unit tests for the pure helpers
```

## Try the demo

Open `index.html` in a browser. If your browser blocks module imports from `file://`, serve the
folder instead:

```bash
cd web-frontend/toc-spy
python3 -m http.server 8000   # then open http://localhost:8000/
```

Run the tests with Node 20+:

```bash
cd web-frontend/toc-spy
npm test          # node --test
```

## Usage

```html
<link rel="stylesheet" href="toc-spy.css" />

<article id="content">
  <h2>Getting started</h2>
  ...
  <h3>Install</h3>
</article>

<nav id="toc" class="toc-spy toc-spy--sticky" aria-label="On this page"></nav>

<script type="module">
  import { createTocSpy } from './toc-spy.js';

  const toc = createTocSpy({
    content: document.querySelector('#content'),
    nav: document.querySelector('#toc'),
    label: 'On this page',
    offset: 64, // height of your sticky header
  });

  // later, if the view is torn down:
  // toc.destroy();
</script>
```

Headings without an `id` get one assigned (`Getting started` → `getting-started`), so existing
deep links keep working and new ones start working immediately.

## `createTocSpy(options)`

| Option | Default | Meaning |
| --- | --- | --- |
| `content` | *required* | Element whose headings make up the outline. |
| `nav` | `null` | Element to render the outline into. Without it you get the tracking but no markup. |
| `headingSelector` | `'h2, h3'` | Which headings to include. |
| `minLevel` / `maxLevel` | `1` / `6` | Drop headings outside this range. |
| `offset` | `0` | Sticky-header height in pixels; shifts both the reading line and scroll targets. |
| `smooth` | `true` | Smooth-scroll on link click. |
| `updateHash` | `false` | Write `#id` to the URL with `history.replaceState` on click. |
| `ordered` | `false` | Render `<ol>` instead of `<ul>`. |
| `label` | `null` | Caption rendered above the outline. |
| `activeClass` | `'is-active'` | Class applied to the active link and its list item. |
| `onActive` | `null` | `(node, index) => void` called when the active section changes. |

Returns an object with:

- `refresh()` — re-read headings, re-render the nav and re-measure. Call it after adding or
  removing content yourself.
- `update()` — re-evaluate the active section from the current scroll position.
- `destroy()` — remove listeners, cancel a pending frame and disconnect the observer.
- `outline` — the nested outline currently rendered.
- `activeIndex` — index into the flat outline, `-1` when above the first heading.
- `nav` — the element passed in, for convenience.

## Pure helpers

The scroll logic is separated from the DOM so it can be tested in Node:

```js
import { slugify, buildOutline, flattenOutline, renderOutline, findActiveIndex } from './toc-spy.js';

slugify('Café & Crème');                     // 'cafe-and-creme'
buildOutline([{ text: 'Intro', level: 2 }, { text: 'Setup', level: 3 }]);
// { nodes: [{ id: 'intro', level: 2, depth: 0, children: [{ id: 'setup', ... }] }],
//   flat:  [ <intro>, <setup> ] }
flattenOutline(nodes);                       // depth-first, document order
renderOutline(nodes, { label: 'Contents' }); // escaped, nested <ul>/<li> markup
findActiveIndex([0, 400, 900], 650);         // 1
```

`findActiveIndex(offsets, scrollTop, { offset })` expects ascending offsets in document
coordinates and returns the last entry at or above `scrollTop + offset`, or `-1`. It binary
searches, so it stays cheap on very long documents.

## Behaviour notes

- **Duplicate headings.** `Setup` appearing three times becomes `setup`, `setup-2`, `setup-3`.
  Explicit ids are kept as-is and de-duplicated the same way.
- **Level jumps.** An `h4` directly under an `h2` nests under the `h2`; the next `h3` is a
  sibling of that `h4`, not its child.
- **End of page.** When the document is scrolled to the bottom the last section becomes active,
  since the final section is often too short to reach the reading line.
- **Re-measuring.** Offsets are re-read on `resize` and through a `ResizeObserver` on the content
  element (images loading, sections expanding). `ResizeObserver` is optional.
- **Accessibility.** Real anchors with `href="#id"`, `aria-current="location"` on the active
  link, visible focus rings, and `scroll-behavior` left to your CSS. Set `smooth: false` if you
  would rather not animate at all.
- **Multiple outlines.** Each controller is independent, so a sidebar and a mobile dropdown can
  track the same content separately.

## Browser support

Any browser with ES modules and `Element.closest` — current Chrome, Firefox, Safari and Edge.
