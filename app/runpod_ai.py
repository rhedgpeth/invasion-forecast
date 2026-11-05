"""Runpod Public Endpoints: an LLM writes, an image model paints. This is the file to copy into your own project.

Both are ready-made models you call over HTTPS with your API key. You pay per call, with no GPU to rent or manage:

    POST https://api.runpod.ai/v2/<endpoint>/...      Authorization: Bearer $RUNPOD_API_KEY

Good to know when calling the image endpoint (more in docs/runpod-docs-feedback.md):
  1. `runsync` can return while the job is still IN_QUEUE / IN_PROGRESS, so poll /status/<id> until it's done.
  2. The image URL comes back in `output.result` (the example response shows `output.image_url`). We read both.
  3. Images take 15-60 s and come back 1024x1024, so start early and resize in code.
"""
import asyncio
import re
import time
from dataclasses import dataclass

import httpx

from app.config import UpstreamError, request_json

BASE_URL = "https://api.runpod.ai/v2"
LLM_ENDPOINT, LLM_MODEL = "qwen3-32b-awq", "Qwen/Qwen3-32B-AWQ"
LLM_USD_PER_TOKEN = 10 / 1_000_000
IMAGE_ENDPOINT = "black-forest-labs-flux-1-schnell"
POLL_SECONDS = 1.5


@dataclass(frozen=True)
class Writing:
    text: str
    seconds: float
    tokens: int
    cost_usd: float


@dataclass(frozen=True)
class Painting:
    png: bytes
    seconds: float
    cost_usd: float


def _auth(api_key: str) -> dict:
    if not api_key:
        raise UpstreamError("runpod", "RUNPOD_API_KEY is not set on the server")
    return {"Authorization": f"Bearer {api_key}"}


async def write(http: httpx.AsyncClient, api_key: str, prompt: str, max_tokens: int = 700) -> Writing:
    """Ask Qwen3 32B for text, using the endpoint's OpenAI-compatible route."""
    started = time.perf_counter()
    reply = await request_json(
        http, "runpod-llm", "POST", f"{BASE_URL}/{LLM_ENDPOINT}/openai/v1/chat/completions",
        headers=_auth(api_key), timeout=90,
        json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt + " /no_think"}],
              "max_tokens": max_tokens, "temperature": 0.6},
    )
    text = reply["choices"][0]["message"]["content"]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # Qwen3 may still emit an empty think block
    tokens = (reply.get("usage") or {}).get("total_tokens", 0)
    return Writing(text, round(time.perf_counter() - started, 1), tokens, round(tokens * LLM_USD_PER_TOKEN, 4))


async def paint(http: httpx.AsyncClient, api_key: str, prompt: str, negative_prompt: str,
                seed: int, deadline_s: float) -> Painting:
    """Ask FLUX.1 Schnell for one image and wait for it, giving up after `deadline_s` seconds."""
    started = time.perf_counter()
    base, headers = f"{BASE_URL}/{IMAGE_ENDPOINT}", _auth(api_key)
    job = await request_json(
        http, "runpod-image", "POST", f"{base}/runsync", headers=headers, timeout=deadline_s,
        json={"input": {"prompt": prompt, "negative_prompt": negative_prompt, "seed": seed,
                        "num_inference_steps": 4, "image_format": "png"}},
    )
    while job.get("status") in ("IN_QUEUE", "IN_PROGRESS"):  # behaviour 1: runsync isn't always synchronous
        if time.perf_counter() - started > deadline_s:
            raise UpstreamError("runpod-image", f"no image within {deadline_s:.0f} s")
        await asyncio.sleep(POLL_SECONDS)
        job = await request_json(http, "runpod-image", "GET", f"{base}/status/{job['id']}", headers=headers)

    output = job.get("output") or {}
    url = output.get("result") or output.get("image_url")  # behaviour 2
    if job.get("status") != "COMPLETED" or not url:
        raise UpstreamError("runpod-image", f"job ended {job.get('status')}: {job.get('error', 'no image')}")
    try:
        image = await http.get(url, timeout=30)
        image.raise_for_status()
    except httpx.HTTPError as e:
        raise UpstreamError("runpod-image", f"couldn't download the image ({type(e).__name__})") from e
    return Painting(image.content, round(time.perf_counter() - started, 1), float(output.get("cost") or 0))
