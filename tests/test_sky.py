import pytest

from app import sky
from app.config import CityNotFound, UpstreamError
from tests.conftest import fake_http

TOKYO = {"results": [{"name": "Tokyo", "admin1": "Tokyo", "country": "Japan",
                      "latitude": 35.69, "longitude": 139.69, "population": 9733276}]}


@pytest.mark.anyio
async def test_find_city():
    async with fake_http({sky.GEOCODE_URL: (200, TOKYO)}) as http:
        place = await sky.find_city(http, "Tokyo")
    assert (place.name, place.country, place.lat) == ("Tokyo", "Japan", 35.69)


@pytest.mark.anyio
async def test_unknown_city():
    async with fake_http({sky.GEOCODE_URL: (200, {"generationtime_ms": 0.4})}) as http:
        with pytest.raises(CityNotFound):
            await sky.find_city(http, "Atlantiss")


@pytest.mark.anyio
async def test_read_sky_in_words():
    current = {"current": {"time": "2026-11-06T03:45", "is_day": 0, "temperature_2m": 14.0, "weather_code": 73}}
    place = sky.Place("Tokyo", "Tokyo", "Japan", 35.69, 139.69, None)
    async with fake_http({sky.FORECAST_URL: (200, current)}) as http:
        now = await sky.read_sky(http, place)
    assert (now.is_day, now.condition, now.temp_c) == (False, "snow", 14.0)


@pytest.mark.anyio
async def test_rate_limit_is_reported_with_its_wait():
    async def check():
        async with fake_http({sky.GEOCODE_URL: (429, {"error": True, "reason": "Too many requests"})}) as http:
            await sky.find_city(http, "Tokyo")
    with pytest.raises(UpstreamError) as err:
        await check()
    assert err.value.service == "open-meteo" and err.value.status == 429


@pytest.mark.parametrize("code, words", [(0, "clear"), (2, "partly cloudy"), (45, "fog"), (63, "rain"),
                                         (75, "snow"), (95, "thunderstorm"), (42, "unsettled")])
def test_weather_codes(code, words):
    assert sky.describe_weather(code) == words
