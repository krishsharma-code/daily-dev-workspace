"""Streak Board HTTP server.

A small, dependency-free service for tracking habits and their streaks: create a
habit with the days it is due, tick off the days you did it, and read back the
current streak, the best streak and a completion rate.

* ``route_request`` is a pure function (no sockets), so the API is cheap to test.
* ``serve`` wires it into :mod:`http.server` and also serves ``client/``, so the
  whole app runs from a single origin.

Run it with::

    python server/app.py                    # http://127.0.0.1:8012
    python server/app.py --db habits.db     # keep habits across restarts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

# Allow running this file directly (``python server/app.py``).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import HabitStore, StoreError  # noqa: E402
from streak import StreakError, format_day, parse_day  # noqa: E402

__all__ = ["CLIENT_DIR", "make_handler", "route_request", "serve"]

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


def _today(query: dict, body: Optional[dict] = None) -> date:
    """The reference day for streaks.

    Defaults to the server's local date. Tests (and anyone debugging around
    midnight) can pin it with ``?today=YYYY-MM-DD`` or a ``today`` body field,
    so a response is reproducible without touching the clock.
    """
    for source in (query.get("today", [None])[0], (body or {}).get("today")):
        if source:
            return parse_day(source)
    return date.today()


def _segments(path: str) -> list[str]:
    return [unquote(part) for part in path.strip("/").split("/") if part]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_request(
    store: HabitStore,
    method: str,
    path: str,
    query: Optional[dict] = None,
    body: bytes = b"",
) -> tuple[int, dict, Any]:
    """Handle one API call. Returns ``(status, headers, payload)``."""
    query = query or {}
    parts = _segments(path)
    if not parts or parts[0] != "api":
        return 404, {}, {"error": "not found"}

    resource = parts[1] if len(parts) > 1 else ""

    try:
        if resource == "health":
            if method != "GET":
                return _method_not_allowed("GET")
            return 200, {}, {
                "status": "ok",
                "habits": len(store.list_habits()),
                "today": format_day(date.today()),
            }

        if resource == "board":
            if method != "GET":
                return _method_not_allowed("GET")
            return 200, {}, store.board(_today(query))

        if resource != "habits":
            return 404, {}, {"error": "not found"}

        # /api/habits
        if len(parts) == 2:
            if method == "GET":
                return 200, {}, store.board(_today(query))
            if method == "POST":
                data, error = _json_object(body)
                if error:
                    return 400, {}, error
                try:
                    habit = store.create_habit(
                        data.get("name"), data.get("weekdays")
                    )
                except StoreError as exc:
                    return 422, {}, {"error": str(exc)}
                view = store.habit_view(habit, _today(query, data))
                return 201, {"Location": f"/api/habits/{habit['id']}"}, view
            return _method_not_allowed("GET, POST")

        habit_id = parts[2]

        # /api/habits/{id}
        if len(parts) == 3:
            if method == "GET":
                habit = store.get_habit(habit_id)
                if habit is None:
                    return 404, {}, {"error": f"unknown habit {habit_id!r}"}
                return 200, {}, store.habit_view(habit, _today(query))
            if method == "DELETE":
                if not store.delete_habit(habit_id):
                    return 404, {}, {"error": f"unknown habit {habit_id!r}"}
                return 200, {}, {"deleted": habit_id}
            return _method_not_allowed("GET, DELETE")

        if len(parts) < 4 or parts[3] != "checkins":
            return 404, {}, {"error": "not found"}

        # /api/habits/{id}/checkins
        if len(parts) == 4:
            if method == "GET":
                try:
                    entries = store.checkins(habit_id)
                except StoreError as exc:
                    return 404, {}, {"error": str(exc)}
                return 200, {}, {"habit_id": habit_id, "checkins": entries}
            if method == "POST":
                data, error = _json_object(body)
                if error:
                    return 400, {}, error
                try:
                    result = store.check_in(
                        habit_id,
                        data.get("day"),
                        data.get("note", ""),
                        today=_today(query, data),
                    )
                except StoreError as exc:
                    status = 404 if "unknown habit" in str(exc) else 422
                    return status, {}, {"error": str(exc)}
                habit = store.get_habit(habit_id)
                return (
                    (201 if result["created"] else 200),
                    {},
                    {
                        **result,
                        "habit": store.habit_view(habit, _today(query, data)),
                    },
                )
            return _method_not_allowed("GET, POST")

        # /api/habits/{id}/checkins/{day}
        if len(parts) != 5:
            return 404, {}, {"error": "not found"}
        day = parts[4]
        if method == "DELETE":
            try:
                removed = store.undo_check_in(habit_id, day)
            except (StoreError, StreakError) as exc:
                status = 404 if "unknown habit" in str(exc) else 422
                return status, {}, {"error": str(exc)}
            if not removed:
                return 404, {}, {"error": f"no check-in on {day}"}
            return 200, {}, {"removed": day, "habit_id": habit_id}
        return _method_not_allowed("DELETE")

    except StreakError as exc:
        return 422, {}, {"error": str(exc)}


def _method_not_allowed(allowed: str) -> tuple[int, dict, Any]:
    return 405, {"Allow": allowed}, {"error": "method not allowed"}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def make_handler(store: HabitStore):
    """Build a :class:`BaseHTTPRequestHandler` bound to ``store``."""

    class StreakBoardHandler(BaseHTTPRequestHandler):
        server_version = "StreakBoard/1.0"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            if os.environ.get("STREAK_BOARD_LOG"):
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

        def do_DELETE(self):  # noqa: N802
            self._dispatch("DELETE")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"
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
            if not (target == CLIENT_DIR or target.startswith(CLIENT_DIR + os.sep)):
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

    return StreakBoardHandler


def serve(
    host: str = "127.0.0.1",
    port: int = 8012,
    db_path: str = ":memory:",
) -> None:
    """Start the server and block until interrupted."""
    store = HabitStore(db_path)
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Streak Board serving on http://{host}:{port}  (Ctrl+C to stop)")
    print(f"  api:     http://{host}:{port}/api/habits")
    print(f"  browse:  http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
        store.close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Streak Board server.")
    parser.add_argument(
        "--host", default=os.environ.get("STREAK_BOARD_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("STREAK_BOARD_PORT", "8012")),
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("STREAK_BOARD_DB", ":memory:"),
        help="SQLite file path; defaults to an in-memory database",
    )
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, db_path=args.db)


if __name__ == "__main__":
    main()
