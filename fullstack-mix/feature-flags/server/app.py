"""Feature Flags HTTP server.

A small, dependency-free feature-flag service for local development: define a
flag, give it a rollout percentage and an allowlist, then ask the server
whether a given user should see it.

* ``route_request`` is a pure function (no sockets), so the API is cheap to
  test and easy to reason about.
* ``serve`` wires it into :mod:`http.server` and also serves ``client/``, so
  the whole app runs from a single origin.

Run it with::

    python server/app.py                  # http://127.0.0.1:8010
    python server/app.py --db flags.db    # persist across restarts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

# Allow running this file directly (``python server/app.py``).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import (  # noqa: E402
    FlagStore,
    ValidationError,
)

__all__ = [
    "CLIENT_DIR",
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


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def _first(query: dict, key: str, default=None):
    values = query.get(key)
    if not values:
        return default
    if isinstance(values, str):
        return values
    return values[0]


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


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_request(
    store: FlagStore,
    method: str,
    path: str,
    query: Optional[dict] = None,
    body: bytes = b"",
) -> tuple[int, dict, object]:
    """Handle one request and return ``(status, headers, payload)``.

    ``payload`` is JSON-serialisable for every response except ``204 No
    Content``, where it is ``None``.
    """
    query = query or {}
    method = (method or "GET").upper()
    segments = [unquote(part) for part in path.strip("/").split("/") if part]
    headers = {"Access-Control-Allow-Origin": "*"}

    if segments[:1] != ["api"]:
        return 404, headers, {"error": "not found"}

    # -- /api/health -------------------------------------------------------
    if segments == ["api", "health"]:
        if method != "GET":
            return 405, headers, {"error": "method not allowed"}
        return 200, headers, {"status": "ok", "flags": store.count_flags()}

    # -- /api/flags --------------------------------------------------------
    if segments == ["api", "flags"]:
        if method == "GET":
            flags = [flag.to_dict() for flag in store.list_flags()]
            return 200, headers, {"flags": flags, "count": len(flags)}
        if method == "POST":
            data, error = _json_object(body)
            if error is not None:
                return 400, headers, error
            try:
                flag, created = store.put_flag(data)
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            return (201 if created else 200), headers, {"flag": flag.to_dict()}
        return 405, headers, {"error": "method not allowed"}

    # -- /api/flags/<key> --------------------------------------------------
    if len(segments) == 3 and segments[:2] == ["api", "flags"]:
        key = segments[2]
        if method == "GET":
            try:
                flag = store.get_flag(key)
            except ValidationError as exc:
                return 400, headers, {"error": str(exc)}
            if flag is None:
                return 404, headers, {"error": "flag not found"}
            return 200, headers, {"flag": flag.to_dict()}
        if method == "PATCH":
            data, error = _json_object(body)
            if error is not None:
                return 400, headers, error
            try:
                flag = store.update_flag(key, data)
            except KeyError:
                return 404, headers, {"error": "flag not found"}
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            return 200, headers, {"flag": flag.to_dict()}
        if method == "DELETE":
            try:
                deleted = store.delete_flag(key)
            except ValidationError as exc:
                return 400, headers, {"error": str(exc)}
            if not deleted:
                return 404, headers, {"error": "flag not found"}
            return 204, headers, None
        return 405, headers, {"error": "method not allowed"}

    # -- /api/evaluate -----------------------------------------------------
    if segments == ["api", "evaluate"]:
        if method == "POST":
            data, error = _json_object(body)
            if error is not None:
                return 400, headers, error
            key = data.get("key")
            user_id = data.get("user_id")
        elif method == "GET":
            key = _first(query, "key")
            user_id = _first(query, "user_id")
        else:
            return 405, headers, {"error": "method not allowed"}

        if key is None or user_id is None:
            return 422, headers, {"error": "key and user_id are required"}
        try:
            decision = store.evaluate(key, user_id)
        except KeyError:
            return 404, headers, {"error": "flag not found"}
        except ValidationError as exc:
            return 422, headers, {"error": str(exc)}
        return 200, headers, decision

    # -- /api/evaluate/all -------------------------------------------------
    if segments == ["api", "evaluate", "all"]:
        if method != "GET":
            return 405, headers, {"error": "method not allowed"}
        user_id = _first(query, "user_id")
        if user_id is None:
            return 422, headers, {"error": "user_id is required"}
        try:
            decisions = store.evaluate_all(user_id)
        except ValidationError as exc:
            return 422, headers, {"error": str(exc)}
        return 200, headers, {
            "user_id": user_id.strip(),
            "flags": decisions,
            "count": len(decisions),
        }

    return 404, headers, {"error": "not found"}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def make_handler(store: FlagStore):
    """Build a :class:`BaseHTTPRequestHandler` bound to ``store``."""

    class FeatureFlagsHandler(BaseHTTPRequestHandler):
        server_version = "FeatureFlags/1.0"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            if os.environ.get("FEATURE_FLAGS_LOG"):
                super().log_message(fmt, *args)

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                status, headers, payload = route_request(
                    store, method, parsed.path, parse_qs(parsed.query), raw
                )
                self._send_json(status, payload, headers)
            elif method in ("GET", "HEAD"):
                self._serve_static(parsed.path)
            else:
                self._send_json(405, {"error": "method not allowed"}, {})

        def do_GET(self):  # noqa: N802 - stdlib naming
            self._dispatch("GET")

        def do_HEAD(self):  # noqa: N802
            self._dispatch("HEAD")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_PATCH(self):  # noqa: N802
            self._dispatch("PATCH")

        def do_DELETE(self):  # noqa: N802
            self._dispatch("DELETE")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"
            )
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send_json(self, status: int, payload: object, headers: dict) -> None:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def _serve_static(self, path: str) -> None:
            relative = unquote(path).lstrip("/") or "index.html"
            target = os.path.abspath(os.path.join(CLIENT_DIR, relative))
            if not target.startswith(CLIENT_DIR + os.sep) and target != CLIENT_DIR:
                self._send_json(403, {"error": "forbidden"}, {})
                return
            if os.path.isdir(target):
                target = os.path.join(target, "index.html")
            if not os.path.isfile(target):
                self._send_json(404, {"error": "not found"}, {})
                return
            extension = os.path.splitext(target)[1].lower()
            with open(target, "rb") as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                _CONTENT_TYPES.get(extension, "application/octet-stream"),
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return FeatureFlagsHandler


def serve(
    host: str = "127.0.0.1",
    port: int = 8010,
    db_path: str = ":memory:",
) -> None:
    """Start the server and block until interrupted."""
    store = FlagStore(db_path)
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Feature Flags serving on http://{host}:{port}  (Ctrl+C to stop)")
    print(f"  api:     http://{host}:{port}/api/flags")
    print(f"  browse:  http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
        store.close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Feature Flags server.")
    parser.add_argument(
        "--host", default=os.environ.get("FEATURE_FLAGS_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("FEATURE_FLAGS_PORT", "8010"))
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("FEATURE_FLAGS_DB", ":memory:"),
        help="SQLite file path; defaults to an in-memory database",
    )
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, db_path=args.db)


if __name__ == "__main__":
    main()
