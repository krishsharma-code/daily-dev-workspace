"""Expense Split HTTP server.

A small, dependency-free service for splitting shared bills: create a group,
log who paid what, and get back net balances plus the shortest list of payments
that settles everyone up.

* ``route_request`` is a pure function (no sockets), so the API is cheap to test.
* ``serve`` wires it into :mod:`http.server` and also serves ``client/``, so the
  whole app runs from a single origin.

Run it with::

    python server/app.py                    # http://127.0.0.1:8011
    python server/app.py --db trips.db      # keep groups across restarts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

# Allow running this file directly (``python server/app.py``).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from splitter import (  # noqa: E402
    ValidationError,
    format_amount,
    parse_amount,
    split as make_split,
)
from store import ExpenseStore  # noqa: E402

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


def _split_spec(raw: Any) -> dict:
    """Normalise the ``split`` field of an expense into keyword arguments.

    ``"equal"`` and ``{"type": "equal"}`` mean the same thing; the map forms add
    the participants or the shares/weights.
    """
    if raw is None:
        raw = "equal"
    if isinstance(raw, str):
        return {"split_type": raw}
    if not isinstance(raw, dict):
        return {"error": "split must be a string or an object"}
    return {
        "split_type": raw.get("type", "equal"),
        "participants": raw.get("participants"),
        "shares": raw.get("shares"),
        "weights": raw.get("weights"),
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_request(
    store: ExpenseStore,
    method: str,
    path: str,
    query: Optional[dict] = None,
    body: bytes = b"",
) -> tuple[int, dict, object]:
    """Handle one request and return ``(status, headers, payload)``."""
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
        return 200, headers, {"status": "ok", "groups": len(store.list_groups())}

    # -- /api/groups -------------------------------------------------------
    if segments == ["api", "groups"]:
        if method == "GET":
            groups = store.list_groups()
            return 200, headers, {"groups": groups, "count": len(groups)}
        if method == "POST":
            data, error = _json_object(body)
            if error is not None:
                return 400, headers, error
            try:
                group = store.create_group(data.get("name"), data.get("members"))
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            return 201, headers, {"group": group}
        return 405, headers, {"error": "method not allowed"}

    # -- /api/split --------------------------------------------------------
    if segments == ["api", "split"]:
        if method != "POST":
            return 405, headers, {"error": "method not allowed"}
        data, error = _json_object(body)
        if error is not None:
            return 400, headers, error
        spec = _split_spec(data.get("split"))
        if "error" in spec:
            return 422, headers, {"error": spec["error"]}
        if str(spec.get("split_type") or "equal").lower() == "equal" and not spec.get(
            "participants"
        ):
            return 422, headers, {"error": "an equal split needs 'participants'"}
        try:
            total = parse_amount(data.get("amount"))
            shares = make_split(total, **spec)
        except ValidationError as exc:
            return 422, headers, {"error": str(exc)}
        return 200, headers, {
            "amount_paise": total,
            "amount": format_amount(total),
            "shares": shares,
            "shares_display": {name: format_amount(v) for name, v in shares.items()},
        }

    # -- /api/groups/<id> --------------------------------------------------
    if len(segments) == 3 and segments[:2] == ["api", "groups"]:
        group_id = segments[2]
        if method == "GET":
            group = store.get_group(group_id)
            if group is None:
                return 404, headers, {"error": "group not found"}
            expenses = store.list_expenses(group_id)
            return 200, headers, {
                "group": group,
                "expense_count": len(expenses),
                "total": format_amount(sum(e["amount_paise"] for e in expenses)),
            }
        if method == "DELETE":
            if not store.delete_group(group_id):
                return 404, headers, {"error": "group not found"}
            return 204, headers, None
        return 405, headers, {"error": "method not allowed"}

    # -- /api/groups/<id>/expenses ----------------------------------------
    if len(segments) == 4 and segments[:2] == ["api", "groups"] and segments[3] == "expenses":
        group_id = segments[2]
        if store.get_group(group_id) is None:
            return 404, headers, {"error": "group not found"}
        if method == "GET":
            expenses = store.list_expenses(group_id)
            return 200, headers, {"expenses": expenses, "count": len(expenses)}
        if method == "POST":
            data, error = _json_object(body)
            if error is not None:
                return 400, headers, error
            spec = _split_spec(data.get("split"))
            if "error" in spec:
                return 422, headers, {"error": spec["error"]}
            try:
                expense = store.add_expense(
                    group_id,
                    description=data.get("description"),
                    payer=data.get("payer"),
                    amount=data.get("amount"),
                    **spec,
                )
            except KeyError:
                return 404, headers, {"error": "group not found"}
            except ValidationError as exc:
                return 422, headers, {"error": str(exc)}
            return 201, headers, {"expense": expense}
        return 405, headers, {"error": "method not allowed"}

    # -- /api/groups/<id>/expenses/<n> -------------------------------------
    if len(segments) == 5 and segments[:2] == ["api", "groups"] and segments[3] == "expenses":
        group_id = segments[2]
        if method != "DELETE":
            return 405, headers, {"error": "method not allowed"}
        try:
            expense_id = int(segments[4])
        except ValueError:
            return 404, headers, {"error": "expense not found"}
        if not store.delete_expense(group_id, expense_id):
            return 404, headers, {"error": "expense not found"}
        return 204, headers, None

    # -- /api/groups/<id>/settlement --------------------------------------
    if len(segments) == 4 and segments[:2] == ["api", "groups"] and segments[3] == "settlement":
        if method != "GET":
            return 405, headers, {"error": "method not allowed"}
        try:
            return 200, headers, store.settlement(segments[2])
        except KeyError:
            return 404, headers, {"error": "group not found"}

    return 404, headers, {"error": "not found"}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def make_handler(store: ExpenseStore):
    """Build a :class:`BaseHTTPRequestHandler` bound to ``store``."""

    class ExpenseSplitHandler(BaseHTTPRequestHandler):
        server_version = "ExpenseSplit/1.0"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            if os.environ.get("EXPENSE_SPLIT_LOG"):
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

    return ExpenseSplitHandler


def serve(
    host: str = "127.0.0.1",
    port: int = 8011,
    db_path: str = ":memory:",
) -> None:
    """Start the server and block until interrupted."""
    store = ExpenseStore(db_path)
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Expense Split serving on http://{host}:{port}  (Ctrl+C to stop)")
    print(f"  api:     http://{host}:{port}/api/groups")
    print(f"  browse:  http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
        store.close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Expense Split server.")
    parser.add_argument(
        "--host", default=os.environ.get("EXPENSE_SPLIT_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("EXPENSE_SPLIT_PORT", "8011"))
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("EXPENSE_SPLIT_DB", ":memory:"),
        help="SQLite file path; defaults to an in-memory database",
    )
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, db_path=args.db)


if __name__ == "__main__":
    main()
