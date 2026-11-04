"""Tests for the Webhook Inbox HTTP layer (``server/app.py``).

Most tests call :func:`route_request` directly - it is a pure function, so no
socket is involved. One end-to-end test boots a real server to prove the
wiring (capture endpoint, API and static file serving) works over HTTP.
"""

import io
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import app as app_module
from app import make_handler, route_request
from store import MAX_BODY_BYTES, InboxStore


@pytest.fixture()
def store():
    s = InboxStore(":memory:")
    yield s
    s.close()


def post_json(store, path, payload):
    body = json.dumps(payload).encode("utf-8")
    return route_request(store, "POST", path, {}, body)


class TestCapture:
    def test_capture_returns_202_and_stores_event(self, store):
        status, headers, payload = route_request(
            store,
            "POST",
            "/in/hooks",
            {},
            b'{"hello": "world"}',
            {"Content-Type": "application/json", "X-Signature": "abc"},
        )
        assert status == 202
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert payload["bin"] == "hooks"

        event = store.get_event(payload["event_id"])
        assert event is not None
        assert event.method == "POST"
        assert event.body == b'{"hello": "world"}'
        assert event.headers["content-type"] == "application/json"
        assert event.headers["x-signature"] == "abc"

    def test_capture_accepts_any_method_and_keeps_subpath(self, store):
        status, _, payload = route_request(store, "patch", "/in/hooks/sub/path", {}, b"x")
        assert status == 202
        event = store.get_event(payload["event_id"])
        assert event.method == "PATCH"
        assert event.path == "/in/hooks/sub/path"

    def test_capture_creates_bin_on_first_hit(self, store):
        route_request(store, "POST", "/in/fresh", {}, b"")
        assert [b["name"] for b in store.list_bins()] == ["fresh"]

    def test_capture_rejects_bad_bin_name(self, store):
        status, _, payload = route_request(store, "POST", "/in/bad%20name", {}, b"")
        assert status == 400
        assert "bin name" in payload["error"]


class TestHealth:
    def test_health_reports_counts(self, store):
        route_request(store, "POST", "/in/alpha", {}, b"")
        status, _, payload = route_request(store, "GET", "/api/health")
        assert status == 200
        assert payload == {"status": "ok", "bins": 1, "events": 1}

    def test_unknown_api_route(self, store):
        status, _, payload = route_request(store, "GET", "/api/nope")
        assert status == 404
        assert payload["error"] == "not found"

    def test_non_api_path_is_not_found(self, store):
        status, _, _ = route_request(store, "GET", "/definitely-not-api")
        assert status == 404


class TestBinsApi:
    def test_create_then_duplicate(self, store):
        status, _, payload = post_json(store, "/api/bins", {"name": "Stripe"})
        assert status == 201
        assert payload["bin"]["name"] == "stripe"
        assert payload["bin"]["events"] == 0

        status, _, payload = post_json(store, "/api/bins", {"name": "stripe"})
        assert status == 200
        assert payload["bin"]["name"] == "stripe"

    def test_create_rejects_bad_name(self, store):
        status, _, payload = post_json(store, "/api/bins", {"name": "no spaces"})
        assert status == 422
        assert "bin name" in payload["error"]

    def test_create_rejects_bad_json(self, store):
        status, _, payload = route_request(store, "POST", "/api/bins", {}, b"{oops")
        assert status == 400
        assert "JSON" in payload["error"]

    def test_create_rejects_non_object(self, store):
        status, _, _ = route_request(store, "POST", "/api/bins", {}, b"[1]")
        assert status == 400

    def test_list_bins(self, store):
        store.add_event("alpha", "POST", "/in/alpha")
        status, _, payload = route_request(store, "GET", "/api/bins")
        assert status == 200
        assert [b["name"] for b in payload["bins"]] == ["alpha"]

    def test_delete_bin(self, store):
        store.add_event("alpha", "POST", "/in/alpha")
        status, _, payload = route_request(store, "DELETE", "/api/bins/alpha")
        assert status == 204
        assert payload is None
        assert store.count_events() == 0

    def test_delete_missing_bin(self, store):
        status, _, payload = route_request(store, "DELETE", "/api/bins/ghost")
        assert status == 404
        assert payload["error"] == "bin not found"

    def test_clear_bin(self, store):
        store.add_event("alpha", "POST", "/in/alpha")
        store.add_event("alpha", "POST", "/in/alpha")
        status, _, payload = route_request(store, "POST", "/api/bins/alpha/clear", {}, b"")
        assert status == 200
        assert payload == {"cleared": 2, "bin": "alpha"}
        assert store.count_bins() == 1

    def test_clear_requires_post(self, store):
        status, _, _ = route_request(store, "GET", "/api/bins/alpha/clear")
        assert status == 405


class TestEventsApi:
    def test_list_returns_summaries_without_bodies(self, store):
        store.add_event("alpha", "POST", "/in/alpha", {}, b'{"a": 1}')
        status, _, payload = route_request(store, "GET", "/api/events")
        assert status == 200
        assert payload["count"] == 1
        event = payload["events"][0]
        assert "body" not in event
        assert event["body_preview"] == '{"a": 1}'
        assert event["content_type"] == ""

    def test_list_filters_by_bin(self, store):
        store.add_event("alpha", "POST", "/in/alpha")
        store.add_event("beta", "POST", "/in/beta")
        status, _, payload = route_request(
            store, "GET", "/api/events", {"bin": ["beta"]}
        )
        assert status == 200
        assert [e["bin"] for e in payload["events"]] == ["beta"]

    def test_list_respects_limit_and_before_id(self, store):
        ids = [
            store.add_event("alpha", "POST", "/in/alpha", {}, str(i).encode()).id
            for i in range(3)
        ]
        status, _, payload = route_request(
            store, "GET", "/api/events", {"limit": ["1"]}
        )
        assert status == 200
        assert [e["id"] for e in payload["events"]] == [ids[2]]

        status, _, payload = route_request(
            store, "GET", "/api/events", {"limit": ["10"], "before_id": [str(ids[2])]}
        )
        assert [e["id"] for e in payload["events"]] == [ids[1], ids[0]]

    @pytest.mark.parametrize(
        "query", [{"limit": ["many"]}, {"before_id": ["nope"]}]
    )
    def test_list_rejects_non_integer_paging(self, store, query):
        status, _, payload = route_request(store, "GET", "/api/events", query)
        assert status == 400
        assert "must be an integer" in payload["error"]

    def test_list_rejects_bad_bin_filter(self, store):
        status, _, payload = route_request(
            store, "GET", "/api/events", {"bin": ["bad name"]}
        )
        assert status == 400

    def test_detail_includes_full_body(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha", {}, b"full body")
        status, _, payload = route_request(store, "GET", f"/api/events/{event.id}")
        assert status == 200
        assert payload["event"]["body"] == "full body"
        assert payload["event"]["body_encoding"] == "utf-8"

    def test_detail_missing_event(self, store):
        status, _, payload = route_request(store, "GET", "/api/events/999")
        assert status == 404
        assert payload["error"] == "event not found"

    def test_detail_rejects_non_integer_id(self, store):
        status, _, payload = route_request(store, "GET", "/api/events/abc")
        assert status == 400
        assert "id must be an integer" in payload["error"]

    def test_delete_event(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha")
        status, _, payload = route_request(store, "DELETE", f"/api/events/{event.id}")
        assert status == 204
        assert payload is None
        assert store.get_event(event.id) is None

    def test_delete_missing_event(self, store):
        status, _, _ = route_request(store, "DELETE", "/api/events/42")
        assert status == 404


class TestReplay:
    def test_replay_forwards_and_reports_upstream(self, store, monkeypatch):
        captured = {}

        def fake_send(event, target, timeout=5.0):
            captured["event"] = event
            captured["target"] = target
            return 204, ""

        monkeypatch.setattr(app_module, "send_upstream", fake_send)
        event = store.add_event("alpha", "POST", "/in/alpha", {}, b'{"x": 1}')

        status, _, payload = post_json(
            store, f"/api/events/{event.id}/replay", {"target": "http://localhost:9999/hook"}
        )
        assert status == 200
        assert payload["upstream_status"] == 204
        assert payload["target"] == "http://localhost:9999/hook"
        assert captured["event"].id == event.id

    @pytest.mark.parametrize(
        "target", ["", "ftp://nope", "not a url", "http://", 123, None]
    )
    def test_replay_rejects_bad_target(self, store, target):
        event = store.add_event("alpha", "POST", "/in/alpha")
        status, _, payload = post_json(
            store, f"/api/events/{event.id}/replay", {"target": target}
        )
        assert status == 422
        assert "http(s)" in payload["error"]

    def test_replay_missing_event(self, store):
        status, _, _ = post_json(store, "/api/events/7/replay", {"target": "http://x.test"})
        assert status == 404

    def test_replay_requires_post(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha")
        status, _, _ = route_request(store, "GET", f"/api/events/{event.id}/replay")
        assert status == 405

    def test_replay_network_failure_is_502(self, store, monkeypatch):
        import urllib.error

        def boom(event, target, timeout=5.0):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(app_module, "send_upstream", boom)
        event = store.add_event("alpha", "POST", "/in/alpha")
        status, _, payload = post_json(
            store, f"/api/events/{event.id}/replay", {"target": "http://x.test"}
        )
        assert status == 502
        assert "replay failed" in payload["error"]


class TestEndToEnd:
    def test_real_server_captures_and_serves_client(self, store):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            request = urllib.request.Request(
                f"{base}/in/e2e",
                data=b'{"ping": true}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 202
                assert json.loads(response.read())["bin"] == "e2e"

            with urllib.request.urlopen(f"{base}/api/events", timeout=5) as response:
                events = json.loads(response.read())["events"]
            assert len(events) == 1
            assert events[0]["bin"] == "e2e"

            with urllib.request.urlopen(f"{base}/", timeout=5) as response:
                assert response.status == 200
                assert b"Webhook Inbox" in response.read()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


def _http(store, path):
    """Start a throwaway server, issue one GET and return ``(status, body)``."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
    try:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class TestReplayTargetHardening:
    """Replay takes a URL from the caller, so the target is validated first."""

    @pytest.mark.parametrize(
        "target",
        [
            "http://169.254.169.254/latest/meta-data/",  # link-local metadata
            "http://[fe80::1]/hook",  # IPv6 link-local
            "http://0.0.0.0:8000/hook",  # unspecified
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://user:secret@localhost:9000/hook",  # embedded credentials
            "http://localhost:notaport/hook",  # unparseable port
        ],
    )
    def test_replay_rejects_unsafe_targets(self, store, target):
        event = store.add_event("alpha", "POST", "/in/alpha")
        status, _, payload = post_json(
            store, f"/api/events/{event.id}/replay", {"target": target}
        )
        assert status == 422
        assert "http(s)" in payload["error"]

    @pytest.mark.parametrize(
        "target",
        [
            "http://127.0.0.1:3000/hook",
            "http://localhost:3000/hook",
            "https://dev.example.test/hook",
        ],
    )
    def test_loopback_targets_still_allowed(self, target):
        assert app_module._valid_target(target) == target

    def test_hosts_are_matched_case_insensitively(self):
        assert app_module._valid_target("http://METADATA.GOOGLE.INTERNAL/") is None


class TestReplayRedirects:
    def test_redirect_is_reported_not_followed(self, store):
        followed = []

        class Target(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib naming
                followed.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

        class Redirector(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{target.server_address[1]}/followed",
                )
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        redirector = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        for server in (target, redirector):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            event = store.add_event("alpha", "POST", "/in/alpha", {}, b"{}")
            url = f"http://127.0.0.1:{redirector.server_address[1]}/hook"
            status, _ = app_module.send_upstream(event, url)
            assert status == 302
            assert followed == []
        finally:
            for server in (target, redirector):
                server.shutdown()
                server.server_close()


class TestRequestFraming:
    """Content-Length comes from the sender and must never be trusted blindly."""

    @pytest.mark.parametrize("value", ["abc", "-5", "1.5", "0x10", ""])
    def test_bad_content_length_is_a_400(self, value):
        length, error = app_module._parse_content_length({"Content-Length": value})
        assert length == 0
        if value == "":
            assert error is None
        else:
            assert error[0] == 400

    def test_header_lookup_is_case_insensitive(self):
        assert app_module._parse_content_length({"content-length": "12"}) == (12, None)

    def test_missing_content_length_is_zero(self):
        assert app_module._parse_content_length({}) == (0, None)

    def test_read_body_keeps_only_the_cap(self):
        declared = MAX_BODY_BYTES * 3
        stored, total = app_module._read_body(io.BytesIO(b"a" * declared), declared)
        assert len(stored) == MAX_BODY_BYTES
        assert total == declared

    def test_read_body_stops_draining_at_the_cap(self):
        declared = app_module.MAX_DRAIN_BYTES * 5
        payload = b"a" * (app_module.MAX_DRAIN_BYTES * 2)
        stored, total = app_module._read_body(io.BytesIO(payload), declared)
        assert len(stored) == MAX_BODY_BYTES
        assert total == MAX_BODY_BYTES + app_module.MAX_DRAIN_BYTES

    def test_malformed_content_length_gets_a_response(self, store):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(
                ("127.0.0.1", httpd.server_address[1]), timeout=5
            ) as conn:
                conn.sendall(
                    b"POST /in/hooks HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n"
                )
                reply = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    reply += chunk
            assert reply.startswith(b"HTTP/1.0 400"), reply[:80]
            assert b"Content-Length must be an integer" in reply
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


class TestCaptureBodyLimits:
    def test_oversized_capture_reports_true_size(self, store):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        payload = b"z" * (MAX_BODY_BYTES + 1024)
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_address[1]}/in/big",
                data=payload,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 202
                event_id = json.loads(response.read())["event_id"]
            event = store.get_event(event_id)
            assert event.body_size == len(payload)
            assert event.truncated is True
            assert len(event.body) == MAX_BODY_BYTES
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


class TestStaticContainment:
    def test_symlink_out_of_client_dir_is_forbidden(self, store, tmp_path, monkeypatch):
        client = tmp_path / "client"
        client.mkdir()
        (client / "index.html").write_text("ok")
        secret = tmp_path / "secret.txt"
        secret.write_text("do not serve me")
        (client / "escape.txt").symlink_to(secret)
        monkeypatch.setattr(app_module, "CLIENT_DIR", str(client))

        status, _ = _http(store, "/escape.txt")
        assert status == 403

    def test_normal_file_still_served(self, store, tmp_path, monkeypatch):
        client = tmp_path / "client"
        client.mkdir()
        (client / "index.html").write_text("Webhook Inbox")
        monkeypatch.setattr(app_module, "CLIENT_DIR", str(client))

        status, body = _http(store, "/")
        assert status == 200
        assert body == b"Webhook Inbox"

    def test_traversal_is_forbidden(self, store, tmp_path, monkeypatch):
        client = tmp_path / "client"
        client.mkdir()
        (client / "index.html").write_text("ok")
        (tmp_path / "secret.txt").write_text("nope")
        monkeypatch.setattr(app_module, "CLIENT_DIR", str(client))

        status, _ = _http(store, "/../secret.txt")
        assert status in (403, 404)

    def test_null_byte_in_path_is_rejected(self, store, tmp_path, monkeypatch):
        client = tmp_path / "client"
        client.mkdir()
        (client / "index.html").write_text("ok")
        monkeypatch.setattr(app_module, "CLIENT_DIR", str(client))

        status, _ = _http(store, "/%00index.html")
        assert status == 400
