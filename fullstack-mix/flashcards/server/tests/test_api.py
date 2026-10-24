"""Tests for the Flashcards HTTP layer (``server/app.py``).

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
from store import CardStore


@pytest.fixture()
def store():
    s = CardStore(":memory:")
    yield s
    s.close()


def post_json(store, payload, path="/api/cards"):
    body = json.dumps(payload).encode("utf-8")
    return route_request(store, "POST", path, {}, body)


class TestHealth:
    def test_health(self, store):
        status, headers, payload = route_request(store, "GET", "/api/health")
        assert status == 200
        assert payload == {"status": "ok", "cards": 0, "due": 0}
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_health_counts_after_create(self, store):
        post_json(store, {"front": "Q", "back": "A"})
        _, _, payload = route_request(store, "GET", "/api/health")
        assert payload["cards"] == 1
        assert payload["due"] == 1

    def test_unknown_api_route(self, store):
        status, _, payload = route_request(store, "GET", "/api/nope")
        assert status == 404
        assert payload["error"] == "not found"

    def test_non_api_path_is_not_found(self, store):
        status, _, _ = route_request(store, "GET", "/definitely-not-api")
        assert status == 404


class TestCreate:
    def test_create_returns_201_and_card(self, store):
        status, _, payload = post_json(store, {"front": "  Q ", "back": " A ", "deck": "math"})
        assert status == 201
        card = payload["card"]
        assert card["front"] == "Q"
        assert card["back"] == "A"
        assert card["deck"] == "math"
        assert card["interval_days"] == 0

    def test_create_defaults_deck(self, store):
        _, _, payload = post_json(store, {"front": "Q", "back": "A"})
        assert payload["card"]["deck"] == "default"

    def test_create_rejects_bad_json(self, store):
        status, _, payload = route_request(store, "POST", "/api/cards", {}, b"{not json")
        assert status == 400
        assert "JSON" in payload["error"]

    def test_create_rejects_non_object(self, store):
        status, _, _ = route_request(store, "POST", "/api/cards", {}, b"[1, 2]")
        assert status == 400

    def test_create_requires_front_and_back(self, store):
        status, _, _ = post_json(store, {"back": "A"})
        assert status == 422
        status, _, _ = post_json(store, {"front": "Q"})
        assert status == 422


class TestListAndDue:
    def test_list_empty(self, store):
        status, _, payload = route_request(store, "GET", "/api/cards")
        assert status == 200
        assert payload == {"cards": []}

    def test_list_returns_created_cards(self, store):
        post_json(store, {"front": "A", "back": "1"})
        post_json(store, {"front": "B", "back": "2"})
        _, _, payload = route_request(store, "GET", "/api/cards")
        assert [c["front"] for c in payload["cards"]] == ["A", "B"]

    def test_list_filters_by_deck(self, store):
        post_json(store, {"front": "A", "back": "1", "deck": "math"})
        post_json(store, {"front": "B", "back": "2", "deck": "bio"})
        _, _, payload = route_request(store, "GET", "/api/cards", {"deck": ["math"]})
        assert [c["front"] for c in payload["cards"]] == ["A"]

    def test_due_lists_new_cards(self, store):
        post_json(store, {"front": "A", "back": "1"})
        status, _, payload = route_request(store, "GET", "/api/cards/due")
        assert status == 200
        assert [c["front"] for c in payload["cards"]] == ["A"]

    def test_due_excludes_reviewed_card(self, store):
        _, _, created = post_json(store, {"front": "A", "back": "1"})
        route_request(
            store, "POST", f"/api/cards/{created['card']['id']}/review", {}, b'{"quality": 5}'
        )
        _, _, payload = route_request(store, "GET", "/api/cards/due")
        assert payload["cards"] == []


class TestDecks:
    def test_decks_summary(self, store):
        post_json(store, {"front": "A", "back": "1", "deck": "math"})
        post_json(store, {"front": "B", "back": "2", "deck": "bio"})
        status, _, payload = route_request(store, "GET", "/api/decks")
        assert status == 200
        assert {d["deck"] for d in payload["decks"]} == {"math", "bio"}


class TestReview:
    def test_review_returns_updated_card(self, store):
        _, _, created = post_json(store, {"front": "A", "back": "1"})
        card_id = created["card"]["id"]
        status, _, payload = post_json(store, {"quality": 5}, f"/api/cards/{card_id}/review")
        assert status == 200
        assert payload["card"]["interval_days"] == 1
        assert payload["card"]["repetitions"] == 1

    def test_review_missing_card(self, store):
        status, _, _ = post_json(store, {"quality": 5}, "/api/cards/42/review")
        assert status == 404

    def test_review_requires_quality(self, store):
        _, _, created = post_json(store, {"front": "A", "back": "1"})
        status, _, payload = post_json(store, {}, f"/api/cards/{created['card']['id']}/review")
        assert status == 422
        assert "quality" in payload["error"]

    def test_review_rejects_bad_quality(self, store):
        _, _, created = post_json(store, {"front": "A", "back": "1"})
        status, _, _ = post_json(store, {"quality": 9}, f"/api/cards/{created['card']['id']}/review")
        assert status == 422

    def test_review_wrong_method(self, store):
        _, _, created = post_json(store, {"front": "A", "back": "1"})
        status, _, _ = route_request(store, "GET", f"/api/cards/{created['card']['id']}/review")
        assert status == 405


class TestSingleAndDelete:
    def test_get_one(self, store):
        _, _, created = post_json(store, {"front": "A", "back": "1"})
        card_id = created["card"]["id"]
        status, _, payload = route_request(store, "GET", f"/api/cards/{card_id}")
        assert status == 200
        assert payload["card"]["id"] == card_id

    def test_get_missing(self, store):
        status, _, _ = route_request(store, "GET", "/api/cards/42")
        assert status == 404

    def test_get_non_integer_id(self, store):
        status, _, payload = route_request(store, "GET", "/api/cards/abc")
        assert status == 400

    def test_delete(self, store):
        _, _, created = post_json(store, {"front": "A", "back": "1"})
        card_id = created["card"]["id"]
        status, _, payload = route_request(store, "DELETE", f"/api/cards/{card_id}")
        assert status == 204
        assert payload is None
        assert store.count() == 0

    def test_delete_missing(self, store):
        status, _, _ = route_request(store, "DELETE", "/api/cards/7")
        assert status == 404

    def test_method_not_allowed(self, store):
        status, _, _ = route_request(store, "PUT", "/api/cards")
        assert status == 405


class TestEndToEnd:
    def test_real_server_round_trip_and_static(self):
        store = CardStore(":memory:")
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
                f"{base}/api/cards",
                data=json.dumps({"front": "Q", "back": "A", "deck": "e2e"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as resp:
                assert resp.status == 201
                created = json.loads(resp.read())["card"]

            # Review
            review = urllib.request.Request(
                f"{base}/api/cards/{created['id']}/review",
                data=json.dumps({"quality": 5}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(review, timeout=5) as resp:
                assert resp.status == 200
                assert json.loads(resp.read())["card"]["repetitions"] == 1

            # Static front end is served from the same origin.
            with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
                assert resp.status == 200
                assert "Flashcards" in html

            # Delete
            delete = urllib.request.Request(f"{base}/api/cards/{created['id']}", method="DELETE")
            with urllib.request.urlopen(delete, timeout=5) as resp:
                assert resp.status == 204

            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(f"{base}/api/cards/{created['id']}", timeout=5)
            assert excinfo.value.code == 404
        finally:
            httpd.shutdown()
            httpd.server_close()
            store.close()
            thread.join(timeout=5)

    def test_client_dir_points_at_client_folder(self):
        assert app_module.CLIENT_DIR.endswith("client")
