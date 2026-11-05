"""★ Start here. The whole workflow, top to bottom: a city goes in, a playable invasion comes out.

    1. Sky      Open-Meteo     Where is the city, and what's its sky doing right now?
    2. Planets  NASA           Which planets orbit stars overhead it right now?
    3. Species  Runpod LLM     Invent an alien species for each planet.
    4. Art      Runpod FLUX    Paint the skyline (starts right after step 1) and every alien, in parallel.

Every step reports its progress (the loading screen shows it live) and has a planned fallback.
"""
import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import planets, rules, runpod_ai, sky, sprites
from app.config import Settings, UpstreamError
from app.rules import Wave

SKYLINE_PROMPT = (
    "16-bit pixel art video game backdrop. Side-on silhouette skyline of {city}, {where}, seen from far away: {scale}. "
    "It is {time_of_day}, {condition}, and the sky shows it. The skyline sits only along the bottom quarter; the top "
    "three quarters are open sky. Retro arcade style, limited palette, crisp pixels. No text, no signs, no lettering."
)
ALIEN_PROMPT = (
    "A simple 8-bit arcade game alien icon, drawn as one solid black silhouette on a plain white background: "
    "{look} Wide and squat, chunky square pixels, perfectly symmetrical, seen from the front. Its eyes are "
    "white holes in the silhouette. Small, centred and floating in the air, with plenty of white space around it. "
    "No ground, no floor line, no shadow, no colour, no text."
)
NEGATIVE_PROMPT = "text, letters, words, signs, logo, watermark, user interface, people, border, frame, ground, shadow"
log = logging.getLogger("invasion")


@dataclass
class Step:
    key: str
    label: str
    status: str = "waiting"  # waiting → running → done | fallback | failed
    detail: str = ""
    seconds: float | None = None
    cost_usd: float = 0.0
    requests: list[str] = field(default_factory=list)  # the calls this step made, for "under the hood"
    result: dict = field(default_factory=dict)  # what it found, shown on the topology as soon as it's known


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
                       faults: frozenset = frozenset(), when: datetime | None = None,
                       on_image: Callable[[str, bytes], None] | None = None) -> Game:
    """`on_image(name, png)` is called the moment each image is painted, so the loading screen can show it."""
    progress = progress or Progress()
    when = when or datetime.now(timezone.utc)

    # 1. Sky. No fallback: without a city there's nothing to defend.
    with running(progress.step("sky")) as step:
        step.requests = [f"GET {sky.GEOCODE_URL}?name={city}"]
        if "weather_down" in faults:
            raise UpstreamError("open-meteo", "simulated outage")
        place = await sky.find_city(http, city)
        step.requests.append(f"GET {sky.FORECAST_URL}?latitude={place.lat:.2f}&longitude={place.lon:.2f}"
                             "&current=temperature_2m,weather_code,is_day")
        now = await sky.read_sky(http, place)
        moment = rules.time_of_day(now.local_time, now.is_day)
        step.detail = f"{place.name}: {moment}, {now.condition}, {now.temp_c:.0f}°C"
        step.result = {"city": place.name, "region": place.region, "country": place.country,
                       "local_time": now.local_time, "time_of_day": moment, "condition": now.condition, "temp_c": now.temp_c}

    # The skyline only needs the city and its sky, so it starts painting now, alongside steps 2 and 3.
    art = progress.step("art")
    art.requests = [f"POST {runpod_ai.BASE_URL}/{runpod_ai.IMAGE_ENDPOINT}/runsync  ×{rules.WAVES + 1}, in parallel",
                    f"GET {runpod_ai.BASE_URL}/{runpod_ai.IMAGE_ENDPOINT}/status/<job id>  until COMPLETED"]
    art.result = {"total": rules.WAVES + 1, "painted": []}
    art.status, art.detail = "running", "FLUX: painting the skyline"
    artist = Artist(http, settings, art, on_image, gpu_down="gpu_down" in faults)
    skyline_job = asyncio.create_task(artist.deliver("skyline.png", artist.skyline(place, now)))
    await asyncio.sleep(0)  # let the skyline request go out before we carry on
    try:
        waves, story, source = await plan_waves(http, settings, progress, place, now, when, faults)
        images = await artist.finish(skyline_job, waves, story)
    except BaseException:
        skyline_job.cancel()
        art.status, art.detail = "failed", ""
        raise
    return Game(build_pack(place, now, waves, story, images, progress, source, when), images)


async def plan_waves(http, settings: Settings, progress: Progress, place, now, when, faults):
    """Steps 2 and 3: which planets are invading, and what their species are like."""
    # 2. Planets. Falls back to the bundled catalogue inside planets.overhead().
    with running(progress.step("planets")) as step:
        lst = planets.sidereal_degrees(when, place.lon)
        step.requests = [f"GET {planets.TAP_URL}?format=json&query={planets.window_query(place.lat, lst)}"]
        found = await planets.overhead(http, place.lat, place.lon, when, simulate_outage="nasa_down" in faults)
        waves = rules.pick_waves(found.planets)
        step.result = {"overhead": len(found.planets), "source": found.source, "picks": [
            {"name": w.planet.name, "climate": w.climate, "temp_k": w.planet.temp_k,
             "light_years": w.planet.light_years, "boss": w.is_boss} for w in waves]}
        if found.source == "live":
            step.detail = f"{len(found.planets)} planets overhead (live NASA data)"
        else:
            step.status, step.detail = "fallback", f"NASA unavailable: {len(found.planets)} overhead from the saved catalogue"

    # 3. Species. Falls back to built-in descriptions.
    with running(progress.step("species")) as step:
        step.requests = [f"POST {runpod_ai.BASE_URL}/{runpod_ai.LLM_ENDPOINT}/openai/v1/chat/completions"
                         f'  {{"model": "{runpod_ai.LLM_MODEL}", "messages": [...]}}']
        story, problem, started = None, "", time.perf_counter()
        for attempt in range(2):  # one retry, but only if the first failure came back quickly
            try:
                if "llm_down" in faults:
                    raise UpstreamError("runpod-llm", "simulated outage")
                writing = await runpod_ai.write(http, settings.runpod_api_key, story_prompt(place, now, waves))
                step.cost_usd = round(step.cost_usd + writing.cost_usd, 4)
                story = parse_story(writing.text, len(waves))
                break
            except (UpstreamError, ValueError, KeyError) as e:
                problem = str(e) if isinstance(e, UpstreamError) else f"unusable answer ({type(e).__name__})"
                log.warning("species attempt %d failed: %s", attempt + 1, problem)
                if "llm_down" in faults or time.perf_counter() - started > 20:
                    break
        if story:
            step.detail = "Qwen3 32B: " + ", ".join(a["species"] for a in story["aliens"])
            step.result = {"model": "Qwen3 32B", "species": [a["species"] for a in story["aliens"]]}
        else:
            story = template_story(place, now, waves)
            step.status, step.detail = "fallback", f"LLM unavailable ({problem}): used built-in descriptions"
            step.result = {"model": "built-in", "species": [a["species"] for a in story["aliens"]]}
    return waves, story, found.source


class Artist:
    """Step 4: paints on Runpod FLUX and hands each image to the loading screen the moment it's ready.

    The skyline starts as soon as the city is known; the aliens start once the LLM has described them.
    Anything not painted in time becomes a silhouette, and the game still plays.
    """

    def __init__(self, http, settings: Settings, step: Step, on_image, gpu_down: bool):
        self.http, self.settings, self.step, self.on_image, self.gpu_down = http, settings, step, on_image, gpu_down
        self.started, self.cost = time.perf_counter(), 0.0

    async def paint(self, prompt: str, deadline_at: float) -> bytes:
        if self.gpu_down:
            raise UpstreamError("runpod-image", "simulated outage")
        art = await runpod_ai.paint(self.http, self.settings.runpod_api_key, prompt, NEGATIVE_PROMPT,
                                    random.randrange(2**31), max(1.0, deadline_at - time.perf_counter()))
        self.cost += art.cost_usd  # paid for even if we can't use it
        return art.png

    async def skyline(self, place: sky.Place, now: sky.Sky) -> bytes:
        prompt = SKYLINE_PROMPT.format(city=place.name, where=", ".join(p for p in (place.region, place.country) if p),
                                       scale=city_scale(place.population), condition=now.condition,
                                       time_of_day=rules.time_of_day(now.local_time, now.is_day))
        # It starts early, so it gets the alien deadline plus the time steps 2 and 3 take.
        return sprites.background(await self.paint(prompt, self.started + self.settings.art_deadline_s + 20))

    async def alien(self, wave: Wave, look: str, deadline_at: float) -> bytes:
        prompt = ALIEN_PROMPT.format(look=look)
        for _ in range(2):  # one repaint if the first attempt can't be cut out cleanly
            try:
                return sprites.cut_out(await self.paint(prompt, deadline_at), wave.colour)
            except sprites.BadSprite:
                continue
        raise sprites.BadSprite("rejected twice")

    async def deliver(self, name: str, painting) -> bytes:
        png = await painting
        if self.on_image:
            self.on_image(name, png)  # saved first, so the browser can fetch it as soon as it's listed
        self.step.result["painted"].append(name)
        self.step.cost_usd = round(self.cost, 4)
        self.step.detail = f"FLUX: {len(self.step.result['painted'])} of {self.step.result['total']} painted"
        return png

    async def finish(self, skyline_job: asyncio.Task, waves: list[Wave], story: dict) -> dict[str, bytes]:
        step, total = self.step, len(waves) + 1
        step.result["total"] = total
        deadline_at = time.perf_counter() + self.settings.art_deadline_s
        names = ["skyline.png"] + [f"alien-{w.number}.png" for w in waves]
        results = await asyncio.gather(skyline_job, *(
            self.deliver(f"alien-{w.number}.png", self.alien(w, a["look"], deadline_at))
            for w, a in zip(waves, story["aliens"])), return_exceptions=True)
        images = {name: png for name, png in zip(names, results) if isinstance(png, bytes)}
        for name, outcome in zip(names, results):
            if isinstance(outcome, BaseException):
                log.warning("painting %s failed: %s", name, outcome)
        step.cost_usd, step.seconds = round(self.cost, 4), round(time.perf_counter() - self.started, 1)
        missing = total - len(images)
        step.status = "fallback" if missing else "done"
        step.detail = (f"FLUX: {len(images)} of {total} painted, {missing} shown as silhouettes" if missing
                       else f"FLUX: {total} of {total} painted")
        return images


def city_scale(population: int | None) -> str:
    if population and population >= 1_000_000:
        return "a major city, with its best-known towers and landmarks"
    if population and population >= 100_000:
        return "a mid-sized city, with its best-known buildings and landmarks"
    return "a small town: low rooftops, trees, church spires and perhaps a water tower, no skyscrapers"


def story_prompt(place: sky.Place, now: sky.Sky, waves: list[Wave]) -> str:
    worlds = "\n".join(
        f"{w.number}. {w.planet.name}: a {w.climate} world, {w.planet.temp_k:.0f} K, "
        f"{w.planet.radius_earths:.1f}x Earth's size, {w.planet.light_years} light-years away" for w in waves)
    return (
        "You are the art director of a retro pixel-art arcade game in which aliens invade a real city.\n"
        f"City: {place.name}, {place.region}, {place.country}. Right now it is "
        f"{rules.time_of_day(now.local_time, now.is_day)} there, {now.condition}, {now.temp_c:.0f}°C.\n"
        f"The invaders come from these real planets, in order:\n{worlds}\n"
        'Reply with ONLY this JSON: {"aliens": [{"species": "...", "look": "...", "bio": "..."}]}, '
        "one alien per planet, in the same order.\n"
        "species: an invented name, different for every alien.\n"
        "bio: one playful sentence for the alien's character card, under 25 words, built on its real planet facts "
        "(temperature, size, distance) with a nod to the city's weather right now. Mention only the weather given "
        "above; never invent rain, fog or snow.\n"
        "look: one short sentence describing a simple, iconic silhouette that suits its world and would still read as "
        "a tiny 16x16 arcade icon. It must be wider than it is tall. Name the head shape, number of eyes, antennae, "
        "horns, spikes, legs or tentacles. Shapes only, no colours. No real brands, franchises or characters."
    )


def parse_story(text: str, count: int) -> dict:
    story = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if len(story.get("aliens", [])) < count:
        raise ValueError("the LLM's answer was missing parts")
    story["aliens"] = [{"species": str(a["species"])[:40], "look": str(a["look"])[:300], "bio": str(a.get("bio", ""))[:200]}
                       for a in story["aliens"][:count]]
    return story


TEMPLATE_LOOKS = {
    "frozen": "a diamond-shaped head with two eyes, two short antennae and four stubby legs",
    "icy": "a round dome head with one big eye and three dangling tentacles",
    "temperate": "a wide oval body with three eyes and two curled antennae",
    "hot": "a squat shell with two horns, two eyes and six short legs",
    "scorching": "a spiky crown, two narrow eyes and two big pincer claws",
    "molten": "a broad blocky head with a jagged crest, two eyes and thick stubby arms",
}


def template_story(place: sky.Place, now: sky.Sky, waves: list[Wave]) -> dict:
    return {
        "aliens": [{"species": f"{w.climate.title()} Drifter", "look": TEMPLATE_LOOKS[w.climate],
                    "bio": f"Crossed {w.planet.light_years:,} light-years from {w.planet.name} and is not impressed by "
                           f"{place.name}'s {now.condition} weather."} for w in waves],
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
            "number": w.number, "boss": w.is_boss, "species": a["species"], "look": a["look"], "bio": a["bio"],
            "climate": w.climate, "colour": w.colour, "hits": w.hits, "speed": w.speed,
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
