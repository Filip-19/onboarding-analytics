import pytest
from fastapi.testclient import TestClient

from app.db import get_pool
from app.main import app

from .conftest import USERS

KEY = {"X-API-Key": "test-key"}


@pytest.fixture
def client(database, monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", "test-key")
    yield TestClient(app)
    # the other tests rely on the hand-checked dataset, so remove anything added here
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM users WHERE id > %s", (max(USERS),))


def sign_up(client, **overrides):
    body = {"channel": "social", "platform": "web", "signed_up_at": "2026-02-20T09:00:00Z", **overrides}
    return client.post("/api/users", json=body, headers=KEY)


def test_ingested_user_shows_up_in_the_funnel(client):
    before = [s["users"] for s in client.get("/api/funnel").json()]
    res = sign_up(client)
    assert res.status_code == 201
    uid = res.json()["id"]
    for minute, name in enumerate(["profile_completed", "group_joined", "session_started", "session_completed"], 1):
        event = {"user_id": uid, "name": name, "occurred_at": f"2026-02-20T09:0{minute}:00Z"}
        assert client.post("/api/events", json=event, headers=KEY).status_code == 201
    after = [s["users"] for s in client.get("/api/funnel").json()]
    assert after == [n + 1 for n in before]


def test_ingestion_needs_the_api_key(client):
    body = {"channel": "social", "platform": "web"}
    assert client.post("/api/users", json=body).status_code == 401
    assert client.post("/api/users", json=body, headers={"X-API-Key": "wrong"}).status_code == 401


def test_ingestion_is_off_without_a_configured_key(client, monkeypatch):
    monkeypatch.delenv("INGEST_API_KEY")
    assert sign_up(client).status_code == 503


def test_bad_events_are_rejected(client):
    uid = sign_up(client).json()["id"]
    post = lambda **e: client.post("/api/events", json={"user_id": uid, "name": "group_joined", **e}, headers=KEY)
    assert post(user_id=999999).status_code == 404
    assert post(name="rage_quit").status_code == 422
    assert post(occurred_at="2026-02-19T09:00:00Z").status_code == 422  # before sign-up
    assert post(occurred_at="2999-01-01T00:00:00Z").status_code == 422  # in the future
    assert post(occurred_at="2026-02-20T10:00:00").status_code == 422   # no time zone
