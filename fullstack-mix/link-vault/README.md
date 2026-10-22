# Link Vault

A tiny, self-hosted bookmark manager. Save links with tags, search them, and
filter by tag. The whole thing runs on the Python standard library — no
framework, no build step, no third-party packages.

```
link-vault/
├── README.md
├── client/            # front end (HTML + CSS + vanilla JS)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/            # back end (stdlib HTTP server + SQLite)
    ├── app.py         # router + static file server
    ├── store.py       # SQLite-backed bookmark store
    ├── example.py     # runnable tour of the API (no sockets)
    └── tests/         # pytest suite
```

## Run it

From this folder:

```bash
python server/app.py
```

Then open <http://127.0.0.1:8000>. The server serves the API *and* the front
end from the same origin, so nothing else is needed.

Configuration via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LINK_VAULT_HOST` | `127.0.0.1` | Bind address |
| `LINK_VAULT_PORT` | `8000` | Port |
| `LINK_VAULT_DB` | `:memory:` | SQLite path (e.g. `links.db` to persist) |

To persist bookmarks between runs:

```bash
LINK_VAULT_DB=links.db python server/app.py
```

You can also open `client/index.html` directly in a browser; when it is loaded
from `file://` it falls back to talking to `http://localhost:8000`.

## HTTP API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness check and link count |
| `GET` | `/api/links` | List links, newest first. Optional `?q=` and `?tag=` |
| `POST` | `/api/links` | Create a link from `{ "url", "title", "tags" }` |
| `GET` | `/api/links/{id}` | Fetch one link |
| `DELETE` | `/api/links/{id}` | Delete a link |

Examples:

```bash
curl -s localhost:8000/api/links \
  -H 'Content-Type: application/json' \
  -d '{"url":"news.ycombinator.com","title":"Hacker News","tags":["news","tech"]}'

curl -s 'localhost:8000/api/links?tag=tech'
curl -s 'localhost:8000/api/links?q=hacker'
```

`url` is required. A bare host such as `news.ycombinator.com` is upgraded to
`https://`, and only `http`/`https` URLs with a host are accepted. Tags are
lower-cased, trimmed and de-duplicated. If `title` is blank it defaults to the
URL's host.

## Test it

```bash
pip install pytest
cd server && python -m pytest -q
```

The suite covers the store (validation, filtering, persistence) and the HTTP
router (every route and status code), plus one end-to-end test that boots a real
server on an ephemeral port and exercises it over HTTP.

## Try the server logic without a socket

```bash
python server/example.py
```
