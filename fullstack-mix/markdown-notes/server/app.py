"""Markdown Notes HTTP server.

A dependency-free REST API plus a static file server for the front end.

* ``route_request`` is a pure function (no sockets) so the API can be tested
  directly and quickly.
* ``serve`` wires that router into :mod:`http.server` and also serves the
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

import markdown  # noqa: E402
from store import NoteStore, ValidationError  # noqa: E402

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


def _json_body(body: bytes) -> tuple[Optional[dict], Optional[str]]:
    """Decode a request body, returning ``(data, error)``."""
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "body must be valid JSON"
    if not isinstance(data, dict):
        return None, "body must be a JSON object"
    return data, None


def route_request(
    store: NoteStore,
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
        return 200, headers, {"status": "ok", "notes": store.count()}

    # /api/preview
    if segments == ["api", "preview"]:
        if method != "POST":
            return 405, headers, {"error": "method not allowed"}
        data, error = _json_body(body)
        if error:
            return 400, headers, {"error": error}
        text = data.get("text", "")
        if not isinstance(text, str):
            return 422, headers, {"error": "text must be a string"}
        if len(text) > 200_000:
            return 422, headers, {"error": "text is too long"}
        result = markdown.render_document(text)
        return 200, headers, {"html": result.html, "headings": result.headings}

    # /api/notes
    if segments == ["api", "notes"]:
        if method == "GET":
            text = (query.get("q") or [None])[0]
            notes = store.list(query=text)
            return 200, headers, {"notes": [note.to_dict() for note in notes]}

        if method == "POST":
            data, error = _json_body(body)
            if error:
                return 400, headers, {"error": error}
            try:
                note = store.add(
                    title=data.get("title", ""),
                    body=data.get("body", ""),
                )
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            return 201, headers, {"note": note.to_dict()}

        return 405, headers, {"error": "method not allowed"}

    # /api/notes/<id>
    if len(segments) == 3 and segments[:2] == ["api", "notes"]:
        raw_id = segments[2]
        if not raw_id.isdigit():
            return 400, headers, {"error": "id must be an integer"}
        note_id = int(raw_id)

        if method == "GET":
            note = store.get(note_id)
            if note is None:
                return 404, headers, {"error": "note not found"}
            return 200, headers, {"note": note.to_dict()}

        if method == "PUT":
            data, error = _json_body(body)
            if error:
                return 400, headers, {"error": error}
            try:
                note = store.update(
                    note_id,
                    title=data.get("title"),
                    body=data.get("body"),
                )
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            if note is None:
                return 404, headers, {"error": "note not found"}
            return 200, headers, {"note": note.to_dict()}

        if method == "DELETE":
            if store.delete(note_id):
                return 204, headers, None
            return 404, headers, {"error": "note not found"}

        return 405, headers, {"error": "method not allowed"}

    return 404, headers, {"error": "not found"}


def make_handler(store: NoteStore):
    """Build a :class:`BaseHTTPRequestHandler` bound to ``store``."""

    class MarkdownNotesHandler(BaseHTTPRequestHandler):
        server_version = "MarkdownNotes/1.0"

        # Silence the default per-request logging unless explicitly asked.
        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            if os.environ.get("MARKDOWN_NOTES_LOG"):
                super().log_message(fmt, *args)

        def do_GET(self):  # noqa: N802 - stdlib naming
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self):  # noqa: N802
            self._dispatch("PUT")

        def do_DELETE(self):  # noqa: N802
            self._dispatch("DELETE")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
            )
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
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
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
            self.send_header(
                "Content-Type", _CONTENT_TYPES.get(ext, "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return MarkdownNotesHandler


def serve(host: str = "127.0.0.1", port: int = 8000, db_path: str = "notes.db") -> None:
    """Start the server and block until interrupted."""
    store = NoteStore(db_path)
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Markdown Notes serving on http://{host}:{port} (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
        store.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    db = os.environ.get("NOTES_DB", "notes.db")
    serve(port=port, db_path=db)
