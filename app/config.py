"""Settings, error types and the one HTTP helper every API client uses."""
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

USER_AGENT = "InvasionForecast/0.1 (https://github.com/rhedgpeth/invasion-forecast)"


@dataclass(frozen=True)
class Settings:
    runpod_api_key: str = ""
    max_games_per_day: int = 200
    demo_faults: bool = False
    http_timeout_s: float = 15.0
    art_deadline_s: float = 60.0
    data_dir: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            runpod_api_key=os.environ.get("RUNPOD_API_KEY", ""),
            max_games_per_day=int(os.environ.get("MAX_GAMES_PER_DAY", "200")),
            demo_faults=os.environ.get("DEMO_FAULTS", "0") == "1",
            data_dir=Path(os.environ.get("DATA_DIR", "data")),
        )


class CityNotFound(Exception):
    """The geocoder had no match for what the player typed."""


class UpstreamError(Exception):
    """An external service failed. `service` says which one, so callers can decide what to do."""

    def __init__(self, service: str, message: str, status: int | None = None, retry_after: int | None = None):
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status = status
        self.retry_after = retry_after


async def get_json(http: httpx.AsyncClient, service: str, url: str, **params) -> object:
    """GET a JSON API and turn every way it can fail into one UpstreamError."""
    try:
        resp = await http.get(url, params=params)
    except httpx.TimeoutException as e:
        raise UpstreamError(service, "timed out") from e
    except httpx.HTTPError as e:
        raise UpstreamError(service, f"unreachable ({type(e).__name__})") from e
    if resp.status_code >= 400:
        retry = resp.headers.get("Retry-After", "")
        raise UpstreamError(service, f"HTTP {resp.status_code}", resp.status_code,
                            int(retry) if retry.isdigit() else None)
    try:
        return resp.json()
    except ValueError as e:
        raise UpstreamError(service, "returned something that isn't JSON") from e
