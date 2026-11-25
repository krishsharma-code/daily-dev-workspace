"""Tests for the HTTP layer: routing, status codes and static serving."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import app as server_app
from store import HabitStore

TODAY = "2026-11-25"  # a Wednesday


@pytest.fixture()
def store():
    with HabitStore(":memory:") as habit_store:
        yield habit_store


def call(store, method, path, body=None, query=None):
    if body is None:
        raw = b""
    elif isinstance(body, bytes):
        raw = body
    else:
        raw = json.dumps(body).encode("utf-8")
    status, headers, payload = server_app.route_request(
        store, method, path, query or {}, raw
    )
    return status, headers, payload


def test_health(store):
    status, _, payload = call(store, "GET", "/api/health")
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["habits"] == 0


def test_health_rejects_post(store):
    status, headers, _ = call(store, "POST", "/api/health", {})
    assert status == 405
    assert headers["Allow"] == "GET"


def test_unknown_resource_is_404(store):
    assert call(store, "GET", "/api/nope")[0] == 404
    assert call(store, "GET", "/not-api")[0] == 404


def test_create_habit(store):
    status, headers, payload = call(
        store,
        "POST",
        "/api/habits",
        {"name": "Read 20 pages", "weekdays": ["mon", "wed", "fri"]},
        {"today": [TODAY]},
    )
    assert status == 201
    assert headers["Location"] == "/api/habits/read-20-pages"
    assert payload["id"] == "read-20-pages"
    assert payload["summary"]["current"] == 0
    assert payload["summary"]["scheduled_today"] is True


def test_create_habit_requires_a_name(store):
    status, _, payload = call(store, "POST", "/api/habits", None)
    assert status == 422
    assert "name" in payload["error"]


def test_create_habit_rejects_malformed_json(store):
    status, _, payload = call(store, "POST", "/api/habits", b"not json")
    assert status == 400
    assert "JSON" in payload["error"]


def test_create_habit_rejects_non_object_body(store):
    status, _, _ = call(store, "POST", "/api/habits", [1, 2, 3])
    assert status == 400


def test_create_habit_rejects_bad_weekdays(store):
    status, _, payload = call(store, "POST", "/api/habits", {"name": "Gym", "weekdays": []})
    assert status == 422
    assert "weekday" in payload["error"]


def test_create_habit_rejects_duplicates(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, payload = call(store, "POST", "/api/habits", {"name": "Gym"})
    assert status == 422
    assert "already exists" in payload["error"]


def test_list_habits_returns_the_board(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, payload = call(store, "GET", "/api/habits", query={"today": [TODAY]})
    assert status == 200
    assert payload["today"] == TODAY
    assert payload["counts"]["habits"] == 1


def test_board_endpoint_matches_the_list(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    listed = call(store, "GET", "/api/habits", query={"today": [TODAY]})[2]
    board = call(store, "GET", "/api/board", query={"today": [TODAY]})[2]
    assert listed == board


def test_get_one_habit(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, payload = call(
        store, "GET", "/api/habits/gym", query={"today": [TODAY]}
    )
    assert status == 200
    assert payload["name"] == "Gym"


def test_get_unknown_habit_is_404(store):
    status, _, payload = call(store, "GET", "/api/habits/ghost")
    assert status == 404
    assert "ghost" in payload["error"]


def test_habit_method_not_allowed(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, headers, _ = call(store, "PUT", "/api/habits/gym")
    assert status == 405
    assert headers["Allow"] == "GET, DELETE"


def test_check_in_then_read_back(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, payload = call(
        store,
        "POST",
        "/api/habits/gym/checkins",
        {"note": "leg day"},
        {"today": [TODAY]},
    )
    assert status == 201
    assert payload["created"] is True
    assert payload["checkin"]["day"] == TODAY
    assert payload["habit"]["summary"]["current"] == 1
    assert payload["habit"]["summary"]["done_today"] is True


def test_second_check_in_is_200_and_not_created(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    call(store, "POST", "/api/habits/gym/checkins", {}, {"today": [TODAY]})
    status, _, payload = call(
        store, "POST", "/api/habits/gym/checkins", {}, {"today": [TODAY]}
    )
    assert status == 200
    assert payload["created"] is False


def test_check_in_rejects_a_future_day(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, payload = call(
        store,
        "POST",
        "/api/habits/gym/checkins",
        {"day": "2026-11-26"},
        {"today": [TODAY]},
    )
    assert status == 422
    assert "future" in payload["error"]


def test_check_in_on_an_unknown_habit_is_404(store):
    status, _, _ = call(store, "POST", "/api/habits/ghost/checkins", {})
    assert status == 404


def test_check_in_with_a_malformed_day_is_422(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, _ = call(
        store, "POST", "/api/habits/gym/checkins", {"day": "25-11-2026"}
    )
    assert status == 422


def test_list_checkins(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    call(store, "POST", "/api/habits/gym/checkins", {"day": "2026-11-24"}, {"today": [TODAY]})
    status, _, payload = call(store, "GET", "/api/habits/gym/checkins")
    assert status == 200
    assert [entry["day"] for entry in payload["checkins"]] == ["2026-11-24"]


def test_list_checkins_of_an_unknown_habit_is_404(store):
    assert call(store, "GET", "/api/habits/ghost/checkins")[0] == 404


def test_undo_check_in(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    call(store, "POST", "/api/habits/gym/checkins", {}, {"today": [TODAY]})
    status, _, payload = call(store, "DELETE", f"/api/habits/gym/checkins/{TODAY}")
    assert status == 200
    assert payload["removed"] == TODAY
    assert call(store, "DELETE", f"/api/habits/gym/checkins/{TODAY}")[0] == 404


def test_undo_check_in_with_a_bad_day_is_422(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, _ = call(store, "DELETE", "/api/habits/gym/checkins/25-11-2026")
    assert status == 422


def test_delete_habit(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    status, _, payload = call(store, "DELETE", "/api/habits/gym")
    assert status == 200
    assert payload["deleted"] == "gym"
    assert call(store, "DELETE", "/api/habits/gym")[0] == 404


def test_bad_today_parameter_is_422(store):
    status, _, _ = call(store, "GET", "/api/habits", query={"today": ["yesterday"]})
    assert status == 422


def test_streak_uses_the_pinned_today(store):
    call(store, "POST", "/api/habits", {"name": "Gym"})
    for day in ("2026-11-23", "2026-11-24", "2026-11-25"):
        call(store, "POST", "/api/habits/gym/checkins", {"day": day}, {"today": [TODAY]})
    view = call(store, "GET", "/api/habits/gym", query={"today": [TODAY]})[2]
    assert view["summary"]["current"] == 3
    assert view["summary"]["longest"] == 3


# ---------------------------------------------------------------------------
# End to end over a real socket
# ---------------------------------------------------------------------------


@pytest.fixture()
def live_server(store):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_app.make_handler(store))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def test_live_api_round_trip(live_server):
    status, _, body = _request(f"{live_server}/api/health")
    assert status == 200
    assert json.loads(body)["status"] == "ok"

    status, _, body = _request(
        f"{live_server}/api/habits", "POST", {"name": "Gym", "weekdays": "mon,wed,fri"}
    )
    assert status == 201
    assert json.loads(body)["id"] == "gym"

    status, _, body = _request(
        f"{live_server}/api/habits/gym/checkins?today={TODAY}", "POST", {}
    )
    assert status == 201
    assert json.loads(body)["checkin"]["day"] == TODAY


def test_live_cors_preflight(live_server):
    request = urllib.request.Request(
        f"{live_server}/api/habits", method="OPTIONS"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert "POST" in response.headers["Access-Control-Allow-Methods"]


def test_live_static_index(live_server):
    status, headers, body = _request(f"{live_server}/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"Streak Board" in body


def test_live_static_traversal_is_refused(live_server):
    status, _, _ = _request(f"{live_server}/../server/app.py")
    assert status in (403, 404)


def test_live_unknown_api_path_is_404(live_server):
    status, _, _ = _request(f"{live_server}/api/ghosts")
    assert status == 404
