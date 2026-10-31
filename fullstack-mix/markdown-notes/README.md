# Markdown Notes

A small, self-hosted Markdown notebook: a stdlib-only Python server with a
vanilla HTML/CSS/JS client. Notes live in SQLite, are written in Markdown, and
are previewed live as you type.

The interesting part is the renderer in `server/markdown.py`. Notes are
rendered to HTML that the browser inserts into the page, so the renderer is
escape-first: every byte of input is HTML-escaped *before* any markup is
generated, and links are only emitted for `http`, `https` and `mailto`. Raw
HTML in a note shows up as literal text, and `javascript:` / `data:` URLs are
refused rather than rewritten.

## Layout

```
markdown-notes/
├── README.md
├── client/
│   ├── index.html      # three-pane layout: list, editor, preview
│   ├── app.js          # list/editor state, debounced live preview, Ctrl/Cmd+S
│   └── styles.css
└── server/
    ├── app.py          # HTTP router + static file server (no sockets in tests)
    ├── markdown.py     # escape-first Markdown subset renderer
    ├── store.py        # SQLite note storage and validation
    └── tests/          # pytest suite
```

## Running it

No dependencies beyond the Python 3.10+ standard library.

```bash
cd server
python app.py                       # http://127.0.0.1:8000
```

The server serves the API under `/api/` and the `client/` folder from the same
origin, so there is nothing else to start. Notes are stored in `notes.db` next
to the server; override with `NOTES_DB=/tmp/notes.db` and change the port with
`PORT=9000`.

## Running the tests

```bash
cd server
pip install pytest
python -m pytest -q
```

The suite covers the renderer (including XSS and bad-scheme cases), the store's
validation and search behaviour, every route in the router, and an end-to-end
pass over a real HTTP server.

## API

| Method | Path              | Purpose                                        |
| ------ | ----------------- | ---------------------------------------------- |
| GET    | `/api/health`     | Liveness plus the current note count.          |
| GET    | `/api/notes`      | List notes, newest first. `?q=` filters title and body. |
| POST   | `/api/notes`      | Create a note from `{title, body}`.            |
| GET    | `/api/notes/<id>` | Fetch one note.                                |
| PUT    | `/api/notes/<id>` | Update `title` and/or `body`.                  |
| DELETE | `/api/notes/<id>` | Delete a note (`204`).                         |
| POST   | `/api/preview`    | Render `{text}` to `{html, headings}` without saving. |

A note with no title inherits one from its first non-empty line. Titles are
capped at 200 characters and bodies at 100,000.

## Supported Markdown

Headings (`#` … `######`, with slugged `id`s collected into the outline),
fenced code blocks, block quotes, nested ordered/unordered lists, horizontal
rules, paragraphs; inline code spans, bold, italic, strikethrough, links,
images and `<https://…>` autolinks.

Deliberately not supported: raw HTML, tables, footnotes, reference-style links.

## Try it from the command line

```bash
curl -s -X POST localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"text":"# Hello\n\n- **bold** item\n- [docs](https://example.com)"}'
```
