"""Onboarding metrics, each computed by one SQL query over the users and events tables.

Definitions used throughout:
- A funnel step counts only if it happened within 7 days of sign-up and every
  earlier step happened too.
- A user is "activated" once they complete a focus session inside that window.
- "Week N" for a user is the 7 days starting N weeks after they signed up.
"""

from datetime import datetime, timedelta

from psycopg import Connection

STEPS = [
    ("signed_up", "Signed up"),
    ("profile_completed", "Set up profile"),
    ("group_joined", "Joined or created a group"),
    ("session_started", "Started first session"),
    ("session_completed", "Completed first session"),
]
CHANNELS = {"invite": "Invite link", "search": "Search", "social": "Social", "direct": "Direct"}
PLATFORMS = {"ios": "iOS", "android": "Android", "web": "Web"}
TIME_BUCKETS = ["Under 1 h", "1–2 h", "2–6 h", "6–24 h", "1–3 days", "3–7 days"]
COHORT_COUNT = 10
COHORT_WEEKS = 8

# Shared by most queries: the users in the selected window, the first time each
# one reached every step, and whether they activated.
ONBOARDING_CTE = """
WITH cohort AS (
    SELECT id, signed_up_at, channel
    FROM users
    WHERE signed_up_at >= %(start)s AND signed_up_at < %(end)s
      AND (%(channel)s::text IS NULL OR channel = %(channel)s)
      AND (%(platform)s::text IS NULL OR platform = %(platform)s)
),
firsts AS (
    SELECT c.id, c.signed_up_at, c.channel,
           min(e.occurred_at) FILTER (WHERE e.name = 'profile_completed') AS profile_at,
           min(e.occurred_at) FILTER (WHERE e.name = 'group_joined')      AS group_at,
           min(e.occurred_at) FILTER (WHERE e.name = 'session_started')   AS started_at,
           min(e.occurred_at) FILTER (WHERE e.name = 'session_completed') AS completed_at
    FROM cohort c
    LEFT JOIN events e
           ON e.user_id = c.id
          AND e.occurred_at >= c.signed_up_at
          AND e.occurred_at <  c.signed_up_at + interval '7 days'
    GROUP BY c.id, c.signed_up_at, c.channel
),
onboarding AS (
    SELECT id, signed_up_at, channel,
           profile_at IS NOT NULL AS did_profile,
           profile_at IS NOT NULL AND group_at IS NOT NULL AS did_group,
           profile_at IS NOT NULL AND group_at IS NOT NULL AND started_at IS NOT NULL AS did_start,
           CASE WHEN profile_at IS NOT NULL AND group_at IS NOT NULL AND started_at IS NOT NULL
                THEN completed_at END AS activated_at
    FROM firsts
)
"""


def data_through(conn: Connection) -> datetime | None:
    """Midnight after the latest event. Every window ends here, not at the wall clock."""
    row = conn.execute(
        "SELECT date_trunc('day', max(occurred_at)) + interval '1 day' AS as_of FROM events"
    ).fetchone()
    return row["as_of"]


def _params(start: datetime, end: datetime, channel: str | None, platform: str | None) -> dict:
    return {"start": start, "end": end, "channel": channel, "platform": platform}


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def summary(conn, as_of, start, end, channel=None, platform=None) -> dict:
    row = conn.execute(
        ONBOARDING_CTE
        + """
        SELECT count(*) AS signups,
               count(activated_at) AS activated,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch FROM activated_at - signed_up_at) / 60
               ) AS median_minutes,
               count(*) FILTER (WHERE mature) AS week1_eligible,
               count(*) FILTER (WHERE mature AND retained) AS week1_retained
        FROM (
            SELECT o.*,
                   -- only users whose week 1 has fully passed can be judged on it
                   o.signed_up_at + interval '14 days' <= %(as_of)s AS mature,
                   EXISTS (
                       SELECT 1 FROM events e
                       WHERE e.user_id = o.id AND e.name = 'session_completed'
                         AND e.occurred_at >= o.signed_up_at + interval '7 days'
                         AND e.occurred_at <  o.signed_up_at + interval '14 days'
                   ) AS retained
            FROM onboarding o
        ) t
        """,
        {**_params(start, end, channel, platform), "as_of": as_of},
    ).fetchone()
    return {
        "signups": row["signups"],
        "activated": row["activated"],
        "activation_rate": _rate(row["activated"], row["signups"]),
        "median_minutes": float(row["median_minutes"]) if row["median_minutes"] is not None else None,
        "week1_retention": _rate(row["week1_retained"], row["week1_eligible"]),
    }


def overview(conn, as_of, days, channel=None, platform=None) -> dict:
    span = timedelta(days=days)
    return {
        "current": summary(conn, as_of, as_of - span, as_of, channel, platform),
        "previous": summary(conn, as_of, as_of - 2 * span, as_of - span, channel, platform),
    }


def funnel(conn, start, end, channel=None, platform=None) -> list[dict]:
    row = conn.execute(
        ONBOARDING_CTE
        + """
        SELECT count(*) AS signed_up,
               count(*) FILTER (WHERE did_profile) AS profile_completed,
               count(*) FILTER (WHERE did_group)   AS group_joined,
               count(*) FILTER (WHERE did_start)   AS session_started,
               count(activated_at)                 AS session_completed
        FROM onboarding
        """,
        _params(start, end, channel, platform),
    ).fetchone()
    return [{"key": key, "label": label, "users": row[key]} for key, label in STEPS]


def by_channel(conn, start, end, platform=None) -> list[dict]:
    rows = conn.execute(
        ONBOARDING_CTE
        + """
        SELECT channel, count(*) AS signups, count(activated_at) AS activated
        FROM onboarding
        GROUP BY channel
        """,
        _params(start, end, None, platform),
    ).fetchall()
    found = {r["channel"]: r for r in rows}
    out = []
    for key, label in CHANNELS.items():
        r = found.get(key, {"signups": 0, "activated": 0})
        out.append({
            "channel": key, "label": label, "signups": r["signups"], "activated": r["activated"],
            "activation_rate": _rate(r["activated"], r["signups"]),
        })
    return out


def trend(conn, start, end, channel=None, platform=None) -> list[dict]:
    rows = conn.execute(
        ONBOARDING_CTE
        + """
        SELECT d::date AS date, count(o.id) AS signups, count(o.activated_at) AS activated
        FROM generate_series(%(start)s::timestamptz,
                             %(end)s::timestamptz - interval '1 day',
                             interval '1 day') AS d
        LEFT JOIN onboarding o
               ON o.signed_up_at >= d AND o.signed_up_at < d + interval '1 day'
        GROUP BY d
        ORDER BY d
        """,
        _params(start, end, channel, platform),
    ).fetchall()
    return [{"date": r["date"].isoformat(), "signups": r["signups"], "activated": r["activated"]} for r in rows]


def time_to_activate(conn, start, end, channel=None, platform=None) -> dict:
    rows = conn.execute(
        ONBOARDING_CTE
        + """
        SELECT CASE WHEN minutes < 60   THEN 0
                    WHEN minutes < 120  THEN 1
                    WHEN minutes < 360  THEN 2
                    WHEN minutes < 1440 THEN 3
                    WHEN minutes < 4320 THEN 4
                    ELSE 5 END AS bucket,
               count(*) AS users
        FROM (
            SELECT extract(epoch FROM activated_at - signed_up_at) / 60 AS minutes
            FROM onboarding
            WHERE activated_at IS NOT NULL
        ) t
        GROUP BY bucket
        """,
        _params(start, end, channel, platform),
    ).fetchall()
    counts = {r["bucket"]: r["users"] for r in rows}
    buckets = [{"label": label, "users": counts.get(i, 0)} for i, label in enumerate(TIME_BUCKETS)]
    return {"total": sum(b["users"] for b in buckets), "buckets": buckets}


def cohorts(conn, as_of, channel=None, platform=None) -> dict:
    """Weekly retention for the last COHORT_COUNT weekly sign-up cohorts.

    Cohort `idx` 0 is the oldest. A cell is null until every user in the cohort
    has lived through that whole week, so recent cohorts are never understated.
    """
    rows = conn.execute(
        """
        WITH cohort AS (
            SELECT id, signed_up_at,
                   %(n)s - 1 - floor(extract(epoch FROM %(as_of)s - signed_up_at) / 604800)::int AS idx
            FROM users
            WHERE signed_up_at > %(as_of)s - make_interval(weeks => %(n)s)
              AND signed_up_at < %(as_of)s
              AND (%(channel)s::text IS NULL OR channel = %(channel)s)
              AND (%(platform)s::text IS NULL OR platform = %(platform)s)
        ),
        active AS (
            SELECT DISTINCT c.id, c.idx,
                   floor(extract(epoch FROM e.occurred_at - c.signed_up_at) / 604800)::int AS week
            FROM cohort c
            JOIN events e
              ON e.user_id = c.id AND e.name = 'session_completed'
             AND e.occurred_at >= c.signed_up_at + interval '7 days'
        )
        SELECT idx, 0 AS week, count(*) AS users FROM cohort GROUP BY idx
        UNION ALL
        SELECT idx, week, count(*) AS users FROM active WHERE week <= %(weeks)s GROUP BY idx, week
        """,
        {"as_of": as_of, "n": COHORT_COUNT, "weeks": COHORT_WEEKS, "channel": channel, "platform": platform},
    ).fetchall()
    counts = {(r["idx"], r["week"]): r["users"] for r in rows}
    out = []
    for idx in range(COHORT_COUNT):
        size = counts.get((idx, 0), 0)
        retention = []
        for week in range(1, COHORT_WEEKS + 1):
            observed = week <= COHORT_COUNT - 2 - idx
            retention.append(counts.get((idx, week), 0) / size if observed and size else None)
        start = as_of - timedelta(weeks=COHORT_COUNT - idx)
        out.append({"start": start.date().isoformat(), "users": size, "retention": retention})
    return {"weeks": COHORT_WEEKS, "cohorts": out}
