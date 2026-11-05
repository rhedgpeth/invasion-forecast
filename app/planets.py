"""API 2: NASA Exoplanet Archive (free, no key). Which known planets orbit stars overhead this place right now?

"Overhead" takes two steps:
1. Ask NASA only for the patch of sky around this place's meridian, so the answer is small and fast.
2. Work out each star's exact altitude here and now, and keep the ones at least 30 degrees up.

If NASA is down, the same maths runs on a bundled snapshot of the catalogue, so the game still plays.
"""
import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import httpx

from app.config import USER_AGENT, UpstreamError, get_json, request_json

TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
COLUMNS = ["pl_name", "hostname", "ra", "dec", "pl_rade", "pl_bmasse", "pl_eqt", "pl_orbper", "sy_dist"]
# Skip planets with missing or implausible values (the catalogue has a few, e.g. 1.5 K and 7,778 K).
QUALITY = "pl_eqt between 50 and 4500 and pl_rade is not null and sy_dist is not null"
SNAPSHOT = Path(__file__).with_name("planets_snapshot.json")
MIN_ALTITUDE_DEG = 30
NASA_TIMEOUT_S = 8  # live answers take 1-2 s; past this, the saved catalogue is the better experience
LIGHT_YEARS_PER_PARSEC = 3.26156


@dataclass(frozen=True)
class Planet:
    name: str
    star: str
    temp_k: float
    radius_earths: float
    mass_earths: float | None
    year_days: float | None
    light_years: int
    altitude_deg: float


@dataclass(frozen=True)
class Overhead:
    planets: list[Planet]  # coldest first
    source: str  # "live" or "snapshot"


def sidereal_degrees(when: datetime, lon_deg: float) -> float:
    """Local sidereal time: the right ascension crossing this place's meridian at `when`."""
    days_since_j2000 = when.timestamp() / 86400 + 2440587.5 - 2451545.0
    return (280.46061837 + 360.98564736629 * days_since_j2000 + lon_deg) % 360


def altitude_degrees(ra_deg: float, dec_deg: float, lat_deg: float, lst_deg: float) -> float:
    """How high a star stands above the horizon (90 = straight up, below 0 = not visible)."""
    ha, dec, lat = map(math.radians, (lst_deg - ra_deg, dec_deg, lat_deg))
    return math.degrees(math.asin(math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(ha)))


def window_query(lat_deg: float, lst_deg: float, half_width_deg: float = 60) -> str:
    """ADQL for the patch of sky near this place's meridian (handles RA wrapping past 360)."""
    lo, hi = (lst_deg - half_width_deg) % 360, (lst_deg + half_width_deg) % 360
    ra = f"ra between {lo:.3f} and {hi:.3f}" if lo < hi else f"(ra >= {lo:.3f} or ra <= {hi:.3f})"
    dec = f"dec between {max(-90.0, lat_deg - 60):.3f} and {min(90.0, lat_deg + 60):.3f}"
    return f"select {','.join(COLUMNS)} from pscomppars where {ra} and {dec} and {QUALITY}"


async def overhead(http: httpx.AsyncClient, lat: float, lon: float, when: datetime,
                   simulate_outage: bool = False) -> Overhead:
    lst = sidereal_degrees(when, lon)
    try:
        if simulate_outage:
            raise UpstreamError("nasa", "simulated outage")
        rows = await request_json(http, "nasa", "GET", TAP_URL, timeout=NASA_TIMEOUT_S,
                                  params={"query": window_query(lat, lst), "format": "json"})
        source = "live"
    except UpstreamError:
        rows, source = load_snapshot(), "snapshot"
    planets = [p for p in (_to_planet(r, lat, lst) for r in rows) if p.altitude_deg >= MIN_ALTITUDE_DEG]
    return Overhead(sorted(planets, key=lambda p: p.temp_k), source)


def _to_planet(row: dict, lat: float, lst: float) -> Planet:
    return Planet(
        name=row["pl_name"], star=row["hostname"], temp_k=row["pl_eqt"], radius_earths=row["pl_rade"],
        mass_earths=row.get("pl_bmasse"), year_days=row.get("pl_orbper"),
        light_years=round(row["sy_dist"] * LIGHT_YEARS_PER_PARSEC),
        altitude_deg=round(altitude_degrees(row["ra"], row["dec"], lat, lst), 1),
    )


@lru_cache(maxsize=1)
def load_snapshot() -> list[dict]:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return [dict(zip(data["columns"], values)) for values in data["rows"]]


async def refresh_snapshot() -> int:
    """Download the whole (filtered) catalogue into the bundled snapshot. Run: python -m app.planets"""
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=90) as http:
        rows = await get_json(http, "nasa", TAP_URL,
                              query=f"select {','.join(COLUMNS)} from pscomppars where {QUALITY}", format="json")
    SNAPSHOT.write_text(json.dumps({
        "source": TAP_URL, "fetched": datetime.now(timezone.utc).date().isoformat(), "columns": COLUMNS,
        "rows": [[r[c] for c in COLUMNS] for r in rows],
    }, separators=(",", ":")), encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    print(f"Saved {asyncio.run(refresh_snapshot())} planets to {SNAPSHOT.name}")
