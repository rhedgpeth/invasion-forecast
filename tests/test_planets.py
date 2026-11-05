from datetime import datetime, timezone

import pytest

from app import planets
from app.planets import altitude_degrees, sidereal_degrees, window_query
from tests.conftest import fake_http


def test_sidereal_time_matches_published_value():
    # Meeus, Astronomical Algorithms, example 12.a: 1987-04-10 00:00 UT → 13h10m46.3668s = 197.693195°
    when = datetime(1987, 4, 10, tzinfo=timezone.utc)
    assert sidereal_degrees(when, 0.0) == pytest.approx(197.693195, abs=0.01)


def test_sidereal_time_shifts_with_longitude():
    when = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    assert (sidereal_degrees(when, 90.0) - sidereal_degrees(when, 0.0)) % 360 == pytest.approx(90.0)


def test_altitude_geometry():
    assert altitude_degrees(ra_deg=0, dec_deg=0, lat_deg=0, lst_deg=0) == pytest.approx(90)  # straight up
    assert altitude_degrees(ra_deg=0, dec_deg=0, lat_deg=0, lst_deg=90) == pytest.approx(0)  # setting
    assert altitude_degrees(ra_deg=123, dec_deg=90, lat_deg=37, lst_deg=0) == pytest.approx(37)  # pole star = latitude


def test_window_query_wraps_past_360():
    assert "(ra >= 310.000 or ra <= 70.000)" in window_query(lat_deg=37, lst_deg=10)
    assert "ra between 140.000 and 260.000" in window_query(lat_deg=37, lst_deg=200)


ROW = {"pl_name": "Test b", "hostname": "Test", "ra": 0.0, "dec": 0.0, "pl_rade": 2.0,
       "pl_bmasse": None, "pl_eqt": 900.0, "pl_orbper": 3.0, "sy_dist": 10.0}


@pytest.mark.anyio
async def test_live_answer_keeps_only_stars_high_enough():
    # At the equator with the meridian at RA 0: a star at RA 0 is straight up, one at RA 80 is 10° up.
    low = ROW | {"pl_name": "Low b", "ra": 80.0}
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lon = (0 - sidereal_degrees(when, 0.0)) % 360  # longitude whose meridian is at RA 0 right now
    async with fake_http({planets.TAP_URL: (200, [ROW, low])}) as http:
        found = await planets.overhead(http, 0.0, lon, when)
    assert found.source == "live"
    assert [p.name for p in found.planets] == ["Test b"]
    assert found.planets[0].light_years == 33


@pytest.mark.anyio
async def test_nasa_outage_falls_back_to_the_bundled_snapshot():
    # This really happened while building the game: every TAP call returned an HTML 503 page.
    async with fake_http({planets.TAP_URL: (503, "<html>Service Unavailable</html>")}) as http:
        found = await planets.overhead(http, 37.0, -93.3, datetime(2026, 11, 5, 18, tzinfo=timezone.utc))
    assert found.source == "snapshot"
    assert len(found.planets) > 100
    assert all(p.altitude_deg >= planets.MIN_ALTITUDE_DEG for p in found.planets)
