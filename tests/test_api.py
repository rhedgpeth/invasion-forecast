"""The HTTP layer: validation, the job lifecycle, limits and error codes."""
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import sky
from app.config import Settings
from app.main import SECONDS_BETWEEN_GAMES, Limits, create_app
from tests.conftest import fake_http, world


@pytest.fixture
def client(tmp_path):
    def make(demo_faults=False, routes=None):
        routes = world(datetime.now(timezone.utc), routes)
        app = create_app(Settings(runpod_api_key="test", data_dir=tmp_path, demo_faults=demo_faults), fake_http(routes))
        return TestClient(app)
    return make


def wait_for(c: TestClient, job_id: str) -> dict:
    for _ in range(100):
        body = c.get(f"/api/invasions/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("invasion never finished")


def test_bad_city_names_are_rejected(client):
    with client() as c:
        for bad in ["", "x", "1234", "<script>", "a" * 81]:
            r = c.post("/api/invasions", json={"city": bad})
            assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_city"


def test_an_invasion_runs_and_its_pack_is_served(client):
    with client() as c:
        r = c.post("/api/invasions", json={"city": "Tokyo"})
        assert r.status_code == 202
        done = wait_for(c, r.json()["id"])
        assert done["status"] == "ready" and [s["status"] for s in done["steps"]] == ["done"] * 4
        pack = c.get(done["pack"]).json()
        assert len(pack["waves"]) == 3
        assert c.get(done["pack"].replace("pack.json", pack["waves"][0]["sprite"])).status_code == 200


def test_unknown_city_fails_with_a_friendly_message(client):
    with client(routes={sky.GEOCODE_URL: (200, {"generationtime_ms": 0.3})}) as c:
        done = wait_for(c, c.post("/api/invasions", json={"city": "Atlantiss"}).json()["id"])
        assert done["status"] == "failed" and done["error"]["code"] == "city_not_found"


def test_weather_outage_fails_with_a_retry_message(client):
    with client(routes={sky.GEOCODE_URL: (503, "down")}) as c:
        done = wait_for(c, c.post("/api/invasions", json={"city": "Tokyo"}).json()["id"])
        assert done["error"]["code"] == "weather_down"


def test_players_are_rate_limited_by_their_real_address(client):
    with client() as c:
        alice = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
        assert c.post("/api/invasions", json={"city": "Tokyo"}, headers=alice).status_code == 202
        again = c.post("/api/invasions", json={"city": "Tokyo"}, headers=alice)
        assert again.status_code == 429 and again.json()["error"]["code"] == "too_fast"
        assert 0 < int(again.headers["Retry-After"]) <= SECONDS_BETWEEN_GAMES
        bob = {"X-Forwarded-For": "198.51.100.4"}
        assert c.post("/api/invasions", json={"city": "Tokyo"}, headers=bob).status_code == 202


def test_daily_cap_protects_the_credit():
    limits = Limits(per_day=2)
    assert limits.check("a", now=1000) is None
    assert limits.check("b", now=1001) is None
    code, wait = limits.check("c", now=1002)
    assert code == "daily_limit" and wait > 0


def test_fault_switch_only_works_when_enabled(client):
    with client(demo_faults=False) as c:
        done = wait_for(c, c.post("/api/invasions?simulate=gpu_down", json={"city": "Tokyo"}).json()["id"])
        assert done["steps"][3]["status"] == "done"
    with client(demo_faults=True) as c:
        done = wait_for(c, c.post("/api/invasions?simulate=gpu_down", json={"city": "Tokyo"}).json()["id"])
        assert done["steps"][3]["status"] == "fallback"


def test_unknown_invasion_is_404(client):
    with client() as c:
        assert c.get("/api/invasions/nope").status_code == 404
