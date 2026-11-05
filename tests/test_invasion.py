"""The whole workflow against scripted services: the happy path, then one test per failure."""
from datetime import datetime, timezone

import pytest

from app import planets, runpod_ai, sky
from app.config import Settings, UpstreamError
from app.invasion import Progress, run_invasion
from tests.conftest import IMAGE_URL, fake_http, painting, world

WHEN = datetime(2026, 11, 5, 18, 45, tzinfo=timezone.utc)
SETTINGS = Settings(runpod_api_key="test-key")
FLUX = f"{runpod_ai.BASE_URL}/{runpod_ai.IMAGE_ENDPOINT}/"


async def invade(routes=None, faults=frozenset()):
    progress = Progress()
    async with fake_http(world(WHEN, routes)) as http:
        game = await run_invasion(http, SETTINGS, "Tokyo", progress, faults, when=WHEN)
    return game, {s.key: s for s in progress.steps}


@pytest.mark.anyio
async def test_happy_path_makes_a_complete_game():
    game, steps = await invade()
    pack = game.pack
    assert [s.status for s in steps.values()] == ["done"] * 4
    assert [w["species"] for w in pack["waves"]] == ["Frost Mite", "Dune Crab", "Magma Lord"]
    assert [w["boss"] for w in pack["waves"]] == [False, False, True]
    assert pack["skyline"] == "skyline.png" and all(w["sprite"] for w in pack["waves"])
    assert set(game.images) == {"skyline.png", "alien-1.png", "alien-2.png", "alien-3.png"}
    assert pack["sky"]["condition"] == "snow" and pack["nasa_source"] == "live"
    assert pack["cost_usd"] == pytest.approx(0.008 + 4 * 0.003)


@pytest.mark.anyio
async def test_weather_down_stops_the_game():
    with pytest.raises(UpstreamError) as err:
        await invade({sky.GEOCODE_URL: (503, "down")})
    assert err.value.service == "open-meteo"


@pytest.mark.anyio
async def test_nasa_down_uses_the_saved_catalogue():
    game, steps = await invade({planets.TAP_URL: (503, "<html>Service Unavailable</html>")})
    assert steps["planets"].status == "fallback"
    assert game.pack["nasa_source"] == "snapshot" and len(game.pack["waves"]) == 3


@pytest.mark.anyio
async def test_llm_down_uses_built_in_descriptions():
    game, steps = await invade({f"{runpod_ai.BASE_URL}/{runpod_ai.LLM_ENDPOINT}/": (500, {"error": "boom"})})
    assert steps["species"].status == "fallback"
    assert game.pack["waves"][0]["species"] == "Frozen Drifter"
    assert all(w["sprite"] for w in game.pack["waves"])  # the GPU still paints


@pytest.mark.anyio
async def test_llm_nonsense_also_uses_built_in_descriptions():
    nonsense = (200, {"choices": [{"message": {"content": "Sure! Here are some aliens."}}]})
    _, steps = await invade({f"{runpod_ai.BASE_URL}/{runpod_ai.LLM_ENDPOINT}/": nonsense})
    assert steps["species"].status == "fallback"


@pytest.mark.anyio
async def test_gpu_down_still_makes_a_playable_game_with_silhouettes():
    game, steps = await invade({FLUX: (503, {"error": "no workers"})})
    assert steps["art"].status == "fallback"
    assert game.pack["skyline"] is None and not any(w["sprite"] for w in game.pack["waves"])
    assert len(game.pack["waves"]) == 3


@pytest.mark.anyio
async def test_runsync_that_returns_early_is_polled(monkeypatch):
    monkeypatch.setattr(runpod_ai, "POLL_SECONDS", 0)

    def flux(request):
        if request.url.path.endswith("/runsync"):
            return 200, {"id": "job-9", "status": "IN_PROGRESS"}
        return 200, {"id": "job-9", "status": "COMPLETED", "output": {"result": IMAGE_URL, "cost": 0.003}}

    game, steps = await invade({FLUX: flux})
    assert steps["art"].status == "done" and len(game.images) == 4


@pytest.mark.anyio
async def test_a_sprite_that_cannot_be_cut_out_is_repainted_once():
    alien_paintings = []

    def flux(request):
        if b"alien invader" not in request.content:
            return 200, {"status": "COMPLETED", "output": {"result": IMAGE_URL, "cost": 0.003}}
        alien_paintings.append(1)
        url = IMAGE_URL + ("?floor" if len(alien_paintings) == 1 else "")  # the first alien comes with a floor
        return 200, {"status": "COMPLETED", "output": {"result": url, "cost": 0.003}}

    def image(request):
        floor = "floor" in str(request.url)
        return 200, painting(alien_box=(0, 200, 256, 256)) if floor else painting()  # a floor touches the edge

    game, steps = await invade({FLUX: flux, IMAGE_URL: image})
    assert len(alien_paintings) == 4  # 3 aliens + 1 repaint
    assert steps["art"].status == "done" and len(game.images) == 4
    assert game.pack["cost_usd"] == pytest.approx(0.008 + 5 * 0.003)  # the rejected painting is still paid for


@pytest.mark.anyio
async def test_simulated_faults_drive_the_same_fallbacks():
    game, steps = await invade(faults=frozenset({"nasa_down", "llm_down", "gpu_down"}))
    assert [steps[k].status for k in ("planets", "species", "art")] == ["fallback"] * 3
    assert game.pack["cost_usd"] == 0
