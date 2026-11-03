"""Tests for the Feature Flags HTTP layer (``server/app.py``).

Most tests call :func:`route_request` directly - it is a pure function, so no
socket is involved. One end-to-end test boots a real server to prove the
wiring (API, CORS headers and static file serving) works over HTTP.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app import CLIENT_DIR, make_handler, route_request
from store import FlagStore


@pytest.fixture()
def store():
    s = FlagStore(":memory:")
    yield s
    s.close()


def post_json(store, path, payload):
    body = json.dumps(payload).encode("utf-8")
    return route_request(store, "POST", path, {}, body)


class TestHealth:
    def test_health_counts_flags(self, store):
        store.put_flag({"key": "one"})
        status, headers, payload = route_request(store, "GET", "/api/health")
        assert status == 200
        assert payload == {"status": "ok", "flags": 1}
        assert headers["Access-Control-Allow-Origin"] == "*"


class TestCreateFlag:
    def test_create_returns_201(self, store):
        status, _, payload = post_json(
            store,
            "/api/flags",
            {"key": "new-checkout", "description": "v2 checkout", "rollout": 25},
        )
        assert status == 201
        assert payload["flag"]["key"] == "new-checkout"
        assert payload["flag"]["rollout"] == 25
        assert payload["flag"]["enabled"] is False

    def test_second_create_returns_200(self, store):
        post_json(store, "/api/flags", {"key": "new-checkout"})
        status, _, payload = post_json(
            store, "/api/flags", {"key": "new-checkout", "enabled": True}
        )
        assert status == 200
        assert payload["flag"]["enabled"] is True

    def test_key_is_normalised(self, store):
        status, _, payload = post_json(store, "/api/flags", {"key": " New-Checkout "})
        assert status == 201
        assert payload["flag"]["key"] == "new-checkout"

    def test_invalid_key_is_422(self, store):
        status, _, payload = post_json(store, "/api/flags", {"key": "bad key"})
        assert status == 422
        assert "error" in payload

    def test_missing_key_is_422(self, store):
        status, _, _ = post_json(store, "/api/flags", {"description": "no key"})
        assert status == 422

    def test_bad_rollout_is_422(self, store):
        status, _, _ = post_json(
            store, "/api/flags", {"key": "ok-key", "rollout": 150}
        )
        assert status == 422

    def test_malformed_json_is_400(self, store):
        status, _, payload = route_request(
            store, "POST", "/api/flags", {}, b"{not json"
        )
        assert status == 400
        assert payload["error"] == "body must be valid JSON"

    def test_non_object_json_is_400(self, store):
        status, _, _ = route_request(store, "POST", "/api/flags", {}, b"[1, 2]")
        assert status == 400


class TestReadAndList:
    def test_list_is_empty_at_first(self, store):
        status, _, payload = route_request(store, "GET", "/api/flags")
        assert status == 200
        assert payload == {"flags": [], "count": 0}

    def test_list_returns_flags(self, store):
        store.put_flag({"key": "b"})
        store.put_flag({"key": "a"})
        status, _, payload = route_request(store, "GET", "/api/flags")
        assert status == 200
        assert payload["count"] == 2
        assert [f["key"] for f in payload["flags"]] == ["a", "b"]

    def test_get_one(self, store):
        store.put_flag({"key": "checkout", "allowlist": ["bob"]})
        status, _, payload = route_request(store, "GET", "/api/flags/checkout")
        assert status == 200
        assert payload["flag"]["allowlist"] == ["bob"]

    def test_get_missing_is_404(self, store):
        status, _, _ = route_request(store, "GET", "/api/flags/nope")
        assert status == 404

    def test_get_invalid_key_is_400(self, store):
        status, _, _ = route_request(store, "GET", "/api/flags/bad%20key")
        assert status == 400


class TestUpdateAndDelete:
    def test_patch_updates_fields(self, store):
        store.put_flag({"key": "checkout", "rollout": 10})
        status, _, payload = route_request(
            store, "PATCH", "/api/flags/checkout", {}, json.dumps({"rollout": 60}).encode()
        )
        assert status == 200
        assert payload["flag"]["rollout"] == 60

    def test_patch_missing_is_404(self, store):
        status, _, _ = route_request(
            store, "PATCH", "/api/flags/nope", {}, b'{"rollout": 5}'
        )
        assert status == 404

    def test_patch_with_nothing_to_change_is_422(self, store):
        store.put_flag({"key": "checkout"})
        status, _, _ = route_request(
            store, "PATCH", "/api/flags/checkout", {}, b'{"colour": "red"}'
        )
        assert status == 422

    def test_delete_returns_204_with_no_body(self, store):
        store.put_flag({"key": "checkout"})
        status, _, payload = route_request(store, "DELETE", "/api/flags/checkout")
        assert status == 204
        assert payload is None

    def test_delete_missing_is_404(self, store):
        status, _, _ = route_request(store, "DELETE", "/api/flags/nope")
        assert status == 404


class TestEvaluate:
    def test_evaluate_post(self, store):
        store.put_flag({"key": "checkout", "enabled": True, "rollout": 100})
        status, _, payload = post_json(
            store, "/api/evaluate", {"key": "checkout", "user_id": "bob"}
        )
        assert status == 200
        assert payload["enabled"] is True
        assert payload["reason"] == "rollout"
        assert payload["user_id"] == "bob"

    def test_evaluate_get_matches_post(self, store):
        store.put_flag({"key": "checkout", "enabled": True, "rollout": 50})
        _, _, posted = post_json(
            store, "/api/evaluate", {"key": "checkout", "user_id": "bob"}
        )
        status, _, fetched = route_request(
            store, "GET", "/api/evaluate", {"key": ["checkout"], "user_id": ["bob"]}
        )
        assert status == 200
        assert fetched == posted

    def test_evaluate_allowlisted_user_on_disabled_flag(self, store):
        store.put_flag({"key": "checkout", "enabled": False, "allowlist": ["bob"]})
        status, _, payload = post_json(
            store, "/api/evaluate", {"key": "checkout", "user_id": "bob"}
        )
        assert status == 200
        assert payload["enabled"] is False
        assert payload["reason"] == "disabled"

    def test_evaluate_unknown_flag_is_404(self, store):
        status, _, _ = post_json(
            store, "/api/evaluate", {"key": "ghost", "user_id": "bob"}
        )
        assert status == 404

    def test_evaluate_without_user_id_is_422(self, store):
        store.put_flag({"key": "checkout"})
        status, _, _ = post_json(store, "/api/evaluate", {"key": "checkout"})
        assert status == 422

    def test_evaluate_empty_user_id_is_422(self, store):
        store.put_flag({"key": "checkout"})
        status, _, _ = post_json(
            store, "/api/evaluate", {"key": "checkout", "user_id": "  "}
        )
        assert status == 422

    def test_evaluate_all(self, store):
        store.put_flag({"key": "a", "enabled": True, "rollout": 100})
        store.put_flag({"key": "b"})
        status, _, payload = route_request(
            store, "GET", "/api/evaluate/all", {"user_id": ["bob"]}
        )
        assert status == 200
        assert payload["count"] == 2
        assert payload["user_id"] == "bob"
        assert [f["enabled"] for f in payload["flags"]] == [True, False]

    def test_evaluate_all_without_user_is_422(self, store):
        status, _, _ = route_request(store, "GET", "/api/evaluate/all")
        assert status == 422


class TestRouting:
    def test_unknown_api_path_is_404(self, store):
        status, _, _ = route_request(store, "GET", "/api/nope")
        assert status == 404

    def test_non_api_path_is_404_from_router(self, store):
        # The HTTP layer serves static files for these; the router itself does not.
        status, _, _ = route_request(store, "GET", "/index.html")
        assert status == 404

    def test_wrong_method_is_405(self, store):
        status, _, _ = route_request(store, "PUT", "/api/flags")
        assert status == 405
        status, _, _ = route_request(store, "POST", "/api/health")
        assert status == 405
        status, _, _ = route_request(store, "DELETE", "/api/evaluate")
        assert status == 405


@pytest.fixture()
def live_server():
    store = FlagStore(":memory:")
    store.put_flag({"key": "checkout", "enabled": True, "rollout": 100})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        store.close()


class TestEndToEnd:
    def test_static_client_is_served(self, live_server):
        with urllib.request.urlopen(f"{live_server}/", timeout=5) as response:
            body = response.read().decode("utf-8")
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/html")
        assert "<!DOCTYPE html>" in body
        assert "Feature Flags" in body

    def test_api_over_http(self, live_server):
        with urllib.request.urlopen(
            f"{live_server}/api/evaluate?key=checkout&user_id=bob", timeout=5
        ) as response:
            payload = json.loads(response.read())
        assert payload["enabled"] is True

    def test_create_over_http(self, live_server):
        request = urllib.request.Request(
            f"{live_server}/api/flags",
            data=json.dumps({"key": "search-v2", "rollout": 10}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
            assert response.status == 201
        assert payload["flag"]["key"] == "search-v2"

    def test_unknown_flag_is_404_over_http(self, live_server):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(
                f"{live_server}/api/flags/ghost", timeout=5
            )
        assert excinfo.value.code == 404

    def test_client_directory_exists(self):
        import os

        assert os.path.isfile(os.path.join(CLIENT_DIR, "index.html"))
