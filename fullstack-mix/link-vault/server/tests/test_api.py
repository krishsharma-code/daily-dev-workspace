"""Tests for the Link Vault HTTP layer (``server/app.py``).

Most tests call :func:`route_request` directly - it is a pure function, so no
socket is involved. One end-to-end test boots a real server to prove the wiring
(including static file serving) works.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import app as app_module
from app import make_handler, route_request
from store import LinkStore


@pytest.fixture()
def store():
    s = LinkStore(":memory:")
    yield s
    s.close()


def post_json(store, payload):
    body = json.dumps(payload).encode("utf-8")
    return route_request(store, "POST", "/api/links", {}, body)


class TestHealth:
    def test_health(self, store):
        status, headers, payload = route_request(store, "GET", "/api/health")
        assert status == 200
        assert payload == {"status": "ok", "links": 0}
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_unknown_api_route(self, store):
        status, _, payload = route_request(store, "GET", "/api/nope")
        assert status == 404
        assert payload["error"] == "not found"

    def test_non_api_path_is_not_found(self, store):
        status, _, _ = route_request(store, "GET", "/definitely-not-api")
        assert status == 404


class TestCreate:
    def test_create_returns_201_and_link(self, store):
        status, _, payload = post_json(store, {"url": "example.com", "title": "Ex", "tags": ["A", "a"]})
        assert status == 201
        link = payload["link"]
        assert link["url"] == "https://example.com"
        assert link["title"] == "Ex"
        assert link["tags"] == ["a"]

    def test_create_rejects_bad_json(self, store):
        status, _, payload = route_request(store, "POST", "/api/links", {}, b"{not json")
        assert status == 400
        assert "JSON" in payload["error"]

    def test_create_rejects_non_object(self, store):
        status, _, _ = route_request(store, "POST", "/api/links", {}, b"[1, 2]")
        assert status == 400

    def test_create_rejects_invalid_url(self, store):
        status, _, payload = post_json(store, {"url": "ftp://nope"})
        assert status == 422
        assert "scheme" in payload["error"]

    def test_create_requires_url(self, store):
        status, _, _ = post_json(store, {"title": "no url"})
        assert status == 422


class TestListAndFilters:
    def test_list_empty(self, store):
        status, _, payload = route_request(store, "GET", "/api/links")
        assert status == 200
        assert payload == {"links": []}

    def test_list_returns_created_links(self, store):
        post_json(store, {"url": "https://a.example", "title": "A"})
        post_json(store, {"url": "https://b.example", "title": "B"})
        _, _, payload = route_request(store, "GET", "/api/links")
        assert [link["title"] for link in payload["links"]] == ["B", "A"]

    def test_filter_by_tag_query_param(self, store):
        post_json(store, {"url": "https://a.example", "tags": ["python"]})
        post_json(store, {"url": "https://b.example", "tags": ["web"]})
        _, _, payload = route_request(store, "GET", "/api/links", {"tag": ["python"]})
        assert [link["url"] for link in payload["links"]] == ["https://a.example"]

    def test_filter_by_text_query_param(self, store):
        post_json(store, {"url": "https://a.example", "title": "Python docs"})
        post_json(store, {"url": "https://b.example", "title": "Rust book"})
        _, _, payload = route_request(store, "GET", "/api/links", {"q": ["rust"]})
        assert [link["title"] for link in payload["links"]] == ["Rust book"]


class TestSingleAndDelete:
    def test_get_one(self, store):
        _, _, created = post_json(store, {"url": "https://a.example"})
        link_id = created["link"]["id"]
        status, _, payload = route_request(store, "GET", f"/api/links/{link_id}")
        assert status == 200
        assert payload["link"]["id"] == link_id

    def test_get_missing(self, store):
        status, _, _ = route_request(store, "GET", "/api/links/42")
        assert status == 404

    def test_get_non_integer_id(self, store):
        status, _, payload = route_request(store, "GET", "/api/links/abc")
        assert status == 400

    def test_delete(self, store):
        _, _, created = post_json(store, {"url": "https://a.example"})
        link_id = created["link"]["id"]
        status, _, payload = route_request(store, "DELETE", f"/api/links/{link_id}")
        assert status == 204
        assert payload is None
        assert store.count() == 0

    def test_delete_missing(self, store):
        status, _, _ = route_request(store, "DELETE", "/api/links/7")
        assert status == 404

    def test_method_not_allowed(self, store):
        status, _, _ = route_request(store, "PUT", "/api/links")
        assert status == 405


class TestEndToEnd:
    def test_real_server_round_trip_and_static(self):
        store = LinkStore(":memory:")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        host, port = httpd.server_address
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://{host}:{port}"
        try:
            # Health
            with urllib.request.urlopen(f"{base}/api/health", timeout=5) as resp:
                assert resp.status == 200
                assert json.loads(resp.read())["status"] == "ok"

            # Create
            request = urllib.request.Request(
                f"{base}/api/links",
                data=json.dumps({"url": "https://e2e.example", "title": "E2E"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as resp:
                assert resp.status == 201
                created = json.loads(resp.read())["link"]

            # List
            with urllib.request.urlopen(f"{base}/api/links", timeout=5) as resp:
                assert len(json.loads(resp.read())["links"]) == 1

            # Static front end is served from the same origin.
            with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
                assert resp.status == 200
                assert "Link Vault" in html

            # Delete
            delete = urllib.request.Request(
                f"{base}/api/links/{created['id']}", method="DELETE"
            )
            with urllib.request.urlopen(delete, timeout=5) as resp:
                assert resp.status == 204

            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(f"{base}/api/links/{created['id']}", timeout=5)
            assert excinfo.value.code == 404
        finally:
            httpd.shutdown()
            httpd.server_close()
            store.close()
            thread.join(timeout=5)

    def test_client_dir_points_at_client_folder(self):
        assert app_module.CLIENT_DIR.endswith("client")
