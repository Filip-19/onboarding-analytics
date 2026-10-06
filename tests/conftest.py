import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://localhost/postgres")
TEST_DB = "onboarding_test"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB
# Must be set before the app creates its connection pool.
os.environ["DATABASE_URL"] = TEST_URL

from app.db import close_pool, get_pool  # noqa: E402

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def at(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def onboarded(start: datetime, *minutes: float):
    """Events for a user who went through the steps at the given minutes after sign-up."""
    names = ["profile_completed", "group_joined", "session_started", "session_completed"]
    return [("signed_up", start)] + [(n, start + timedelta(minutes=m)) for n, m in zip(names, minutes)]


# A handful of users whose metrics can be worked out by hand. The latest event is
# on 28 Feb, so the data runs through 28 Feb and every window ends at 1 Mar 00:00.
USERS = {
    # activated in 50 min, then came back in week 1
    1: ("invite", "ios", onboarded(at(2, 10, 10), 5, 10, 20, 50) + [("session_completed", at(2, 18, 10))]),
    # stopped after the profile step
    2: ("search", "web", onboarded(at(2, 10, 12), 5)),
    # activated in 3 hours, never came back
    3: ("social", "android", onboarded(at(2, 12), 5, 30, 150, 180)),
    # signed up on the last day and did nothing else
    4: ("invite", "ios", onboarded(at(2, 28, 12))),
    # finished a first session 8 days after sign-up: too late to count as
    # activated, but it is activity in week 1
    5: ("direct", "ios", onboarded(at(2, 14), 5, 60, 2 * 1440, 8 * 1440)),
    # previous 30-day period: activated in 30 min
    6: ("search", "ios", onboarded(at(1, 10), 5, 10, 15, 30)),
}
AS_OF = at(3, 1)


@pytest.fixture(scope="session")
def database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.execute(f"CREATE DATABASE {TEST_DB}")
    with psycopg.connect(TEST_URL) as conn:
        conn.execute(SCHEMA.read_text())
        for uid, (channel, platform, events) in USERS.items():
            conn.execute("INSERT INTO users VALUES (%s, %s, %s, %s)", (uid, events[0][1], channel, platform))
            for name, when in events:
                conn.execute("INSERT INTO events (user_id, name, occurred_at) VALUES (%s, %s, %s)", (uid, name, when))
        conn.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT max(id) FROM users))")
    yield
    close_pool()
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")


@pytest.fixture
def conn(database):
    with get_pool().connection() as c:
        yield c
