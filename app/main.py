"""Web routes: start an invasion, follow its progress, serve the game. The workflow itself is in invasion.py.

    POST /api/invasions {"city": "Tokyo"}   → 202 {"id": "…"}            starts a game in the background
    GET  /api/invasions/{id}                → {"status", "steps", "pack"}  poll every ~1.5 s until "ready"

Starting a job and polling it keeps every request short, which matters behind Runpod's Pod proxy (100 s limit).
"""
import asyncio
import logging
import math
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import USER_AGENT, CityNotFound, Settings, UpstreamError
from app.invasion import Progress, run_invasion, save
from app.rules import NoPlanetsOverhead

ROOT = Path(__file__).resolve().parent.parent
FAULTS = frozenset({"weather_down", "nasa_down", "llm_down", "gpu_down"})
SECONDS_BETWEEN_GAMES = 20
MAX_JOBS_KEPT = 200
log = logging.getLogger("invasion")


class InvasionRequest(BaseModel):
    # Letters in any language, plus the spaces and punctuation real place names use ("St. John's", "Ho Chi Minh").
    city: str = Field(min_length=2, max_length=80, pattern=r"^[^\W\d_][\w\s.,'’()\-]*$")


@dataclass
class Job:
    id: str
    progress: Progress = field(default_factory=Progress)
    status: str = "running"  # running → ready | failed
    pack: str | None = None  # URL of pack.json once ready
    error: dict | None = None
    task: asyncio.Task | None = None


class Limits:
    """One game per player every 20 s, plus a daily cap across all players that protects the Runpod credit."""

    def __init__(self, per_day: int):
        self.per_day, self.last_start, self.day, self.count = per_day, {}, None, 0

    def check(self, player: str, now: float | None = None) -> tuple[str, int] | None:
        now = time.time() if now is None else now
        wait = SECONDS_BETWEEN_GAMES - (now - self.last_start.get(player, -math.inf))
        if wait > 0:
            return "too_fast", math.ceil(wait)
        today = datetime.fromtimestamp(now, timezone.utc).date()
        if self.day != today:
            self.day, self.count = today, 0
        if self.count >= self.per_day:
            midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), timezone.utc)
            return "daily_limit", math.ceil(midnight.timestamp() - now)
        self.last_start[player], self.count = now, self.count + 1
        return None

    def refund(self, player: str) -> None:
        """A game that failed before spending anything doesn't count, so TRY AGAIN works straight away."""
        self.last_start.pop(player, None)
        self.count = max(0, self.count - 1)


def create_app(settings: Settings | None = None, http: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    games_dir = settings.data_dir / "games"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.runpod_api_key:
            log.warning("RUNPOD_API_KEY is not set: games will use built-in descriptions and silhouettes")
        games_dir.mkdir(parents=True, exist_ok=True)
        app.state.settings, app.state.jobs, app.state.limits = settings, {}, Limits(settings.max_games_per_day)
        if http:
            app.state.http = http
            yield
        else:
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=settings.http_timeout_s) as client:
                app.state.http = client
                yield

    app = FastAPI(title="Invasion Forecast", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_: Request, __: RequestValidationError) -> JSONResponse:
        return error(422, "invalid_city", "Type a city name: letters, 2 to 80 characters.")

    @app.post("/api/invasions", status_code=202)
    async def start_invasion(body: InvasionRequest, request: Request, simulate: str = ""):
        state = request.app.state
        player = player_ip(request)
        if blocked := state.limits.check(player):
            code, wait = blocked
            message = (f"One invasion every {SECONDS_BETWEEN_GAMES} seconds. Next coin in {wait} s."
                       if code == "too_fast" else "Today's invasions are used up. Come back tomorrow.")
            return error(429, code, message, retry_after=wait)
        faults = frozenset(simulate.split(",")) & FAULTS if state.settings.demo_faults else frozenset()
        job = Job(secrets.token_hex(4))
        state.jobs[job.id] = job
        while len(state.jobs) > MAX_JOBS_KEPT:
            state.jobs.pop(next(iter(state.jobs)))
        job.task = asyncio.create_task(run_job(state, job, body.city.strip(), faults, player))
        return {"id": job.id, "poll": f"/api/invasions/{job.id}"}

    @app.get("/api/invasions/{job_id}")
    async def invasion_status(job_id: str, request: Request):
        job = request.app.state.jobs.get(job_id)
        if not job:
            return error(404, "unknown_invasion", "That invasion has expired. Start a new one.")
        return {"id": job.id, "status": job.status, "steps": [asdict(s) for s in job.progress.steps],
                "images": f"/games/{job.id}/", "pack": job.pack, "error": job.error}

    @app.get("/api/packs")
    async def saved_packs():
        """Pre-made games shipped with the repo: offline play, the GPU-down fallback, the demo backup."""
        return [{"name": p.parent.name, "url": f"/packs/{p.parent.name}/pack.json"}
                for p in sorted((ROOT / "packs").glob("*/pack.json"))]

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    app.mount("/games", StaticFiles(directory=games_dir, check_dir=False), name="games")
    app.mount("/packs", StaticFiles(directory=ROOT / "packs", check_dir=False), name="packs")
    app.mount("/", StaticFiles(directory=ROOT / "web", html=True, check_dir=False), name="web")
    return app


async def run_job(state, job: Job, city: str, faults: frozenset, player: str = "") -> None:
    folder = state.settings.data_dir / "games" / job.id

    def on_image(name: str, png: bytes) -> None:  # each image is served as soon as it's painted
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(png)

    try:
        game = await run_invasion(state.http, state.settings, city, job.progress, faults, on_image=on_image)
        save(game, folder)
        job.pack, job.status = f"/games/{job.id}/pack.json", "ready"
        return
    except CityNotFound:
        job.error = {"code": "city_not_found",
                     "message": f"We couldn't find a city called “{city}”. Check the spelling or try a nearby town."}
    except NoPlanetsOverhead:
        job.error = {"code": "no_planets",
                     "message": "No known planets are overhead there right now. Try another city."}
    except UpstreamError as e:
        log.warning("invasion %s failed: %s", job.id, e)
        job.error = ({"code": "weather_down", "message": "Open-Meteo isn't answering, so we can't locate your city. "
                                                          "No coin was spent. Try again in a minute."}
                     if e.service == "open-meteo" else {"code": "upstream_failed", "message": str(e)})
    except Exception:
        log.exception("invasion %s crashed", job.id)
        job.error = {"code": "internal", "message": "Something went wrong on our side. Please try again."}
    job.status = "failed"
    if job.error["code"] in ("city_not_found", "no_planets", "weather_down"):
        state.limits.refund(player)  # failed in step 1 or 2: nothing was spent


def player_ip(request: Request) -> str:
    """The player's real address, for the rate limit.

    Runpod's proxy sits behind Cloudflare, which sets CF-Connecting-IP to the real client address (and overwrites
    any value a client sends). X-Forwarded-For can be forged by the client, so only its last hop, the one a proxy
    added, is used as a fallback.
    """
    if cf := request.headers.get("cf-connecting-ip", "").strip():
        return cf
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return hops[-1] if hops else (request.client.host if request.client else "unknown")


def error(status: int, code: str, message: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


app = create_app()
