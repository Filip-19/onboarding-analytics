from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import metrics
from app.main import app

from .conftest import AS_OF

START = AS_OF - timedelta(days=30)


def test_data_runs_through_the_day_of_the_latest_event(conn):
    assert metrics.data_through(conn) == AS_OF


def test_summary_counts_only_activations_inside_seven_days(conn):
    s = metrics.summary(conn, AS_OF, START, AS_OF)
    assert s["signups"] == 5
    assert s["activated"] == 2  # users 1 and 3; user 5 finished on day 8
    assert s["activation_rate"] == pytest.approx(0.4)
    assert s["median_minutes"] == pytest.approx(115)  # midpoint of 50 and 180


def test_week1_retention_ignores_users_too_new_to_judge(conn):
    s = metrics.summary(conn, AS_OF, START, AS_OF)
    # users 1, 2, 3 and 5 are at least 14 days old; 1 and 5 had a session in week 1
    assert s["week1_retention"] == pytest.approx(0.5)


def test_overview_compares_with_the_previous_period(conn):
    o = metrics.overview(conn, AS_OF, 30)
    assert o["current"]["signups"] == 5
    assert o["previous"]["signups"] == 1
    assert o["previous"]["activation_rate"] == 1


def test_funnel_never_grows(conn):
    steps = metrics.funnel(conn, START, AS_OF)
    assert [s["users"] for s in steps] == [5, 4, 3, 3, 2]


def test_filters_narrow_the_funnel(conn):
    assert [s["users"] for s in metrics.funnel(conn, START, AS_OF, channel="invite")] == [2, 1, 1, 1, 1]
    assert [s["users"] for s in metrics.funnel(conn, START, AS_OF, platform="web")] == [1, 1, 0, 0, 0]


def test_channels_list_every_channel(conn):
    rows = {r["channel"]: r for r in metrics.by_channel(conn, START, AS_OF)}
    assert {k: (r["signups"], r["activated"]) for k, r in rows.items()} == {
        "invite": (2, 1), "search": (1, 0), "social": (1, 1), "direct": (1, 0),
    }
    android = {r["channel"]: r for r in metrics.by_channel(conn, START, AS_OF, platform="android")}
    assert android["invite"]["signups"] == 0 and android["invite"]["activation_rate"] is None


def test_trend_has_one_row_per_day_including_empty_days(conn):
    rows = metrics.trend(conn, START, AS_OF)
    assert len(rows) == 30
    assert rows[0]["date"] == "2026-01-30" and rows[-1]["date"] == "2026-02-28"
    by_date = {r["date"]: r for r in rows}
    assert (by_date["2026-02-10"]["signups"], by_date["2026-02-10"]["activated"]) == (2, 1)
    assert by_date["2026-02-11"]["signups"] == 0
    assert sum(r["signups"] for r in rows) == 5


def test_time_to_activate_buckets(conn):
    t = metrics.time_to_activate(conn, START, AS_OF)
    assert t["total"] == 2
    assert [b["users"] for b in t["buckets"]] == [1, 0, 1, 0, 0, 0]


def test_cohorts_leave_unfinished_weeks_empty(conn):
    c = metrics.cohorts(conn, AS_OF)["cohorts"]
    assert len(c) == metrics.COHORT_COUNT
    newest, feb8 = c[-1], c[-3]
    assert newest["users"] == 1 and newest["retention"] == [None] * 8
    assert feb8["start"] == "2026-02-08" and feb8["users"] == 4
    assert feb8["retention"][0] == pytest.approx(0.5)  # users 1 and 5
    assert feb8["retention"][1] is None  # week 2 has not finished for this cohort


@pytest.fixture
def client(database):
    return TestClient(app)


def test_api_serves_metrics_and_page(client):
    assert client.get("/api/meta").json()["data_through"] == "2026-02-28"
    assert client.get("/api/overview", params={"days": 30}).json()["current"]["signups"] == 5
    assert client.get("/api/funnel", params={"channel": "invite"}).json()[0]["users"] == 2
    assert len(client.get("/api/trend", params={"days": 60}).json()) == 60
    assert "Onboarding analytics" in client.get("/").text


def test_api_rejects_unknown_filters(client):
    assert client.get("/api/funnel", params={"channel": "billboard"}).status_code == 422
    assert client.get("/api/funnel", params={"days": 5000}).status_code == 422
