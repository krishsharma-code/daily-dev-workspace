"""Tests for the HTTP routing layer (no sockets involved)."""

import json

import pytest

import app
from app import EXAMPLE_REQUEST, route_request


def post(payload) -> tuple[int, object]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    status, _headers, data = route_request("POST", "/api/slots", body)
    return status, data


def base_request(**overrides) -> dict:
    payload = {
        "busy": "",
        "timezone": "Asia/Kolkata",
        "start_date": "2026-11-16",
        "end_date": "2026-11-16",
        "duration_min": 30,
        "step_min": 30,
        "limit": 100,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Health and discovery
# ---------------------------------------------------------------------------


def test_health_is_ok():
    status, headers, payload = route_request("GET", "/api/health")
    assert status == 200
    assert payload["status"] == "ok"
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_example_request_actually_runs():
    status, _headers, payload = route_request("GET", "/api/example")
    assert status == 200
    example = payload["request"]
    assert example == EXAMPLE_REQUEST
    status, data = post(example)
    assert status == 200, data
    assert data["slots"], "the example should produce at least one slot"


def test_unknown_api_path_is_404():
    status, _headers, payload = route_request("GET", "/api/nope")
    assert status == 404
    assert payload == {"error": "not found"}


def test_wrong_method_is_405():
    status, _headers, _payload = route_request("GET", "/api/slots")
    assert status == 405


def test_options_preflight_has_no_body():
    status, headers, payload = route_request("OPTIONS", "/api/slots")
    assert (status, payload) == (204, None)
    assert headers["Access-Control-Allow-Origin"] == "*"


# ---------------------------------------------------------------------------
# POST /api/slots
# ---------------------------------------------------------------------------


def test_slots_endpoint_returns_a_slot_list():
    status, data = post(base_request())
    assert status == 200
    assert len(data["slots"]) == 18
    assert data["slots"][0]["start"] == "2026-11-16T09:00:00+05:30"
    assert data["window"]["duration_min"] == 30


def test_busy_blocks_and_weekday_names_are_accepted():
    status, data = post(
        base_request(
            busy="2026-11-16T09:00+05:30/2026-11-16T12:00+05:30",
            weekdays=["mon", "tue"],
        )
    )
    assert status == 200
    assert data["slots"][0]["start"] == "2026-11-16T12:00:00+05:30"
    assert data["window"]["weekdays"] == [0, 1]


def test_numbers_sent_as_strings_still_work():
    status, data = post(base_request(duration_min="60", step_min="60", limit="2"))
    assert status == 200
    assert len(data["slots"]) == 2
    assert data["slots"][0]["duration_min"] == 60


def test_earliest_accepts_an_iso_string():
    status, data = post(base_request(earliest="2026-11-16T06:00Z", limit=1))
    assert status == 200
    assert data["slots"][0]["start"] == "2026-11-16T11:30:00+05:30"


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"{not json", "body must be valid JSON"),
        (b"[1, 2, 3]", "body must be a JSON object"),
        ({"busy": "", "end_date": "2026-11-16"}, "start_date"),
        ({"busy": 42, "start_date": "2026-11-16", "end_date": "2026-11-16"}, "busy"),
        (base_request(busy="nope"), "busy line 1"),
        (base_request(timezone="Nowhere/Special"), "not a known IANA time zone"),
        (base_request(duration_min="soon"), "expected an integer"),
        (base_request(weekdays=["funday"]), "not a weekday name"),
    ],
)
def test_bad_requests_get_a_400_with_a_reason(payload, message):
    status, data = post(payload)
    assert status == 400
    assert message in data["error"]


def test_empty_body_uses_defaults_but_still_needs_dates():
    status, data = post(b"")
    assert status == 400
    assert "start_date" in data["error"]


# ---------------------------------------------------------------------------
# Static client
# ---------------------------------------------------------------------------


def test_client_files_are_present():
    import os

    for name in ("index.html", "app.js", "styles.css"):
        assert os.path.isfile(os.path.join(app.CLIENT_DIR, name)), name
