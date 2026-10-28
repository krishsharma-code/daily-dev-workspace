"""Tests for the Webhook Inbox HTTP layer (``server/app.py``).

Most tests call :func:`route_request` directly - it is a pure function, so no
socket is involved. One end-to-end test boots a real server to prove the
wiring (capture endpoint, API and static file serving) works over HTTP.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import app as app_module
from app import make_handler, route_request
from store import InboxStore


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
