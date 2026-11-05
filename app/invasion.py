"""★ Start here. The whole workflow, top to bottom: a city goes in, a playable invasion comes out.

    1. Sky      Open-Meteo     Where is the city, and what's its sky doing right now?
    2. Planets  NASA           Which planets orbit stars overhead it right now?
    3. Species  Runpod LLM     Invent an alien species for each planet, and describe the skyline.
    4. Art      Runpod FLUX    Paint the skyline and every alien, in parallel.

Every step reports its progress (the loading screen shows it live) and has a planned fallback.
"""
import asyncio
import json
import random
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import planets, rules, runpod_ai, sky, sprites
from app.config import Settings, UpstreamError
from app.rules import Wave

SKYLINE_PROMPT = (
    "16-bit pixel art video game backdrop. Side-on silhouette skyline of {city}, seen from far away: {skyline} "
    "It is {time_of_day}, {condition}. The skyline sits only along the bottom quarter; the top three quarters are "
    "open sky. Retro arcade style, limited palette, crisp pixels. No text, no signs, no lettering anywhere."
)
ALIEN_PROMPT = (
    "16-bit pixel art sprite of a single compact alien invader for a retro arcade shooter: {look} "
    "Front view, symmetrical, bold silhouette, whole body visible with empty space around it, "
    "on a flat solid {backdrop} background. No ground, no shadow, no particles, no glow, no text."
)
NEGATIVE_PROMPT = "text, letters, words, signs, logo, watermark, user interface, people, border, frame, ground, shadow"


@dataclass
class Step:
    key: str
    label: str
    status: str = "waiting"  # waiting → running → done | fallback | failed
    detail: str = ""
    seconds: float | None = None
    cost_usd: float = 0.0


@dataclass
class Progress:
    steps: list[Step] = field(default_factory=lambda: [
        Step("sky", "Read the sky"), Step("planets", "Find the planets overhead"),
        Step("species", "Imagine the species"), Step("art", "Paint the art"),
    ])

    def step(self, key: str) -> Step:
        return next(s for s in self.steps if s.key == key)


@dataclass
class Game:
    pack: dict  # everything the browser needs, saved as pack.json
    images: dict[str, bytes]  # file name → PNG


async def run_invasion(http: httpx.AsyncClient, settings: Settings, city: str, progress: Progress | None = None,
                       faults: frozenset = frozenset(), when: datetime | None = None) -> Game:
    progress = progress or Progress()
    when = when or datetime.now(timezone.utc)

    # 1. Sky. No fallback: without a city there's nothing to defend.
    with running(progress.step("sky")) as step:
        if "weather_down" in faults:
            raise UpstreamError("open-meteo", "simulated outage")
        place = await sky.find_city(http, city)
        now = await sky.read_sky(http, place)
        step.detail = f"{place.name}: {rules.time_of_day(now.local_time, now.is_day)}, {now.condition}, {now.temp_c:.0f}°C"

    # 2. Planets. Falls back to the bundled catalogue inside planets.overhead().
    with running(progress.step("planets")) as step:
        found = await planets.overhead(http, place.lat, place.lon, when, simulate_outage="nasa_down" in faults)
        waves = rules.pick_waves(found.planets)
        if found.source == "live":
            step.detail = f"{len(found.planets)} planets overhead (live NASA data)"
        else:
            step.status, step.detail = "fallback", f"NASA unavailable: {len(found.planets)} overhead from the saved catalogue"

    # 3. Species. Falls back to built-in descriptions.
    with running(progress.step("species")) as step:
        try:
            if "llm_down" in faults:
                raise UpstreamError("runpod-llm", "simulated outage")
            writing = await runpod_ai.write(http, settings.runpod_api_key, story_prompt(place, now, waves))
            story = parse_story(writing.text, len(waves))
            step.cost_usd = writing.cost_usd
            step.detail = "Qwen3 32B: " + ", ".join(a["species"] for a in story["aliens"])
        except (UpstreamError, ValueError, KeyError):
            story = template_story(place, now, waves)
            step.status, step.detail = "fallback", "LLM unavailable: used built-in descriptions"

    # 4. Art. Anything that isn't painted in time is drawn as a silhouette instead.
    with running(progress.step("art")) as step:
        images, cost = await paint_all(http, settings, place, now, waves, story, step, gpu_down="gpu_down" in faults)
        step.cost_usd = cost
        missing = len(waves) + 1 - len(images)
        if missing:
            step.status = "fallback"
            step.detail = f"FLUX: {len(images)} of {len(waves) + 1} painted, {missing} shown as silhouettes"

    return Game(build_pack(place, now, waves, story, images, progress, found.source, when), images)


async def paint_all(http, settings, place, now, waves: list[Wave], story: dict, step: Step,
                    gpu_down: bool = False) -> tuple[dict[str, bytes], float]:
    """Paint the skyline and every alien at the same time, all under one deadline."""
    if gpu_down:
        return {}, 0.0
    deadline = time.perf_counter() + settings.art_deadline_s
    key, total = settings.runpod_api_key, len(waves) + 1
    painted, cost = 0, 0.0

    def time_left() -> float:
        return max(1.0, deadline - time.perf_counter())

    def tick(spent: float) -> None:
        nonlocal painted, cost
        painted, cost = painted + 1, cost + spent
        step.detail = f"FLUX: {painted} of {total} painted"

    async def skyline() -> bytes:
        prompt = SKYLINE_PROMPT.format(city=place.name, skyline=story["skyline"], condition=now.condition,
                                       time_of_day=rules.time_of_day(now.local_time, now.is_day))
        art = await runpod_ai.paint(http, key, prompt, NEGATIVE_PROMPT, random.randrange(2**31), time_left())
        tick(art.cost_usd)
        return sprites.background(art.png)

    async def alien(wave: Wave, look: str) -> bytes:
        nonlocal cost
        prompt = ALIEN_PROMPT.format(look=look, backdrop=wave.backdrop)
        for _ in range(2):  # one repaint if the first attempt can't be cut out cleanly
            art = await runpod_ai.paint(http, key, prompt, NEGATIVE_PROMPT, random.randrange(2**31), time_left())
            try:
                sprite = sprites.cut_out(art.png)
            except sprites.BadSprite:
                cost += art.cost_usd  # a rejected painting still costs money
                continue
            tick(art.cost_usd)
            return sprite
        raise sprites.BadSprite("rejected twice")

    step.detail = f"FLUX: 0 of {total} painted"
    jobs = {"skyline.png": skyline()} | {
        f"alien-{w.number}.png": alien(w, a["look"]) for w, a in zip(waves, story["aliens"])}
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    return {name: png for name, png in zip(jobs, results) if isinstance(png, bytes)}, round(cost, 4)


def story_prompt(place: sky.Place, now: sky.Sky, waves: list[Wave]) -> str:
    worlds = "\n".join(
        f"{w.number}. {w.planet.name}: a {w.climate} world, {w.planet.temp_k:.0f} K, "
        f"{w.planet.radius_earths:.1f}x Earth's size, {w.planet.light_years} light-years away" for w in waves)
    return (
        "You are the art director of a retro pixel-art arcade game in which aliens invade a real city.\n"
        f"City: {place.name}, {place.region}, {place.country}. Right now it is "
        f"{rules.time_of_day(now.local_time, now.is_day)} there, {now.condition}, {now.temp_c:.0f}°C.\n"
        f"The invaders come from these real planets, in order:\n{worlds}\n"
        'Reply with ONLY this JSON: {"skyline": "...", "aliens": [{"species": "...", "look": "..."}]}, '
        "one alien per planet, in the same order.\n"
        "skyline: one sentence describing the shapes of this city's skyline seen side-on from far away, at this time "
        "of day and in this weather. Buildings and landmarks only; no signs, no lettering.\n"
        "species: an invented two-word name.\n"
        "look: one sentence of visual traits for a compact creature that suits its world (colours, body shape, eyes, "
        "limbs). Use bright colours, but never green or magenta. No real brands, franchises or characters."
    )


def parse_story(text: str, count: int) -> dict:
    story = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if not isinstance(story.get("skyline"), str) or len(story.get("aliens", [])) < count:
        raise ValueError("the LLM's answer was missing parts")
    story["aliens"] = [{"species": str(a["species"])[:40], "look": str(a["look"])[:300]} for a in story["aliens"][:count]]
    return story


TEMPLATE_LOOKS = {
    "frozen": "a pale blue crystalline body, frost-white eyes and stubby icicle legs",
    "icy": "a round white furry body, big dark eyes and short clawed limbs",
    "temperate": "a yellow rounded body, three curious eyes and four short legs",
    "hot": "an orange armoured shell, glowing amber eyes and six short legs",
    "scorching": "a red spiky body, smouldering yellow eyes and crab-like claws",
    "molten": "a glowing lava-orange body, white-hot eyes and stubby rock limbs",
}


def template_story(place: sky.Place, now: sky.Sky, waves: list[Wave]) -> dict:
    return {
        "skyline": f"the skyline of {place.name}, {place.country}",
        "aliens": [{"species": f"{w.climate.title()} Drifter", "look": TEMPLATE_LOOKS[w.climate]} for w in waves],
    }


def build_pack(place, now, waves: list[Wave], story: dict, images: dict, progress: Progress,
               nasa_source: str, when: datetime) -> dict:
    return {
        "version": 1,
        "created": when.isoformat(timespec="seconds"),
        "city": {"name": place.name, "region": place.region, "country": place.country, "lat": place.lat, "lon": place.lon},
        "sky": {"local_time": now.local_time, "time_of_day": rules.time_of_day(now.local_time, now.is_day),
                "condition": now.condition, "temp_c": now.temp_c, "is_day": now.is_day},
        "skyline": "skyline.png" if "skyline.png" in images else None,
        "waves": [{
            "number": w.number, "boss": w.is_boss, "species": a["species"], "look": a["look"],
            "climate": w.climate, "hits": w.hits, "speed": w.speed,
            "sprite": f"alien-{w.number}.png" if f"alien-{w.number}.png" in images else None,
            "planet": {"name": w.planet.name, "star": w.planet.star, "temp_k": w.planet.temp_k,
                       "radius_earths": w.planet.radius_earths, "year_days": w.planet.year_days,
                       "light_years": w.planet.light_years, "altitude_deg": w.planet.altitude_deg},
        } for w, a in zip(waves, story["aliens"])],
        "nasa_source": nasa_source,
        "steps": [asdict(s) for s in progress.steps],
        "cost_usd": round(sum(s.cost_usd for s in progress.steps), 4),
    }


def save(game: Game, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, png in game.images.items():
        (folder / name).write_bytes(png)
    (folder / "pack.json").write_text(json.dumps(game.pack, indent=2), encoding="utf-8")


@contextmanager
def running(step: Step):
    step.status = "running"
    started = time.perf_counter()
    try:
        yield step
        if step.status == "running":
            step.status = "done"
    except Exception:
        step.status = "failed"
        raise
    finally:
        step.seconds = round(time.perf_counter() - started, 1)
