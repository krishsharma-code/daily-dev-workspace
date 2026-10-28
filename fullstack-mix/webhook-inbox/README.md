# Webhook Inbox

A tiny, self-hosted inbox for webhooks you are developing against. Point any
sender at `http://127.0.0.1:8000/in/<bin>`; the request is captured as-is, and
the bundled client lets you browse, inspect and replay it.

No dependencies — just the Python standard library and plain HTML/CSS/JS.

## Why

While wiring up a webhook integration you usually cannot see what the provider
actually sent: headers, signature, content type, the exact body. This tool
records each delivery so you can look at the real payload, then replay it into
your local endpoint as many times as you need while you fix your handler.

## Layout

```
webhook-inbox/
├── README.md
├── client/          # vanilla HTML/CSS/JS inspector (no build step)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/          # stdlib HTTP server + SQLite storage
    ├── app.py       # routing, replay, static file serving
    ├── store.py     # bins and events
    ├── conftest.py
    └── tests/
        ├── test_api.py
        └── test_store.py
```

## Run it

```bash
python server/app.py                  # http://127.0.0.1:8000, in-memory database
python server/app.py --db inbox.db    # persist across restarts
python server/app.py --port 9000
```

Then open <http://127.0.0.1:8000> and send it something:

```bash
curl -X POST http://127.0.0.1:8000/in/stripe-test \
  -H 'Content-Type: application/json' \
  -H 'X-Signature: t=123,v1=abc' \
  -d '{"type": "payment_intent.succeeded", "amount": 4200}'
```

The event appears in the `stripe-test` bin immediately. Any HTTP method works
(`POST`, `PUT`, `PATCH`, `DELETE`, `GET`), and any sub-path under the bin is
accepted — `/in/stripe-test/foo/bar` is captured with its full path intact, so
you can mimic a provider that routes on path.

Bins are created automatically on the first delivery, so you can start sending
before you create anything. Bin names are lowercased and limited to 1–64
characters of letters, digits, `-` and `_`.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| any | `/in/<bin>[/...]` | Capture a delivery. Returns `202 {"event_id", "bin"}` |
| `GET` | `/api/health` | `{"status": "ok", "bins": n, "events": n}` |
| `GET` | `/api/bins` | Bins with event counts and last-received time |
| `POST` | `/api/bins` | Create a bin: `{"name": "stripe-test"}` (201 new, 200 existing) |
| `DELETE` | `/api/bins/<bin>` | Delete a bin and all its events (204) |
| `POST` | `/api/bins/<bin>/clear` | Delete a bin's events but keep the bin |
| `GET` | `/api/events?bin=&limit=&before_id=` | Newest-first summaries (no full bodies) |
| `GET` | `/api/events/<id>` | One event, with body, headers and size |
| `DELETE` | `/api/events/<id>` | Delete one event (204) |
| `POST` | `/api/events/<id>/replay` | Re-send the event: `{"target": "http://..."}` |

`limit` defaults to 50 and is clamped to 200. `before_id` pages backwards
through older events.

Replay forwards the stored method, body and headers (minus hop-by-hop headers
such as `host` and `content-length`) and reports what the receiver answered:

```bash
curl -X POST http://127.0.0.1:8000/api/events/1/replay \
  -H 'Content-Type: application/json' \
  -d '{"target": "http://localhost:3000/webhook"}'
# {"event_id": 1, "target": "...", "upstream_status": 204, "upstream_body": ""}
```

## Details worth knowing

- Bodies are stored as bytes. Anything over 256 KB is truncated on capture and
  the event is flagged `truncated`, so one noisy sender cannot bloat the
  database. `body_size` always reports the original size.
- Bodies that are not valid UTF-8 are returned base64-encoded with
  `"body_encoding": "base64"`, so binary payloads survive the round trip.
- Header names are lowercased; repeated headers are joined with `, `.
- The list endpoint returns a 200-character `body_preview` instead of the full
  body, which keeps the polling client light.

## Tests

```bash
cd server && python -m pytest -q
```

`route_request` is a pure function, so most of the API is tested without
sockets; one end-to-end test boots a real `ThreadingHTTPServer` to check the
capture endpoint, the API and static file serving together.

## Scope

This is a local development tool. It binds to `127.0.0.1` by default, has no
authentication, and replay will send a request to whatever URL you type. Do not
expose it to an untrusted network.
