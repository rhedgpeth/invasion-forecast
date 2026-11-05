# Suggested Runpod docs improvements

Four small things I hit in my first hour with the FLUX.1 Schnell Public Endpoint (`black-forest-labs-flux-1-schnell`,
November 2026), how the game handles each, and a doc change that would save the next developer the detour.

## 1. Where the image URL comes back

- **What I hit:** my first call raised a `KeyError`, although the image was generated. Completed jobs return the URL in
  `output.result`; the [model page](https://docs.runpod.io/public-endpoints/models/flux-schnell)'s example response
  shows `output.image_url`.
- **How the game handles it:** [`app/runpod_ai.py`](../app/runpod_ai.py) reads either field.
- **Suggested doc change:** update the example response (or note both fields during a transition).

## 2. `/runsync` can return before the job finishes

- **What I hit:** `/runsync` sometimes returned `{"status": "IN_PROGRESS", "id": "…"}`. The
  [operations page](https://docs.runpod.io/serverless/endpoints/operations) explains that a request "stays open until
  the job finishes or the wait time expires"; what comes back in the second case isn't shown.
- **How the game handles it:** `paint()` polls `/status/{id}` until `COMPLETED`, within a deadline.
- **Suggested doc change:** a short example of the `IN_PROGRESS` response and the polling loop. Anyone calling a slow
  model will meet it.

## 3. Image size

- **What I hit:** every image came back 1024×1024, including requests for 1024×576 and 512×512 (the docs list
  256–1536 for `width` and `height`).
- **How the game handles it:** [`app/sprites.py`](../app/sprites.py) crops and shrinks the square image.
- **Suggested doc change:** note the sizes the endpoint currently returns.

## 4. How long an image takes

- **What I hit:** one image took 15–60 s end to end (queue 3–7 s, generation 16–24 s, plus polling), and four in
  parallel took 25–60 s.
- **How the game handles it:** the skyline starts painting as soon as the city is known, all images paint in parallel,
  and the loading screen shows progress live.
- **Suggested doc change:** typical queue and generation times, so developers can design for the wait.
