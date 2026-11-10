"""Tests for the HTTP layer (``server/app.py``).

Most tests call :func:`route_request` directly - it is a pure function, so no
socket is involved. One end-to-end test boots a real server to prove the wiring
(API, CORS headers and static file serving) works over HTTP.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app import CLIENT_DIR, make_handler, route_request
from store import ExpenseStore


@pytest.fixture()
def store():
    instance = ExpenseStore(":memory:")
    yield instance
    instance.close()


def post_json(store, path, payload):
    body = json.dumps(payload).encode("utf-8")
    return route_request(store, "POST", path, {}, body)


def make_group(store, name="Goa Trip", members=("asha", "rahul", "meera")):
    status, _headers, payload = post_json(
        store, "/api/groups", {"name": name, "members": list(members)}
    )
    assert status == 201, payload
    return payload["group"]


class TestHealth:
    def test_counts_groups(self, store):
        make_group(store)
        status, headers, payload = route_request(store, "GET", "/api/health")
        assert status == 200
        assert payload == {"status": "ok", "groups": 1}
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_rejects_other_methods(self, store):
        status, _headers, _payload = route_request(store, "POST", "/api/health")
        assert status == 405


class TestGroupsApi:
    def test_create_and_list(self, store):
        group = make_group(store)
        assert group["id"] == "goa-trip"

        status, _headers, payload = route_request(store, "GET", "/api/groups")
        assert status == 200
        assert payload["count"] == 1
        assert payload["groups"][0]["members"] == ["asha", "rahul", "meera"]

    def test_create_validation_error(self, store):
        status, _headers, payload = post_json(
            store, "/api/groups", {"name": "Trip", "members": ["solo"]}
        )
        assert status == 422
        assert "two members" in payload["error"]

    def test_duplicate_name_is_a_conflict(self, store):
        make_group(store)
        status, _headers, payload = post_json(
            store, "/api/groups", {"name": "Goa Trip", "members": ["a", "b"]}
        )
        assert status == 422
        assert "already exists" in payload["error"]

    def test_get_one(self, store):
        make_group(store)
        status, _headers, payload = route_request(store, "GET", "/api/groups/goa-trip")
        assert status == 200
        assert payload["group"]["name"] == "Goa Trip"
        assert payload["expense_count"] == 0
        assert payload["total"] == "0.00"

    def test_get_missing(self, store):
        status, _headers, payload = route_request(store, "GET", "/api/groups/nope")
        assert status == 404
        assert payload == {"error": "group not found"}

    def test_delete(self, store):
        make_group(store)
        status, _headers, payload = route_request(store, "DELETE", "/api/groups/goa-trip")
        assert status == 204
        assert payload is None
        status, _headers, _payload = route_request(store, "GET", "/api/groups/goa-trip")
        assert status == 404

    def test_delete_missing(self, store):
        status, _headers, _payload = route_request(store, "DELETE", "/api/groups/nope")
        assert status == 404

    def test_malformed_body(self, store):
        status, _headers, payload = route_request(
            store, "POST", "/api/groups", {}, b"{not json"
        )
        assert status == 400
        assert payload == {"error": "body must be valid JSON"}

    def test_body_must_be_an_object(self, store):
        status, _headers, payload = route_request(
            store, "POST", "/api/groups", {}, b'["a", "b"]'
        )
        assert status == 400
        assert payload == {"error": "body must be a JSON object"}


class TestSplitPreview:
    def test_equal_preview(self, store):
        status, _headers, payload = post_json(
            store,
            "/api/split",
            {
                "amount": "100",
                "split": {"type": "equal", "participants": ["asha", "rahul", "meera"]},
            },
        )
        assert status == 200
        assert payload["amount_paise"] == 10000
        assert payload["shares_display"] == {
            "asha": "33.34",
            "rahul": "33.33",
            "meera": "33.33",
        }

    def test_equal_preview_needs_participants(self, store):
        status, _headers, payload = post_json(
            store, "/api/split", {"amount": "100", "split": {"type": "equal"}}
        )
        assert status == 422
        assert payload == {"error": "an equal split needs 'participants'"}

    def test_preview_uses_the_given_participants(self, store):
        status, _headers, payload = post_json(
            store,
            "/api/split",
            {"amount": "100", "split": {"type": "equal", "participants": ["a", "b"]}},
        )
        assert status == 200
        assert payload["shares"] == {"a": 5000, "b": 5000}

    def test_weighted_preview(self, store):
        status, _headers, payload = post_json(
            store,
            "/api/split",
            {"amount": "1000", "split": {"type": "weights", "weights": {"a": 2, "b": 1}}},
        )
        assert status == 200
        assert payload["shares"] == {"a": 66667, "b": 33333}
        assert payload["shares_display"] == {"a": "666.67", "b": "333.33"}

    def test_bad_split_type(self, store):
        status, _headers, payload = post_json(
            store, "/api/split", {"amount": "100", "split": "telepathy"}
        )
        assert status == 422
        assert "split type" in payload["error"]

    def test_bad_amount(self, store):
        status, _headers, payload = post_json(
            store,
            "/api/split",
            {"amount": "ten", "split": {"type": "equal", "participants": ["a", "b"]}},
        )
        assert status == 422
        assert "amount" in payload["error"]

    def test_get_not_allowed(self, store):
        status, _headers, _payload = route_request(store, "GET", "/api/split")
        assert status == 405


class TestExpensesApi:
    def test_add_and_list(self, store):
        make_group(store)
        status, _headers, payload = post_json(
            store,
            "/api/groups/goa-trip/expenses",
            {"description": "Dinner", "payer": "asha", "amount": "300"},
        )
        assert status == 201
        assert payload["expense"]["shares"] == {
            "asha": 10000,
            "rahul": 10000,
            "meera": 10000,
        }

        status, _headers, payload = route_request(
            store, "GET", "/api/groups/goa-trip/expenses"
        )
        assert status == 200
        assert payload["count"] == 1
        assert payload["expenses"][0]["amount"] == "300.00"

    def test_exact_and_weighted_splits(self, store):
        make_group(store)
        status, _headers, payload = post_json(
            store,
            "/api/groups/goa-trip/expenses",
            {
                "description": "Cab",
                "payer": "rahul",
                "amount": "900",
                "split": {"type": "exact", "shares": {"asha": "300", "rahul": "600"}},
            },
        )
        assert status == 201
        assert payload["expense"]["split_type"] == "exact"

        status, _headers, payload = post_json(
            store,
            "/api/groups/goa-trip/expenses",
            {
                "description": "Villa",
                "payer": "meera",
                "amount": "6000",
                "split": {"type": "weights", "weights": {"asha": 2, "rahul": 2, "meera": 1}},
            },
        )
        assert status == 201
        assert payload["expense"]["shares"]["meera"] == 120000

    def test_unknown_group(self, store):
        status, _headers, payload = post_json(
            store,
            "/api/groups/nope/expenses",
            {"description": "Dinner", "payer": "asha", "amount": "300"},
        )
        assert status == 404
        assert payload == {"error": "group not found"}

    def test_validation_error(self, store):
        make_group(store)
        status, _headers, payload = post_json(
            store,
            "/api/groups/goa-trip/expenses",
            {"description": "Dinner", "payer": "stranger", "amount": "300"},
        )
        assert status == 422
        assert "payer must be one of" in payload["error"]

    def test_bad_split_object(self, store):
        make_group(store)
        status, _headers, payload = post_json(
            store,
            "/api/groups/goa-trip/expenses",
            {"description": "Dinner", "payer": "asha", "amount": "300", "split": 7},
        )
        assert status == 422
        assert payload == {"error": "split must be a string or an object"}

    def test_delete(self, store):
        make_group(store)
        status, _headers, payload = post_json(
            store,
            "/api/groups/goa-trip/expenses",
            {"description": "Dinner", "payer": "asha", "amount": "300"},
        )
        expense_id = payload["expense"]["id"]
        status, _headers, payload = route_request(
            store, "DELETE", f"/api/groups/goa-trip/expenses/{expense_id}"
        )
        assert status == 204
        status, _headers, _payload = route_request(
            store, "DELETE", f"/api/groups/goa-trip/expenses/{expense_id}"
        )
        assert status == 404

    def test_delete_with_non_numeric_id(self, store):
        make_group(store)
        status, _headers, _payload = route_request(
            store, "DELETE", "/api/groups/goa-trip/expenses/abc"
        )
        assert status == 404


class TestSettlementApi:
    def test_settlement_payload(self, store):
        make_group(store)
        post_json(
            store,
            "/api/groups/goa-trip/expenses",
            {"description": "Dinner", "payer": "asha", "amount": "300"},
        )
        status, _headers, payload = route_request(
            store, "GET", "/api/groups/goa-trip/settlement"
        )
        assert status == 200
        assert payload["total"] == "300.00"
        assert payload["balances"]["asha"]["amount"] == "200.00"
        assert {t["to"] for t in payload["settlements"]} == {"asha"}
        assert sum(t["amount"] for t in payload["settlements"]) == 20000

    def test_missing_group(self, store):
        status, _headers, _payload = route_request(
            store, "GET", "/api/groups/nope/settlement"
        )
        assert status == 404


class TestUnknownRoutes:
    @pytest.mark.parametrize("path", ["/api", "/api/whatever", "/api/groups/x/y/z"])
    def test_404(self, store, path):
        status, _headers, payload = route_request(store, "GET", path)
        assert status == 404
        assert payload == {"error": "not found"}


class TestEndToEnd:
    def test_full_flow_over_http(self, store):
        handler = make_handler(store)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def request(method, path, payload=None):
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                base + path,
                data=data,
                method=method,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                body = response.read()
                return response.status, (
                    json.loads(body) if body else None
                ), response.headers

        try:
            status, payload, headers = request(
                "POST", "/api/groups", {"name": "Goa Trip", "members": ["asha", "rahul"]}
            )
            assert status == 201
            assert headers["Access-Control-Allow-Origin"] == "*"

            status, payload, _ = request(
                "POST",
                "/api/groups/goa-trip/expenses",
                {"description": "Dinner", "payer": "asha", "amount": "500"},
            )
            assert status == 201

            status, payload, _ = request("GET", "/api/groups/goa-trip/settlement")
            assert status == 200
            assert payload["settlements"] == [
                {"from": "rahul", "to": "asha", "amount": 25000, "amount_display": "250.00"}
            ]

            # The client page is served from the same origin.
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                assert response.status == 200
                assert "text/html" in response.headers["Content-Type"]
                assert b"Expense Split" in response.read()

            with pytest.raises(urllib.error.HTTPError) as error:
                request("GET", "/api/groups/nope")
            assert error.value.code == 404
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_static_files_stay_inside_client_dir(self, store):
        assert CLIENT_DIR.endswith("client")
        handler = make_handler(store)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + "/../server/store.py", timeout=5)
            assert error.value.code in (403, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()
