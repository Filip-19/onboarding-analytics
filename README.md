# Onboarding Analytics

A dashboard that shows how new users of a study-with-friends app move from sign-up to their first completed focus session, and whether they come back.

Every number on the page is computed by SQL over a raw `events` table in PostgreSQL, served by a FastAPI backend.

## What it shows

- Sign-ups, activation rate, median time to activate and week 1 retention, each compared with the previous period
- A five-step onboarding funnel with the biggest drop-off flagged
- Activation rate by acquisition channel
- Daily sign-ups and activated users
- Weekly retention for the last 10 sign-up cohorts
- How long activated users took to get there

A user is **activated** once they complete a focus session within 7 days of signing up.

## How it is built

| Part | Where | Notes |
|---|---|---|
| Database | `db/schema.sql` | `users` and an append-only `events` table |
| Metrics | `app/metrics.py` | One SQL query per metric, sharing a common table expression that finds each user's first time at every step |
| API | `app/main.py` | FastAPI, read-only JSON endpoints under `/api`, validated filters |
| Dashboard | `web/` | Plain HTML, CSS and JavaScript with hand-drawn SVG charts, no framework |
| Sample data | `scripts/seed.py` | Generates about 9,000 users and 42,000 events |
| Tests | `tests/` | A small hand-checked dataset run against a real PostgreSQL database |

Windows end at midnight after the latest event in the table, not at the current time, so the dashboard stays meaningful when the data is old.

## Run it

Needs Python 3.12+ and PostgreSQL 16.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
createdb onboarding
.venv/bin/python -m scripts.seed
.venv/bin/uvicorn app.main:app --port 8000
```

Then open http://localhost:8000. Interactive API docs are at http://localhost:8000/docs.

Set `DATABASE_URL` to use a database other than `postgresql://localhost/onboarding`.

## Test it

```bash
.venv/bin/python -m pytest
```

The tests create and drop their own `onboarding_test` database.

## Send it events

A real app feeds the dashboard through two write endpoints. They are switched off until `INGEST_API_KEY` is set, and every request must send that value in an `X-API-Key` header.

```bash
export INGEST_API_KEY=choose-a-long-random-value
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
curl -X POST localhost:8000/api/users -H "X-API-Key: $INGEST_API_KEY" -H "Content-Type: application/json" -d '{"channel": "invite", "platform": "ios"}'
```

```bash
curl -X POST localhost:8000/api/events -H "X-API-Key: $INGEST_API_KEY" -H "Content-Type: application/json" -d '{"user_id": 1, "name": "profile_completed"}'
```

Timestamps default to now. Events dated in the future, before the user's sign-up, or without a time zone are rejected.

## Deploy it

`render.yaml` describes a free web service and a free PostgreSQL database on [Render](https://render.com). Push the repository to GitHub, then in Render choose New, Blueprint and pick the repository. The first start creates the tables and loads the sample data, and Render generates the ingestion key.

Any other host works too: set `DATABASE_URL`, run `python -m scripts.seed --if-empty` once, and start uvicorn.

## API

All endpoints accept `days` (7 to 180, default 30), `channel` and `platform` unless noted.

| Endpoint | Returns |
|---|---|
| `GET /api/meta` | Date the data runs through, and the filter options |
| `GET /api/overview` | Headline figures for the current and previous period |
| `GET /api/funnel` | Users reaching each onboarding step |
| `GET /api/channels` | Activation rate per channel (no `channel` filter) |
| `GET /api/trend` | Sign-ups and activated users per day |
| `GET /api/time-to-activate` | Distribution of time from sign-up to activation |
| `GET /api/cohorts` | Weekly retention by sign-up cohort (no `days` filter) |
| `POST /api/users` | Registers a sign-up: `channel`, `platform`, optional `signed_up_at` |
| `POST /api/events` | Records an event: `user_id`, `name`, optional `occurred_at` |
