import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg import Connection
from pydantic import AwareDatetime, BaseModel

from . import metrics
from .db import close_pool, get_conn, get_pool

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

Channel = Literal["invite", "search", "social", "direct"]
Platform = Literal["ios", "android", "web"]
Days = Annotated[int, Query(ge=7, le=180, description="Length of the sign-up window, in days")]
Conn = Annotated[Connection, Depends(get_conn)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_pool()
    yield
    close_pool()


app = FastAPI(title="Onboarding Analytics API", lifespan=lifespan)


def as_of(conn: Conn) -> datetime:
    value = metrics.data_through(conn)
    if value is None:
        raise HTTPException(503, "The events table is empty. Load data with: python -m scripts.seed")
    return value


AsOf = Annotated[datetime, Depends(as_of)]


@app.get("/api/meta")
def meta(as_of: AsOf):
    return {
        "data_through": (as_of - timedelta(days=1)).date().isoformat(),
        "channels": [{"key": k, "label": v} for k, v in metrics.CHANNELS.items()],
        "platforms": [{"key": k, "label": v} for k, v in metrics.PLATFORMS.items()],
    }


@app.get("/api/overview")
def overview(conn: Conn, as_of: AsOf, days: Days = 30, channel: Channel | None = None, platform: Platform | None = None):
    return metrics.overview(conn, as_of, days, channel, platform)


@app.get("/api/funnel")
def funnel(conn: Conn, as_of: AsOf, days: Days = 30, channel: Channel | None = None, platform: Platform | None = None):
    return metrics.funnel(conn, as_of - timedelta(days=days), as_of, channel, platform)


@app.get("/api/channels")
def channels(conn: Conn, as_of: AsOf, days: Days = 30, platform: Platform | None = None):
    return metrics.by_channel(conn, as_of - timedelta(days=days), as_of, platform)


@app.get("/api/trend")
def trend(conn: Conn, as_of: AsOf, days: Days = 30, channel: Channel | None = None, platform: Platform | None = None):
    return metrics.trend(conn, as_of - timedelta(days=days), as_of, channel, platform)


@app.get("/api/time-to-activate")
def time_to_activate(conn: Conn, as_of: AsOf, days: Days = 30, channel: Channel | None = None, platform: Platform | None = None):
    return metrics.time_to_activate(conn, as_of - timedelta(days=days), as_of, channel, platform)


@app.get("/api/cohorts")
def cohorts(conn: Conn, as_of: AsOf, channel: Channel | None = None, platform: Platform | None = None):
    return metrics.cohorts(conn, as_of, channel, platform)


# ---------- ingestion: how a real app feeds the dashboard ----------

# Clocks on phones drift, so allow a little slack before calling a timestamp "in the future".
CLOCK_SKEW = timedelta(minutes=5)


class NewUser(BaseModel):
    channel: Channel
    platform: Platform
    signed_up_at: AwareDatetime | None = None


class NewEvent(BaseModel):
    user_id: int
    name: Literal["profile_completed", "group_joined", "session_started", "session_completed"]
    occurred_at: AwareDatetime | None = None


def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("INGEST_API_KEY")
    if not expected:
        raise HTTPException(503, "Ingestion is switched off. Set INGEST_API_KEY to enable it.")
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Missing or wrong X-API-Key header.")


def _timestamp(value: datetime | None) -> datetime:
    now = datetime.now(timezone.utc)
    if value is None:
        return now
    if value > now + CLOCK_SKEW:
        raise HTTPException(422, "The timestamp is in the future.")
    return value


@app.post("/api/users", status_code=201, dependencies=[Depends(require_key)])
def create_user(body: NewUser, conn: Conn):
    """Register a sign-up. Records the user and their `signed_up` event together."""
    at = _timestamp(body.signed_up_at)
    row = conn.execute(
        """
        WITH u AS (
            INSERT INTO users (signed_up_at, channel, platform)
            VALUES (%(at)s, %(channel)s, %(platform)s)
            RETURNING id, signed_up_at
        ), e AS (
            INSERT INTO events (user_id, name, occurred_at)
            SELECT id, 'signed_up', signed_up_at FROM u
        )
        SELECT id, signed_up_at FROM u
        """,
        {"at": at, "channel": body.channel, "platform": body.platform},
    ).fetchone()
    return {"id": row["id"], "signed_up_at": row["signed_up_at"], "channel": body.channel, "platform": body.platform}


@app.post("/api/events", status_code=201, dependencies=[Depends(require_key)])
def create_event(body: NewEvent, conn: Conn):
    """Record something a user did. The event cannot predate the user's sign-up."""
    at = _timestamp(body.occurred_at)
    row = conn.execute(
        """
        INSERT INTO events (user_id, name, occurred_at)
        SELECT id, %(name)s, %(at)s FROM users WHERE id = %(user_id)s AND signed_up_at <= %(at)s
        RETURNING id
        """,
        {"user_id": body.user_id, "name": body.name, "at": at},
    ).fetchone()
    if row is None:
        known = conn.execute("SELECT 1 FROM users WHERE id = %s", (body.user_id,)).fetchone()
        if known is None:
            raise HTTPException(404, f"No user with id {body.user_id}.")
        raise HTTPException(422, "The event is dated before the user signed up.")
    return {"id": row["id"], "user_id": body.user_id, "name": body.name, "occurred_at": at}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
