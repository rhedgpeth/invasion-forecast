"""Command line.

    python -m app Tokyo                           free: the sky and the planets invading Tokyo right now
    python -m app Tokyo --save packs/tokyo-night  a full invasion (about 2 cents of Runpod credit), saved as a pack
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import planets, rules, sky
from app.config import USER_AGENT, Settings
from app.invasion import Progress, run_invasion, save


async def preview(city: str) -> None:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=30) as http:
        place = await sky.find_city(http, city)
        now = await sky.read_sky(http, place)
        found = await planets.overhead(http, place.lat, place.lon, datetime.now(timezone.utc))
    print(f"{place.name}, {place.region or ''} {place.country or ''}".replace("  ", " "))
    print(f"  {now.local_time[11:]} local · {rules.time_of_day(now.local_time, now.is_day)} · "
          f"{now.condition} · {now.temp_c:.0f}°C")
    print(f"  {len(found.planets)} planets overhead ({found.source} NASA data)\n")
    for wave in rules.pick_waves(found.planets):
        p = wave.planet
        label = "BOSS" if wave.is_boss else f"Wave {wave.number}"
        print(f"  {label:7} {p.name:18} {wave.climate:10} {p.temp_k:6.0f} K  {p.radius_earths:5.1f}× Earth  "
              f"{p.light_years:5} ly  {p.altitude_deg:4.0f}° up  → {wave.hits} hits, speed {wave.speed}")


async def make_pack(city: str, folder: Path) -> None:
    progress = Progress()
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=30) as http:
        game = await run_invasion(http, Settings.from_env(), city, progress)
    save(game, folder)
    for s in progress.steps:
        print(f"  {s.label:28} {s.status:9} {s.seconds:5.1f} s  ${s.cost_usd:.4f}  {s.detail}")
    print(f"\nSaved to {folder} (${game.pack['cost_usd']:.4f})")
    for w in game.pack["waves"]:
        print(f"  {'BOSS' if w['boss'] else 'Wave ' + str(w['number']):7} {w['species']:22} from {w['planet']['name']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--save" in args:
        i = args.index("--save")
        asyncio.run(make_pack(" ".join(args[:i]), Path(args[i + 1])))
    else:
        asyncio.run(preview(" ".join(args) or "Tokyo"))
