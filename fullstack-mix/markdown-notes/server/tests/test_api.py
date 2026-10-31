"""Tests for the HTTP router, driven directly with no sockets."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from app import make_handler, route_request
from http.server import ThreadingHTTPServer


def call(store, method, path, query=None, payload=None, raw=None):
    if raw is None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    else:
        body = raw
    return route_request(store, method, path, query or {}, body)


def test_health(store):
    store.add(title="x")
    status, headers, payload = call(store, "GET", "/api/health")
    assert status == 200
    assert payload == {"status": "ok", "notes": 1}
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_create_note(store):
    status, _, payload = call(store, "POST", "/api/notes", payload={"body": "# Title\n\ntext"})
    assert status == 201
    assert payload["note"]["title"] == "Title"
    assert payload["note"]["id"] == 1


def test_create_note_rejects_invalid_json(store):
    status, _, payload = call(store, "POST", "/api/notes", raw=b"{not json")
    assert status == 400
    assert payload["error"] == "body must be valid JSON"


def test_create_note_rejects_json_array(store):
    status, _, payload = call(store, "POST", "/api/notes", raw=b"[]")
    assert status == 400
    assert payload["error"] == "body must be a JSON object"


def test_create_note_rejects_non_string_body(store):
    status, _, payload = call(store, "POST", "/api/notes", payload={"body": 42})
    assert status == 422
    assert "body must be a string" in payload["error"]


def test_list_notes_with_query(store):
    store.add(title="alpha")
    store.add(title="beta")
    status, _, payload = call(store, "GET", "/api/notes", query={"q": ["bet"]})
    assert status == 200
    assert [n["title"] for n in payload["notes"]] == ["beta"]


def test_get_note_and_missing_note(store):
    note = store.add(title="one")
    status, _, payload = call(store, "GET", f"/api/notes/{note.id}")
    assert status == 200 and payload["note"]["title"] == "one"

    status, _, payload = call(store, "GET", "/api/notes/999")
    assert status == 404 and payload["error"] == "note not found"


def test_non_numeric_id_is_a_bad_request(store):
    status, _, payload = call(store, "GET", "/api/notes/abc")
    assert status == 400
    assert payload["error"] == "id must be an integer"


def test_update_note(store):
    note = store.add(title="one", body="old")
    status, _, payload = call(store, "PUT", f"/api/notes/{note.id}", payload={"body": "new"})
    assert status == 200
    assert payload["note"]["body"] == "new"
    assert payload["note"]["title"] == "one"


def test_update_missing_note(store):
    status, _, payload = call(store, "PUT", "/api/notes/5", payload={"title": "x"})
    assert status == 404


def test_update_with_no_fields(store):
    note = store.add(title="one")
    status, _, payload = call(store, "PUT", f"/api/notes/{note.id}", payload={})
    assert status == 422
    assert payload["error"] == "nothing to update"


def test_delete_note(store):
    note = store.add(title="one")
    status, _, payload = call(store, "DELETE", f"/api/notes/{note.id}")
    assert status == 204 and payload is None
    status, _, payload = call(store, "DELETE", f"/api/notes/{note.id}")
    assert status == 404


def test_preview_renders_markdown(store):
    status, _, payload = call(store, "POST", "/api/preview", payload={"text": "# Hi\n\n**bold**"})
    assert status == 200
    assert payload["html"] == '<h1 id="hi">Hi</h1>\n<p><strong>bold</strong></p>'
    assert payload["headings"] == [{"level": 1, "text": "Hi", "id": "hi"}]


def test_preview_escapes_html(store):
    status, _, payload = call(store, "POST", "/api/preview", payload={"text": "<script>x</script>"})
    assert status == 200
    assert "<script>" not in payload["html"]


def test_preview_requires_text_string(store):
    status, _, payload = call(store, "POST", "/api/preview", payload={"text": None})
    assert status == 422


def test_preview_rejects_get(store):
    status, _, _ = call(store, "GET", "/api/preview")
    assert status == 405


def test_unknown_routes(store):
    assert call(store, "GET", "/api/nope")[0] == 404
    assert call(store, "GET", "/elsewhere")[0] == 404
    assert call(store, "PATCH", "/api/notes")[0] == 405
    assert call(store, "POST", "/api/notes/1")[0] == 405


@pytest.fixture()
def live_server(store):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_end_to_end_over_http(live_server):
    request = urllib.request.Request(
        f"{live_server}/api/notes",
        data=json.dumps({"title": "Live", "body": "hi"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 201
        note_id = json.load(response)["note"]["id"]

    with urllib.request.urlopen(f"{live_server}/api/notes/{note_id}", timeout=5) as response:
        assert json.load(response)["note"]["title"] == "Live"

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{live_server}/api/notes/999", timeout=5)
    assert excinfo.value.code == 404


def test_static_client_is_served(live_server):
    with urllib.request.urlopen(f"{live_server}/", timeout=5) as response:
        body = response.read().decode("utf-8")
    assert response.status == 200
    assert "Markdown Notes" in body


def test_static_path_traversal_is_refused(live_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{live_server}/../server/app.py", timeout=5)
    assert excinfo.value.code in (403, 404)
