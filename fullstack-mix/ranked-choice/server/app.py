"""Ranked Choice HTTP server.

A dependency-free local service for instant-runoff counts.

* ``route_request`` is a pure function (no sockets), so the API is cheap to
  test and easy to reason about.
* ``serve`` wires it into :mod:`http.server` and also serves ``client/``, so
  the whole app runs from a single origin.

Run it with::

    python server/app.py                 # http://127.0.0.1:8021
    python server/app.py --port 9100
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import unquote, urlparse

# Allow running this file directly (``python server/app.py``).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import InputError, parse_candidates, run  # noqa: E402

__all__ = [
    "CLIENT_DIR",
    "EXAMPLE_REQUEST",
    "make_handler",
    "route_request",
    "serve",
]

CLIENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "client")
)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

#: The payload the client offers as "load example": a book-club shortlist with
#: one voter who ranks a single title (their ballot exhausts) and one two-way
#: contest that a transfer decides.
EXAMPLE_REQUEST = {
    "candidates": ["Bengaluru", "Goa", "Jaipur", "Kochi"],
    "ballots": "\n".join(
        [
            "# 10 voters choosing the team offsite",
            "Bengaluru > Goa > Jaipur > Kochi",
            "Bengaluru > Jaipur > Goa > Kochi",
            "Bengaluru > Jaipur > Goa",
            "Bengaluru > Goa > Jaipur",
            "Goa > Bengaluru > Jaipur",
            "Goa > Bengaluru > Kochi",
            "Goa > Kochi > Bengaluru",
            "Jaipur > Goa > Bengaluru",
            "Jaipur > Bengaluru > Goa",
            "# one voter only names one option, so this ballot exhausts",
            "Kochi",
        ]
    ),
}

_MAX_BODY_BYTES = 1 << 20  # 1 MiB of pasted ballots is plenty


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def _json_object(body: bytes) -> tuple[Optional[dict], Optional[dict]]:
    """Parse a JSON object body. Returns ``(data, error_payload)``."""
    if not body.strip():
        return {}, None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, {"error": "body must be valid JSON"}
    if not isinstance(data, dict):
        return None, {"error": "body must be a JSON object"}
    return data, None


def _build_call(data: dict) -> dict:
    """Translate a request body into ``core.run`` keyword arguments."""
    ballots = data.get("ballots", "")
    if ballots is None:
        ballots = ""
    if not isinstance(ballots, str):
        raise InputError("ballots: expected a string of ranked lines")

    candidates = data.get("candidates")
    # Validated here so a bad roster is reported even with no ballots.
    parse_candidates(candidates)

    return {"text": ballots, "candidates": candidates}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_request(
    method: str,
    path: str,
    body: bytes = b"",
) -> tuple[int, dict, object]:
    """Handle one request and return ``(status, headers, payload)``."""
    method = (method or "GET").upper()
    segments = [unquote(part) for part in path.strip("/").split("/") if part]
    headers = {"Access-Control-Allow-Origin": "*"}

    if method == "OPTIONS":
        return 204, headers, None

    if segments[:1] != ["api"]:
        return 404, headers, {"error": "not found"}

    if segments == ["api", "health"]:
        if method != "GET":
            return 405, headers, {"error": "method not allowed"}
        return 200, headers, {"status": "ok", "service": "ranked-choice"}

    if segments == ["api", "example"]:
        if method != "GET":
            return 405, headers, {"error": "method not allowed"}
        return 200, headers, {"request": EXAMPLE_REQUEST}

    if segments == ["api", "tabulate"]:
        if method != "POST":
            return 405, headers, {"error": "method not allowed"}
        if len(body) > _MAX_BODY_BYTES:
            return 413, headers, {"error": "body is too large"}
        data, error = _json_object(body)
        if error:
            return 400, headers, error
        try:
            result = run(**_build_call(data))
        except InputError as exc:
            return 400, headers, {"error": str(exc)}
        return 200, headers, result

    return 404, headers, {"error": "not found"}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def make_handler():
    """Build a request handler bound to the routing table above."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "RankedChoice/1.0"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            pass

        def _read_body(self) -> bytes:
            length = self.headers.get("Content-Length") or "0"
            try:
                size = int(length)
            except ValueError:
                return b""
            if size <= 0:
                return b""
            return self.rfile.read(min(size, _MAX_BODY_BYTES + 1))

        def _send_json(self, status: int, payload: object, headers: dict) -> None:
            encoded = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(encoded)

        def _serve_static(self, path: str) -> None:
            relative = path.strip("/") or "index.html"
            target = os.path.abspath(os.path.join(CLIENT_DIR, relative))
            if not target.startswith(CLIENT_DIR + os.sep) and target != CLIENT_DIR:
                self._send_json(403, {"error": "forbidden"}, {})
                return
            if os.path.isdir(target):
                target = os.path.join(target, "index.html")
            if not os.path.isfile(target):
                self._send_json(404, {"error": "not found"}, {})
                return
            with open(target, "rb") as handle:
                payload = handle.read()
            self.send_response(200)
            extension = os.path.splitext(target)[1].lower()
            self.send_header(
                "Content-Type", _CONTENT_TYPES.get(extension, "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            body = self._read_body() if method in {"POST", "PUT", "PATCH"} else b""
            if parsed.path.startswith("/api/"):
                status, headers, payload = route_request(method, parsed.path, body)
                if payload is None:
                    self.send_response(status)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    return
                self._send_json(status, payload, headers)
                return
            if method in {"GET", "HEAD"}:
                self._serve_static(parsed.path)
                return
            self._send_json(405, {"error": "method not allowed"}, {})

        def do_GET(self):  # noqa: N802 - stdlib naming
            self._dispatch("GET")

        def do_HEAD(self):  # noqa: N802
            self._dispatch("HEAD")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_OPTIONS(self):  # noqa: N802
            self._dispatch("OPTIONS")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8021) -> ThreadingHTTPServer:
    """Start the server and return it (already bound)."""
    httpd = ThreadingHTTPServer((host, port), make_handler())
    print(f"Ranked Choice on http://{host}:{port}  (Ctrl-C to stop)", flush=True)
    return httpd


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Count ranked-choice ballots.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8021)
    args = parser.parse_args(argv)

    httpd = serve(args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
