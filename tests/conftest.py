"""Test helpers: a scripted stand-in for every external service, so tests need no network and no credits."""
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image, ImageDraw

from app import planets, runpod_ai, sky

IMAGE_URL = "https://images.test/painting.png"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def fake_http(routes: dict) -> httpx.AsyncClient:
    """Responses are scripted per URL prefix. A value is (status, body) or a function(request) → (status, body).

    body: dict/list → JSON, str → text, bytes → raw content.
    """
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, reply in routes.items():
            if url.startswith(prefix):
                status, body = reply(request) if callable(reply) else reply
                if isinstance(body, bytes):
                    return httpx.Response(status, content=body)
                if isinstance(body, str):
                    return httpx.Response(status, text=body)
                return httpx.Response(status, content=json.dumps(body), headers={"content-type": "application/json"})
        raise AssertionError(f"unexpected request: {url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def painting(alien_box=(80, 80, 176, 176), eyes: bool = True) -> bytes:
    """A fake FLUX stencil: a black oval 'alien' on white, with two white eye holes."""
    img = Image.new("RGB", (256, 256), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse(alien_box, fill=(0, 0, 0))
    if eyes:
        draw.rectangle((104, 112, 116, 124), fill=(255, 255, 255))
        draw.rectangle((140, 112, 152, 124), fill=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def world(when, overrides: dict | None = None) -> dict:
    """Routes for a healthy world: Tokyo, a snowy night, three planets straight overhead, working Runpod models."""
    lat, lon = 35.69, 139.69
    lst = planets.sidereal_degrees(when, lon)
    rows = [{"pl_name": name, "hostname": name[:-2], "ra": lst, "dec": lat, "pl_rade": radius, "pl_bmasse": None,
             "pl_eqt": temp, "pl_orbper": year, "sy_dist": 100.0}
            for name, temp, radius, year in [("Cold b", 80, 2.0, 400), ("Warm b", 500, 4.0, 20), ("Lava b", 2500, 12.0, 1.2)]]
    story = {"skyline": "towers and a lattice tower", "aliens": [
        {"species": "Frost Mite", "look": "a pale blue blob"}, {"species": "Dune Crab", "look": "an orange crab"},
        {"species": "Magma Lord", "look": "a glowing lava giant"}]}
    routes = {
        sky.GEOCODE_URL: (200, {"results": [{"name": "Tokyo", "admin1": "Tokyo", "country": "Japan",
                                             "latitude": lat, "longitude": lon, "population": 9733276}]}),
        sky.FORECAST_URL: (200, {"current": {"time": "2026-11-06T03:45", "is_day": 0, "temperature_2m": -1.0,
                                             "weather_code": 73}}),
        planets.TAP_URL: (200, rows),
        f"{runpod_ai.BASE_URL}/{runpod_ai.LLM_ENDPOINT}/": (200, {
            "choices": [{"message": {"content": "<think></think>" + json.dumps(story)}}], "usage": {"total_tokens": 800}}),
        f"{runpod_ai.BASE_URL}/{runpod_ai.IMAGE_ENDPOINT}/": (200, {
            "id": "job-1", "status": "COMPLETED", "output": {"result": IMAGE_URL, "cost": 0.003}}),
        IMAGE_URL: (200, painting()),
    }
    return routes | (overrides or {})
