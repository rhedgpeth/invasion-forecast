import pytest

from app.planets import Planet
from app.rules import NoPlanetsOverhead, backdrop_for, climate, hits_for, pick_waves, speed_for, time_of_day


def planet(name, temp_k, radius=1.0, year=None):
    return Planet(name, "star", temp_k, radius, None, year, 100, 45.0)


def test_waves_are_coldest_middle_hottest_and_hottest_is_boss():
    found = [planet("mid", 400), planet("hot", 1500), planet("cold", 60), planet("cool", 300), planet("lava", 3000)]
    waves = pick_waves(found)
    assert [w.planet.name for w in waves] == ["cold", "mid", "lava"]
    assert [w.is_boss for w in waves] == [False, False, True]
    assert [w.number for w in waves] == [1, 2, 3]


def test_fewer_planets_than_waves_still_makes_a_game():
    waves = pick_waves([planet("only", 500)])
    assert len(waves) == 1 and waves[0].is_boss


def test_empty_sky_raises():
    with pytest.raises(NoPlanetsOverhead):
        pick_waves([])


@pytest.mark.parametrize("temp_k, word", [(50, "frozen"), (200, "icy"), (300, "temperate"),
                                          (800, "hot"), (1500, "scorching"), (2600, "molten")])
def test_climate_words(temp_k, word):
    assert climate(temp_k) == word


def test_bigger_planets_make_tougher_aliens_and_bosses_are_much_tougher():
    assert hits_for(1.0, is_boss=False) == 1  # Earth-sized
    assert hits_for(11.2, is_boss=False) == 4  # Jupiter-sized
    assert hits_for(40.0, is_boss=False) == 4  # capped
    assert hits_for(11.2, is_boss=True) == 20


def test_short_years_mean_fast_aliens():
    assert speed_for(1.0) > speed_for(5) > speed_for(50) > speed_for(365)
    assert speed_for(None) == 1.0


def test_backdrop_contrasts_with_the_alien():
    # warm aliens are reds and oranges, so they get green; cold aliens get magenta
    assert backdrop_for("molten") == "bright green"
    assert backdrop_for("frozen") == "bright magenta"


@pytest.mark.parametrize("local_time, is_day, phrase", [
    ("2026-11-05T03:45", False, "night"), ("2026-11-05T07:10", True, "early morning"),
    ("2026-11-05T10:00", True, "morning"), ("2026-11-05T14:30", True, "afternoon"),
    ("2026-11-05T18:00", True, "evening"),
])
def test_time_of_day(local_time, is_day, phrase):
    assert time_of_day(local_time, is_day) == phrase
