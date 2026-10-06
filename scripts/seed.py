"""Fill the database with generated sample users and events.

    python -m scripts.seed            # 200 days of data ending today
    python -m scripts.seed --days 90
    python -m scripts.seed --if-empty # only when the tables hold no users yet

Replaces whatever is in the users and events tables. The data is random but
seeded, so every run produces the same shape.
"""

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

from app.db import database_url

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
CHANNELS = [("invite", 0.35), ("search", 0.25), ("social", 0.22), ("direct", 0.18)]
PLATFORMS = [("ios", 0.45), ("android", 0.30), ("web", 0.25)]
# Chance of joining a group, the step where channels differ most.
JOIN_RATE = {"invite": 0.84, "search": 0.52, "social": 0.46, "direct": 0.60}
RETENTION_WEEKS = 8


def pick(rng: random.Random, weighted):
    return rng.choices([k for k, _ in weighted], [w for _, w in weighted])[0]


def generate(days: int, end: datetime, seed: int = 20261006):
    rng = random.Random(seed)
    users, events = [], []
    minute = timedelta(minutes=1)
    for d in range(days):
        day_start = end - timedelta(days=days - d)
        weekend = day_start.weekday() >= 5
        count = round((34 + d * 0.17) * (0.72 if weekend else 1) * rng.uniform(0.85, 1.15))
        for _ in range(count):
            uid = len(users) + 1
            channel, platform = pick(rng, CHANNELS), pick(rng, PLATFORMS)
            signed_up = day_start + rng.uniform(0, 1440) * minute
            users.append((uid, signed_up, channel, platform))
            steps = [("signed_up", 0.0)]
            activated = joined = False
            if rng.random() < (0.80 if platform == "web" else 0.89):
                t = rng.uniform(1, 7)
                steps.append(("profile_completed", t))
                # The group step was redesigned 45 days before the end of the data.
                if rng.random() < JOIN_RATE[channel] + (0.07 if d >= days - 45 else 0):
                    x = rng.random()
                    t += rng.uniform(1, 11) if x < 0.62 else rng.uniform(30, 1430) if x < 0.9 else rng.uniform(1440, 5740)
                    steps.append(("group_joined", t))
                    joined = True
                    if rng.random() < (0.74 if platform == "web" else 0.83):
                        y = rng.random()
                        t += rng.uniform(1, 31) if y < 0.5 else rng.uniform(60, 1440) if y < 0.8 else rng.uniform(1440, 6440)
                        steps.append(("session_started", t))
                        if rng.random() < 0.87:
                            t += rng.uniform(25, 55)
                            steps.append(("session_completed", t))
                            activated = t <= 7 * 1440
            for name, offset in steps:
                events.append((uid, name, signed_up + offset * minute))
            # Later weeks: activated users come back far more often, invited ones most of all.
            base = (0.64 if channel == "invite" else 0.5) if activated else 0.16 if joined else 0.05
            for week in range(1, RETENTION_WEEKS + 1):
                if rng.random() < base * 0.9 ** (week - 1):
                    events.append((uid, "session_completed", signed_up + timedelta(weeks=week) + rng.uniform(0, 7 * 1440) * minute))
    # Nothing can have happened after the end of the data.
    return users, [e for e in events if e[2] < end]


def load(conn: psycopg.Connection, users, events) -> None:
    conn.execute(SCHEMA.read_text())
    conn.execute("TRUNCATE users, events RESTART IDENTITY")
    with conn.cursor() as cur:
        with cur.copy("COPY users (id, signed_up_at, channel, platform) FROM STDIN") as copy:
            for row in users:
                copy.write_row(row)
        with cur.copy("COPY events (user_id, name, occurred_at) FROM STDIN") as copy:
            for row in events:
                copy.write_row(row)
    conn.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT max(id) FROM users))")
    conn.commit()
    conn.execute("ANALYZE")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=200)
    parser.add_argument("--if-empty", action="store_true", help="do nothing if users already exist")
    args = parser.parse_args()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    users, events = generate(args.days, end)
    with psycopg.connect(database_url()) as conn:
        if args.if_empty:
            conn.execute(SCHEMA.read_text())
            if conn.execute("SELECT EXISTS (SELECT 1 FROM users)").fetchone()[0]:
                print("Users already exist, leaving the data alone.")
                return
        load(conn, users, events)
    print(f"Loaded {len(users):,} users and {len(events):,} events through {(end - timedelta(days=1)).date()}.")


if __name__ == "__main__":
    main()
