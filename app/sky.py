"""API 1: Open-Meteo (free, no key). Where is the city, and what does its sky look like right now?"""
from dataclasses import dataclass

import httpx

from app.config import CityNotFound, get_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class Place:
    name: str
    region: str | None
    country: str | None
    lat: float
    lon: float
    population: int | None


@dataclass(frozen=True)
class Sky:
    local_time: str  # the city's own clock, e.g. "2026-11-05T21:45"
    is_day: bool
    temp_c: float
    condition: str  # plain words, e.g. "light snow"


async def find_city(http: httpx.AsyncClient, name: str) -> Place:
    data = await get_json(http, "open-meteo", GEOCODE_URL, name=name, count=1)
    if not data.get("results"):
        raise CityNotFound(name)
    r = data["results"][0]
    return Place(r["name"], r.get("admin1"), r.get("country"), r["latitude"], r["longitude"], r.get("population"))


async def read_sky(http: httpx.AsyncClient, place: Place) -> Sky:
    data = await get_json(http, "open-meteo", FORECAST_URL, latitude=place.lat, longitude=place.lon,
                          timezone="auto", current="temperature_2m,weather_code,is_day")
    now = data["current"]
    return Sky(now["time"], bool(now["is_day"]), now["temperature_2m"], describe_weather(now["weather_code"]))


def describe_weather(code: int) -> str:
    """Open-Meteo reports weather as WMO codes; turn them into words a person (and an LLM) understands."""
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly cloudy"
    if code == 3:
        return "overcast"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 61 <= code <= 67:
        return "rain"
    if 71 <= code <= 77:
        return "snow"
    if 80 <= code <= 82:
        return "rain showers"
    if code in (85, 86):
        return "snow showers"
    if 95 <= code <= 99:
        return "thunderstorm"
    return "unsettled"
