# Runpod docs feedback

Four places where the Public Endpoint docs and the live API behaved differently while I built this game.
All were measured in November 2026 against `black-forest-labs-flux-1-schnell`, through `https://api.runpod.ai/v2/`.

## 1. The image URL is in `output.result`, not `output.image_url`

- **Docs:** the [FLUX.1 Schnell page](https://docs.runpod.io/public-endpoints/models/flux-schnell) shows the
  image URL at `output.image_url`.
- **Live:** completed jobs return it at `output.result`. Code written from the docs gets a `KeyError` even though
  the image was generated (and billed).
- **Suggestion:** update the example response, or return both fields during a transition.
- **In this repo:** [`app/runpod_ai.py`](../app/runpod_ai.py) accepts either.

## 2. `/runsync` can return before the job is finished

- **Docs:** the [operations page](https://docs.runpod.io/serverless/endpoints/operations) says a `/runsync`
  request "stays open until the job finishes or the wait time expires", but not what it returns when the wait
  time expires.
- **Live:** `/runsync` sometimes returned `{"status": "IN_PROGRESS", "id": "…"}`, so the client has to poll
  `/status/{id}`.
- **Suggestion:** document the wait time, the `IN_QUEUE` / `IN_PROGRESS` response and the need to poll, with a
  short example. It's the first thing anyone calling a slow model will hit.
- **In this repo:** `paint()` in [`app/runpod_ai.py`](../app/runpod_ai.py) polls until `COMPLETED` or a deadline.

## 3. `width` and `height` are ignored

- **Docs:** `width` and `height` accept 256–1536 (divisible by 64), default 1024.
- **Live:** every request returned a 1024×1024 image, including 1024×576 and 512×512, and `output.cost`
  was `0.003` each time.
- **Suggestion:** either honour the parameters or note that the endpoint currently returns 1024×1024.
- **In this repo:** [`app/sprites.py`](../app/sprites.py) crops and shrinks the square image instead.

## 4. No guidance on latency

- **Docs:** the model page says it "generates images quickly", with no numbers.
- **Live:** a single image took **15–60 s** end to end (queue 3–7 s, execution 16–24 s, plus polling), and four
  images in parallel took 25–60 s.
- **Suggestion:** publish typical queue and execution times, so developers can design for them, e.g. starting
  generation early and showing progress, as this game does.
