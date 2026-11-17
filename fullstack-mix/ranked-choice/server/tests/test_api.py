"""Tests for the HTTP routing layer (no sockets involved)."""

import json

import app
from app import EXAMPLE_REQUEST, route_request


def post(payload) -> tuple[int, object]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    status, _headers, data = route_request("POST", "/api/tabulate", body)
    return status, data


def base_request(**overrides) -> dict:
    payload = {
        "candidates": ["Aditi", "Bharat", "Chen"],
        "ballots": "Aditi > Bharat\nAditi > Bharat\nBharat > Aditi\nChen > Aditi",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Health and discovery
# ---------------------------------------------------------------------------


def test_health_is_ok():
    status, headers, payload = route_request("GET", "/api/health")
    assert status == 200
    assert payload == {"status": "ok", "service": "ranked-choice"}
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_example_request_actually_runs():
    status, _headers, payload = route_request("GET", "/api/example")
    assert status == 200
    example = payload["request"]
    assert example == EXAMPLE_REQUEST
    status, data = post(example)
    assert status == 200, data
    assert data["winner"] == "Bengaluru"
    assert len(data["rounds"]) == 3


def test_unknown_api_path_is_404():
    status, _headers, payload = route_request("GET", "/api/nope")
    assert status == 404
    assert payload == {"error": "not found"}


def test_non_api_path_is_404():
    status, _headers, payload = route_request("GET", "/index.html")
    assert status == 404
    assert payload == {"error": "not found"}


def test_wrong_method_is_405():
    status, _headers, payload = route_request("GET", "/api/tabulate")
    assert status == 405
    assert payload == {"error": "method not allowed"}


def test_options_preflight_has_no_body():
    status, headers, payload = route_request("OPTIONS", "/api/tabulate")
    assert status == 204
    assert payload is None
    assert headers["Access-Control-Allow-Origin"] == "*"


# ---------------------------------------------------------------------------
# Successful counts
# ---------------------------------------------------------------------------


def test_a_count_returns_the_full_report():
    status, data = post(base_request())
    assert status == 200
    assert data["winner"] == "Aditi"
    assert data["reason"] == "majority"
    assert set(data) >= {
        "winner",
        "tied",
        "reason",
        "candidates",
        "rounds",
        "ballots",
        "first_preferences",
        "pairwise",
        "condorcet",
        "condorcet_agrees",
    }
    assert data["ballots"]["weight"] == 4
    assert data["pairwise"]["Aditi"]["Bharat"] == 2
    assert data["pairwise"]["Bharat"]["Aditi"] == 1


def test_candidates_may_arrive_as_a_string():
    status, data = post(base_request(candidates="Aditi, Bharat\nChen"))
    assert status == 200
    assert data["candidates"] == ["Aditi", "Bharat", "Chen"]


def test_candidates_may_be_omitted_and_derived_from_ballots():
    status, data = post({"ballots": "Chen > aditi\nBharat > Chen"})
    assert status == 200
    assert data["candidates"] == ["Chen", "aditi", "Bharat"]


def test_weighted_ballot_lines_are_accepted():
    status, data = post({"ballots": "4: Aditi > Bharat\n3: Bharat > Aditi"})
    assert status == 200
    assert data["ballots"]["weight"] == 7


# ---------------------------------------------------------------------------
# Bad requests
# ---------------------------------------------------------------------------


def test_malformed_json_is_a_bad_request():
    status, data = post(b"{not json")
    assert status == 400
    assert data == {"error": "body must be valid JSON"}


def test_a_json_array_body_is_a_bad_request():
    status, data = post(b"[]")
    assert status == 400
    assert data == {"error": "body must be a JSON object"}


def test_an_empty_body_is_a_bad_request():
    status, data = post(b"")
    assert status == 400
    assert "nothing to count" in data["error"]


def test_ballots_must_be_a_string():
    status, data = post(base_request(ballots=["Aditi > Bharat"]))
    assert status == 400
    assert data["error"].startswith("ballots:")


def test_a_bad_roster_is_reported_even_with_no_ballots():
    status, data = post({"candidates": ["Aditi", "aditi"], "ballots": ""})
    assert status == 400
    assert "declared twice" in data["error"]


def test_an_undeclared_candidate_names_the_line():
    status, data = post(base_request(ballots="Aditi > Bharat\nAditi > Divya"))
    assert status == 400
    assert data["error"] == "line 2: 'Divya' is not a declared candidate"


def test_a_ballot_ranked_twice_is_rejected():
    status, data = post(base_request(ballots="Aditi > Bharat > Aditi"))
    assert status == 400
    assert "ranked twice" in data["error"]


def test_a_tie_is_a_successful_response_not_an_error():
    status, data = post({"ballots": "Aditi > Bharat\nBharat > Aditi"})
    assert status == 200
    assert data["winner"] is None
    assert data["reason"] == "tie"
    assert data["tied"] == ["Aditi", "Bharat"]


def test_an_oversized_body_is_rejected():
    status, _headers, payload = route_request(
        "POST", "/api/tabulate", b"x" * (app._MAX_BODY_BYTES + 1)
    )
    assert status == 413
    assert payload == {"error": "body is too large"}


# ---------------------------------------------------------------------------
# Server wiring
# ---------------------------------------------------------------------------


def test_the_handler_is_a_request_handler_class():
    from http.server import BaseHTTPRequestHandler

    assert issubclass(app.make_handler(), BaseHTTPRequestHandler)


def test_the_client_directory_sits_next_to_the_server():
    import os

    assert os.path.isdir(app.CLIENT_DIR)
    assert os.path.isfile(os.path.join(app.CLIENT_DIR, "index.html"))
