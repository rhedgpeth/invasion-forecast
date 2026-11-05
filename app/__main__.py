"""Command line: `python -m app Tokyo` shows a city's sky and the planets invading it right now."""
import asyncio
import sys
from datetime import datetime, timezone

import httpx

from app import planets, rules, sky
from app.config import USER_AGENT


async def main(city: str) -> None:
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


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Tokyo"))
