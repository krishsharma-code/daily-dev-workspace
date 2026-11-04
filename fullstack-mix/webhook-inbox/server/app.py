"""Webhook Inbox HTTP server.

A dependency-free inbox for webhooks you are developing against: point any
sender at ``http://127.0.0.1:8000/in/<bin>`` and the request is captured, then
browse, inspect and replay it from the bundled client.

* ``route_request`` is a pure function (no sockets), so the API is cheap to
  test and easy to reason about.
* ``serve`` wires it into :mod:`http.server` and also serves ``client/``, so
  the whole app runs from a single origin.

Run it with::

    python server/app.py                 # http://127.0.0.1:8000
    python server/app.py --port 9000     # or set WEBHOOK_INBOX_PORT

Replay sends the stored request to a URL you supply. That is intentionally a
local development tool: it binds to 127.0.0.1 by default and should not be
exposed to an untrusted network.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

# Allow running this file directly (``python server/app.py``).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import (  # noqa: E402
    DEFAULT_LIMIT,
    MAX_BODY_BYTES,
    Event,
    InboxStore,
    ValidationError,
)

__all__ = [
    "CLIENT_DIR",
    "make_handler",
    "route_request",
    "send_upstream",
    "serve",
]

CLIENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "client")
)

REPLAY_TIMEOUT = 5.0

# How many bytes we are willing to read off the socket for one request. The
# stored prefix is capped at MAX_BODY_BYTES; the rest is drained (and counted)
# up to this limit so a huge upload cannot pin a worker thread.
MAX_DRAIN_BYTES = 4 * MAX_BODY_BYTES

# Headers that describe the connection to *us*, not the payload; forwarding
# them would corrupt the replayed request.
_HOP_BY_HOP = {
    "accept-encoding",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# Every API and capture response carries the same permissive CORS header: the
# bundled client is served from the same origin, and a local tool has no
# credential a foreign origin could abuse.
_CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects while replaying.

    :mod:`urllib.request` follows 3xx responses by default, which would let an
    allowed target bounce the replayed request on to a host the caller never
    named - including a link-local metadata address. Returning ``None`` makes
    urllib raise :class:`urllib.error.HTTPError`, which
    :func:`send_upstream` already reports as the upstream status, so the
    redirect shows up in the UI instead of being silently chased.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_REPLAY_OPENER = urllib.request.build_opener(_NoRedirect)


def send_upstream(
    event: Event, target: str, timeout: float = REPLAY_TIMEOUT
) -> tuple[int, str]:
    """Forward a stored event to ``target``.

    Returns ``(status_code, short_body_text)``. HTTP error responses are
    returned rather than raised, because a 4xx from the receiver is useful
    information, not a failure of the inbox.

    Only the first 2 KB of the receiver's answer is read back, and redirects
    are not followed (see :class:`_NoRedirect`).
    """
    headers = {
        key: value
        for key, value in event.headers.items()
        if key not in _HOP_BY_HOP
    }
    request = urllib.request.Request(
        target,
        data=event.body if event.body else None,
        headers=headers,
        method=event.method,
    )
    try:
        with _REPLAY_OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # a real answer, just not a 2xx
        return exc.code, exc.read(2048).decode("utf-8", "replace")


# Hosts and networks a replay must never reach, even when the operator typed
# them: cloud metadata endpoints hand credentials to anything that asks, and
# link-local addresses are not the developer's endpoint either.
_BLOCKED_HOSTS = frozenset({"metadata", "metadata.google.internal", "metadata.goog"})
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in ("0.0.0.0/8", "169.254.0.0/16", "fe80::/10", "::/128")
)


def _host_is_blocked(host: str) -> bool:
    """True for empty hosts, metadata aliases and link-local/unspecified IPs."""
    candidate = host.strip().strip("[]").lower().rstrip(".")
    if not candidate:
        return True
    if candidate in _BLOCKED_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:  # a name, not an IP literal - nothing static to reject
        return False
    return any(address in network for network in _BLOCKED_NETWORKS)


def _valid_target(target: object) -> Optional[str]:
    """Return the target URL if it is a usable, safe http(s) URL, else ``None``.

    Replay is a loopback development tool, so ``127.0.0.1`` and ``localhost``
    stay allowed - that is the whole point. Everything else is narrowed to what
    the feature actually needs:

    * ``http``/``https`` only, with a parseable host and port;
    * no embedded ``user:password@`` credentials, which would otherwise be
      forwarded upstream and captured in the stored headers;
    * no link-local, unspecified or cloud-metadata addresses.

    Hostnames are checked as written, so a name that *resolves* to a link-local
    address (DNS rebinding) is out of scope for a tool that is documented as
    loopback-only.
    """
    if not isinstance(target, str) or not target.strip():
        return None
    candidate = target.strip()
    try:
        parsed = urlparse(candidate)
        port = parsed.port  # raises ValueError on a malformed port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    if port is not None and not 0 < port < 65536:
        return None
    if _host_is_blocked(parsed.hostname):
        return None
    return candidate


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _first(query: dict, key: str, default=None):
    values = query.get(key)
    if not values:
        return default
    if isinstance(values, str):
        return values
    return values[0]


def _header_value(headers: dict, name: str) -> Optional[str]:
    """Case-insensitive header lookup.

    ``http.server`` hands us the header names exactly as the client sent them,
    so a sender using a lowercase ``content-length`` used to look like a
    zero-length body.
    """
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return value
    return None


def _parse_content_length(headers: dict) -> tuple[int, Optional[tuple]]:
    """Parse ``Content-Length`` leniently. Returns ``(length, error_response)``.

    A malformed value used to raise straight out of the request handler, which
    dropped the connection with no response at all and left a traceback in the
    log. Callers now get a 400 they can act on.
    """
    raw = _header_value(headers, "Content-Length")
    if raw is None:
        return 0, None
    text = str(raw).strip()
    if not text:
        return 0, None
    try:
        length = int(text)
    except (TypeError, ValueError):
        return 0, (400, {"error": "Content-Length must be an integer"})
    if length < 0:
        return 0, (400, {"error": "Content-Length must not be negative"})
    return length, None


def _read_body(rfile, length: int) -> tuple[bytes, int]:
    """Read one request body, bounded in both memory and time.

    Returns ``(stored, total)``: the prefix that will be persisted and the
    number of bytes that actually arrived. Only ``MAX_BODY_BYTES`` are kept, so
    a 5 GB ``Content-Length`` cannot exhaust memory; the remainder is drained
    (and counted) up to :data:`MAX_DRAIN_BYTES` so the socket is not left full
    of unread data.
    """
    if length <= 0:
        return b"", 0
    stored = rfile.read(min(length, MAX_BODY_BYTES)) or b""
    total = len(stored)
    remaining = min(length - total, MAX_DRAIN_BYTES)
    while remaining > 0:
        chunk = rfile.read(min(65536, remaining))
        if not chunk:
            break
        total += len(chunk)
        remaining -= len(chunk)
    return stored, total


def _json_body(body: bytes) -> tuple[Optional[dict], Optional[tuple]]:
    """Parse a JSON object body. Returns ``(data, error_response)``."""
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, (400, {"error": "body must be valid JSON"})
    if not isinstance(data, dict):
        return None, (400, {"error": "body must be a JSON object"})
    return data, None


def _capture(
    store: InboxStore,
    bin_name: str,
    method: str,
    path: str,
    headers: dict,
    body: bytes,
    base_headers: dict,
    body_size: Optional[int] = None,
) -> tuple[int, dict, object]:
    """Persist one delivery and answer 202.

    ``body_size`` lets the HTTP layer report the *true* payload size when it
    only read (and stored) the first ``MAX_BODY_BYTES`` of an oversized body.
    """
    try:
        event = store.add_event(
            bin=bin_name,
            method=method,
            path=path,
            headers=headers,
            body=body,
            body_size=body_size,
        )
    except ValidationError as exc:
        return 400, base_headers, {"error": str(exc)}
    return 202, base_headers, {"event_id": event.id, "bin": event.bin}


def route_request(
    store: InboxStore,
    method: str,
    path: str,
    query: Optional[dict] = None,
    body: bytes = b"",
    headers: Optional[dict] = None,
    body_size: Optional[int] = None,
) -> tuple[int, dict, object]:
    """Handle one request and return ``(status, headers, payload)``.

    ``payload`` is JSON-serialisable for every response except ``204 No
    Content``, where it is ``None``. ``body_size`` is the byte count the
    caller actually received, for bodies it had to truncate before calling.
    """
    query = query or {}
    request_headers = headers or {}
    method = (method or "GET").upper()
    segments = [unquote(part) for part in path.strip("/").split("/") if part]
    extra_headers = dict(_CORS_HEADERS)

    # -- capture endpoint: /in/<bin>[/anything] ---------------------------
    if segments[:1] == ["in"] and len(segments) >= 2:
        return _capture(
            store,
            segments[1],
            method,
            path,
            request_headers,
            body,
            extra_headers,
            body_size,
        )

    if segments[:1] != ["api"]:
        return 404, extra_headers, {"error": "not found"}

    # -- /api/health -------------------------------------------------------
    if segments == ["api", "health"] and method == "GET":
        return (
            200,
            extra_headers,
            {
                "status": "ok",
                "bins": store.count_bins(),
                "events": store.count_events(),
            },
        )

    # -- /api/bins ---------------------------------------------------------
    if segments == ["api", "bins"]:
        if method == "GET":
            return 200, extra_headers, {"bins": store.list_bins()}
        if method == "POST":
            data, error = _json_body(body)
            if error is not None:
                status, payload = error
                return status, extra_headers, payload
            try:
                info, created = store.ensure_bin(data.get("name", ""))
            except ValidationError as exc:
                return 422, extra_headers, {"error": str(exc)}
            info = store.get_bin(info["name"]) or info
            return (201 if created else 200), extra_headers, {"bin": info}
        return 405, extra_headers, {"error": "method not allowed"}

    # -- /api/bins/<name> --------------------------------------------------
    if len(segments) == 3 and segments[:2] == ["api", "bins"]:
        if method == "DELETE":
            try:
                deleted = store.delete_bin(segments[2])
            except ValidationError as exc:
                return 400, extra_headers, {"error": str(exc)}
            if deleted:
                return 204, extra_headers, None
            return 404, extra_headers, {"error": "bin not found"}
        return 405, extra_headers, {"error": "method not allowed"}

    # -- /api/events -------------------------------------------------------
    if segments == ["api", "events"]:
        if method == "GET":
            bin_name = _first(query, "bin")
            limit_raw = _first(query, "limit", DEFAULT_LIMIT)
            before_raw = _first(query, "before_id")
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError):
                return 400, extra_headers, {"error": "limit must be an integer"}
            before_id = None
            if before_raw not in (None, ""):
                try:
                    before_id = int(before_raw)
                except (TypeError, ValueError):
                    return 400, extra_headers, {"error": "before_id must be an integer"}
            try:
                events = store.list_events(
                    bin=bin_name, limit=limit, before_id=before_id
                )
            except ValidationError as exc:
                return 400, extra_headers, {"error": str(exc)}
            return (
                200,
                extra_headers,
                {
                    "events": [event.to_dict(include_body=False) for event in events],
                    "count": len(events),
                },
            )
        return 405, extra_headers, {"error": "method not allowed"}

    # -- /api/events/<id> --------------------------------------------------
    if len(segments) == 3 and segments[:2] == ["api", "events"]:
        raw_id = segments[2]
        if not raw_id.isdigit():
            return 400, extra_headers, {"error": "id must be an integer"}
        event_id = int(raw_id)
        if method == "GET":
            event = store.get_event(event_id)
            if event is None:
                return 404, extra_headers, {"error": "event not found"}
            return 200, extra_headers, {"event": event.to_dict()}
        if method == "DELETE":
            if store.delete_event(event_id):
                return 204, extra_headers, None
            return 404, extra_headers, {"error": "event not found"}
        return 405, extra_headers, {"error": "method not allowed"}

    # -- /api/events/<id>/replay -------------------------------------------
    if (
        len(segments) == 4
        and segments[:2] == ["api", "events"]
        and segments[3] == "replay"
    ):
        if method != "POST":
            return 405, extra_headers, {"error": "method not allowed"}
        if not segments[2].isdigit():
            return 400, extra_headers, {"error": "id must be an integer"}
        event = store.get_event(int(segments[2]))
        if event is None:
            return 404, extra_headers, {"error": "event not found"}
        data, error = _json_body(body)
        if error is not None:
            status, payload = error
            return status, extra_headers, payload
        target = _valid_target(data.get("target"))
        if target is None:
            return 422, extra_headers, {
                "error": "target must be an absolute http(s) URL"
            }
        try:
            upstream_status, upstream_body = send_upstream(event, target)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return 502, extra_headers, {"error": f"replay failed: {reason}"}
        return (
            200,
            extra_headers,
            {
                "event_id": event.id,
                "target": target,
                "upstream_status": upstream_status,
                "upstream_body": upstream_body,
            },
        )

    # -- /api/bins/<name>/clear --------------------------------------------
    if (
        len(segments) == 4
        and segments[:2] == ["api", "bins"]
        and segments[3] == "clear"
    ):
        if method != "POST":
            return 405, extra_headers, {"error": "method not allowed"}
        try:
            removed = store.clear_bin(segments[2])
        except ValidationError as exc:
            return 400, extra_headers, {"error": str(exc)}
        return 200, extra_headers, {"cleared": removed, "bin": segments[2].lower()}

    return 404, extra_headers, {"error": "not found"}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def make_handler(store: InboxStore):
    """Build a :class:`BaseHTTPRequestHandler` bound to ``store``."""

    class WebhookInboxHandler(BaseHTTPRequestHandler):
        server_version = "WebhookInbox/1.0"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            if os.environ.get("WEBHOOK_INBOX_LOG"):
                super().log_message(fmt, *args)

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith(("/api/", "/in/")):
                headers = dict(self.headers.items())
                length, error = _parse_content_length(headers)
                if error is not None:
                    status, payload = error
                    # The body was never consumed, so this connection cannot be
                    # reused for another request.
                    self.close_connection = True
                    self._send_json(status, payload, _CORS_HEADERS)
                    return
                raw, received = _read_body(self.rfile, length)
                status, response_headers, payload = route_request(
                    store,
                    method,
                    parsed.path,
                    parse_qs(parsed.query),
                    raw,
                    headers,
                    received,
                )
                self._send_json(status, payload, response_headers)
            elif method == "GET":
                self._serve_static(parsed.path)
            else:
                self._send_json(405, {"error": "method not allowed"}, {})

        # Webhook senders use whatever verb their service picked, so capture
        # every common one rather than only GET/POST.
        def do_GET(self):  # noqa: N802 - stdlib naming
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self):  # noqa: N802
            self._dispatch("PUT")

        def do_PATCH(self):  # noqa: N802
            self._dispatch("PATCH")

        def do_DELETE(self):  # noqa: N802
            self._dispatch("DELETE")

        def do_HEAD(self):  # noqa: N802
            self._dispatch("HEAD")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, PATCH, DELETE, OPTIONS",
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
            if "\x00" in relative:
                self._send_json(400, {"error": "bad path"}, {})
                return
            # realpath, not abspath: a symlink dropped into client/ must not be
            # able to point outside it, and the containment check has to compare
            # resolved paths on both sides.
            root = os.path.realpath(CLIENT_DIR)
            target = os.path.realpath(os.path.join(root, relative))
            if target != root and not target.startswith(root + os.sep):
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

    return WebhookInboxHandler


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    db_path: str = ":memory:",
) -> None:
    """Start the server and block until interrupted."""
    store = InboxStore(db_path)
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Webhook Inbox serving on http://{host}:{port}  (Ctrl+C to stop)")
    print(f"  capture: POST http://{host}:{port}/in/<bin>")
    print(f"  browse:  http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
        store.close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Webhook Inbox server.")
    parser.add_argument(
        "--host", default=os.environ.get("WEBHOOK_INBOX_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("WEBHOOK_INBOX_PORT", "8000"))
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("WEBHOOK_INBOX_DB", ":memory:"),
        help="SQLite file path; defaults to an in-memory database",
    )
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, db_path=args.db)


if __name__ == "__main__":
    main()
