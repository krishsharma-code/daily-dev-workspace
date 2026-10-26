# web-frontend/

Standalone front-end work: HTML, CSS, and modern JavaScript components and utilities that run in the browser without a build step.

## Modules

- [`color-kit/`](color-kit/) — dependency-free colour parsing, conversion, blending and WCAG contrast utilities.
- [`fuzzy-matcher/`](fuzzy-matcher/) — fuzzy subsequence matching and ranking for typeahead search.
- [`undo-stack/`](undo-stack/) — undo/redo history manager with keystroke coalescing and transactions.

## Conventions

```
web-frontend/<module-name>/
├── README.md      # what it does and how to use it
├── index.html     # runnable demo / usage example
├── <module>.js    # the component or utility
└── <module>.css   # styles, if needed
```

Open `index.html` in a browser to try a module, or import the JavaScript module directly.
