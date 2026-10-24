"""Flashcards HTTP server.

A small, dependency-free REST API plus a static file server for the front end.

* :func:`route_request` is a pure function (no sockets) so the API can be
  tested directly and quickly.
* :func:`serve` wires that router into :mod:`http.server` and also serves the
  ``client/`` folder, so the whole app runs from one origin.

Run it with::

    python server/app.py            # http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

# Allow running this file directly (``python server/app.py``).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import CardStore, ValidationError  # noqa: E402

__all__ = ["route_request", "make_handler", "serve", "CLIENT_DIR"]

CLIENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "client")
CLIENT_DIR = os.path.abspath(CLIENT_DIR)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _first(query: dict, key: str) -> Optional[str]:
    values = query.get(key)
    return values[0] if values else None


def route_request(
    store: CardStore,
    method: str,
    path: str,
    query: Optional[dict] = None,
    body: bytes = b"",
) -> tuple[int, dict, object]:
    """Handle one API call and return ``(status, headers, payload)``.

    ``payload`` is a JSON-serialisable object for every response except
    ``204 No Content``, where it is ``None``.
    """
    query = query or {}
    headers = {"Access-Control-Allow-Origin": "*"}
    segments = [unquote(s) for s in path.strip("/").split("/") if s]
    method = method.upper()

    if segments[:1] != ["api"]:
        return 404, headers, {"error": "not found"}

    # /api/health
    if segments == ["api", "health"] and method == "GET":
        return 200, headers, {"status": "ok", "cards": store.count(), "due": store.due_count()}

    # /api/decks
    if segments == ["api", "decks"] and method == "GET":
        return 200, headers, {"decks": store.decks()}

    # /api/cards/due  (checked before /api/cards/<id>: "due" is not an id)
    if segments == ["api", "cards", "due"] and method == "GET":
        cards = store.due(deck=_first(query, "deck"))
        return 200, headers, {"cards": [card.to_dict() for card in cards]}

    # /api/cards
    if segments == ["api", "cards"]:
        if method == "GET":
            cards = store.list(deck=_first(query, "deck"))
            return 200, headers, {"cards": [card.to_dict() for card in cards]}

        if method == "POST":
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400, headers, {"error": "body must be valid JSON"}
            if not isinstance(data, dict):
                return 400, headers, {"error": "body must be a JSON object"}
            try:
                card = store.add(
                    front=data.get("front", ""),
                    back=data.get("back", ""),
                    deck=data.get("deck", "default"),
                )
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            return 201, headers, {"card": card.to_dict()}

        return 405, headers, {"error": "method not allowed"}

    # /api/cards/<id> and /api/cards/<id>/review
    if len(segments) in (3, 4) and segments[:2] == ["api", "cards"] and segments[2] != "due":
        raw_id = segments[2]
        if not raw_id.isdigit():
            return 400, headers, {"error": "id must be an integer"}
        card_id = int(raw_id)

        if len(segments) == 4:
            if segments[3] != "review":
                return 404, headers, {"error": "not found"}
            if method != "POST":
                return 405, headers, {"error": "method not allowed"}
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400, headers, {"error": "body must be valid JSON"}
            if not isinstance(data, dict):
                return 400, headers, {"error": "body must be a JSON object"}
            if "quality" not in data:
                return 422, headers, {"error": "quality is required"}
            try:
                card = store.review(card_id, data["quality"])
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            if card is None:
                return 404, headers, {"error": "card not found"}
            return 200, headers, {"card": card.to_dict()}

        if method == "GET":
            card = store.get(card_id)
            if card is None:
                return 404, headers, {"error": "card not found"}
            return 200, headers, {"card": card.to_dict()}

        if method == "DELETE":
            if store.delete(card_id):
                return 204, headers, None
            return 404, headers, {"error": "card not found"}

        return 405, headers, {"error": "method not allowed"}

    return 404, headers, {"error": "not found"}


def make_handler(store: CardStore):
    """Build a :class:`BaseHTTPRequestHandler` bound to ``store``."""

    class FlashcardsHandler(BaseHTTPRequestHandler):
        server_version = "Flashcards/1.0"

        # Silence the default per-request logging in tests.
        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            if os.environ.get("FLASHCARDS_LOG"):
                super().log_message(fmt, *args)

        def do_GET(self):  # noqa: N802 - stdlib naming
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_DELETE(self):  # noqa: N802
            self._dispatch("DELETE")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                status, headers, payload = route_request(
                    store, method, parsed.path, parse_qs(parsed.query), raw
                )
                self._send_json(status, payload, headers)
            elif method == "GET":
                self._serve_static(parsed.path)
            else:
                self._send_json(405, {"error": "method not allowed"}, {})

        def _send_json(self, status: int, payload: object, headers: dict) -> None:
            if payload is None:
                body = b""
            else:
                body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _serve_static(self, path: str) -> None:
            relative = unquote(path).lstrip("/") or "index.html"
            target = os.path.abspath(os.path.join(CLIENT_DIR, relative))
            # Refuse to escape the client directory.
            if not target.startswith(CLIENT_DIR + os.sep) and target != CLIENT_DIR:
                self._send_json(403, {"error": "forbidden"}, {})
                return
            if os.path.isdir(target):
                target = os.path.join(target, "index.html")
            if not os.path.isfile(target):
                self._send_json(404, {"error": "not found"}, {})
                return
            ext = os.path.splitext(target)[1].lower()
            with open(target, "rb") as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header("Content-Type", _CONTENT_TYPES.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return FlashcardsHandler


def serve(host: str = "127.0.0.1", port: int = 8000, db_path: str = ":memory:") -> None:
    """Start the server and block until interrupted."""
    store = CardStore(db_path)
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Flashcards serving on http://{host}:{port} (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
        store.close()


if __name__ == "__main__":
    host = os.environ.get("FLASHCARDS_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASHCARDS_PORT", "8000"))
    db_path = os.environ.get("FLASHCARDS_DB", ":memory:")
    serve(host=host, port=port, db_path=db_path)
