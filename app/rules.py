"""Pure game rules: no network and no randomness, so the same inputs always make the same game."""
from dataclasses import dataclass

from app.planets import Planet

WAVES = 3


class NoPlanetsOverhead(Exception):
    """No catalogued planet is high enough in this sky right now."""


@dataclass(frozen=True)
class Wave:
    number: int
    is_boss: bool
    planet: Planet
    climate: str  # "frozen" … "molten": what the LLM is told, instead of raw kelvin
    hits: int  # shots needed to destroy one alien
    speed: float  # how fast the formation marches (1.0 = normal)
    colour: str  # the sprite's colour, from its climate


def pick_waves(planets: list[Planet]) -> list[Wave]:
    """Coldest, middle and hottest planet overhead. The hottest one is the boss."""
    if not planets:
        raise NoPlanetsOverhead()
    ordered = sorted(planets, key=lambda p: p.temp_k)
    picks = [ordered[0], ordered[len(ordered) // 2], ordered[-1]] if len(ordered) >= WAVES else ordered
    return [make_wave(i + 1, p, is_boss=(i == len(picks) - 1)) for i, p in enumerate(picks)]


def make_wave(number: int, planet: Planet, is_boss: bool) -> Wave:
    feel = climate(planet.temp_k)
    return Wave(number, is_boss, planet, feel, hits_for(planet.radius_earths, is_boss),
                speed_for(planet.year_days), sprite_colour(feel))


def climate(temp_k: float) -> str:
    if temp_k < 150:
        return "frozen"
    if temp_k < 273:
        return "icy"
    if temp_k < 373:
        return "temperate"
    if temp_k < 1000:
        return "hot"
    if temp_k < 2000:
        return "scorching"
    return "molten"


def hits_for(radius_earths: float, is_boss: bool) -> int:
    """Bigger planet, tougher aliens: Earth-sized takes 1 hit, Jupiter-sized or larger takes 4. Bosses take 5x."""
    hits = max(1, min(4, round(radius_earths / 4) + 1))
    return hits * 5 if is_boss else hits


def speed_for(year_days: float | None) -> float:
    """A planet that whips round its star in days sends faster aliens."""
    if year_days is None:
        return 1.0
    if year_days < 2:
        return 1.6
    if year_days < 10:
        return 1.3
    if year_days < 100:
        return 1.0
    return 0.8


SPRITE_COLOURS = {  # cold worlds are blues, hot worlds run yellow → red
    "frozen": "#6FD6FF", "icy": "#B5ECFF", "temperate": "#FFE066",
    "hot": "#FFA23A", "scorching": "#FF6A3D", "molten": "#FF3B3B",
}


def sprite_colour(climate_word: str) -> str:
    return SPRITE_COLOURS[climate_word]


def time_of_day(local_time: str, is_day: bool) -> str:
    """'2026-11-05T21:45' plus Open-Meteo's is_day flag → a phrase for the skyline painting."""
    hour = int(local_time[11:13])
    if not is_day:
        return "night"
    if hour < 9:
        return "early morning"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"
