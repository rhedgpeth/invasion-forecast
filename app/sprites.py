"""Turn FLUX paintings into game art: cut the alien out of its flat backdrop, shrink both to arcade size."""
from collections import deque
from io import BytesIO

from PIL import Image

SPRITE_PX = 64
BACKGROUND_PX = (320, 200)  # the game scales this up with crisp, square pixels
WORK_PX = 256  # cut-outs are done at this size: fast, and plenty for a 64 px sprite
TOLERANCE = 70  # how far (sum of RGB differences) a pixel can be from the backdrop and still count as backdrop


class BadSprite(Exception):
    """The painting can't be used as a sprite: it touches the edges (e.g. a painted floor) or is empty."""


def cut_out(png: bytes) -> bytes:
    img = Image.open(BytesIO(png)).convert("RGB").resize((WORK_PX, WORK_PX), Image.NEAREST)
    backdrop = _backdrop_colour(img)
    sprite = img.convert("RGBA")
    src, out = img.load(), sprite.load()

    # 1. Flood-fill the backdrop inwards from the edges (also takes its darker shades: cast shadows).
    seen, queue = set(), deque((x, y) for x in range(WORK_PX) for y in (0, WORK_PX - 1))
    queue.extend((x, y) for y in range(WORK_PX) for x in (0, WORK_PX - 1))
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen or not (0 <= x < WORK_PX and 0 <= y < WORK_PX):
            continue
        seen.add((x, y))
        if _is_backdrop(src[x, y], backdrop):
            out[x, y] = (0, 0, 0, 0)
            queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # 2. Green-screen rule for everything the fill missed: pockets between legs, shadows, coloured fringes.
    #    Safe because the backdrop is chosen to contrast with the alien (rules.backdrop_for)
    #    and the LLM is told never to use green or magenta.
    remove_screen_colour(sprite, "green" if backdrop[1] > max(backdrop[0], backdrop[2]) else "magenta")

    box = sprite.getbbox()
    if box is None or _coverage(sprite) < 0.03:
        raise BadSprite("nothing left after removing the backdrop")
    if box[0] < 2 or box[1] < 2 or box[2] > WORK_PX - 2 or box[3] > WORK_PX - 2:
        raise BadSprite("the alien touches the edge of the painting")

    # 3. Crop, centre on a square canvas, shrink.
    alien = sprite.crop(box)
    side = max(alien.size)
    square = Image.new("RGBA", (side, side))
    square.paste(alien, ((side - alien.width) // 2, side - alien.height))
    return _png(square.resize((SPRITE_PX, SPRITE_PX), Image.NEAREST))


def background(png: bytes) -> bytes:
    """FLUX paints a square. Keep a wide band with the skyline at the bottom, then shrink to arcade size."""
    img = Image.open(BytesIO(png)).convert("RGB")
    w, h = img.size
    band = round(w * BACKGROUND_PX[1] / BACKGROUND_PX[0])
    top = max(0, h - band - h // 16)  # skyline sits in the bottom quarter; keep a little ground under it
    return _png(img.crop((0, top, w, top + band)).resize(BACKGROUND_PX, Image.LANCZOS))


def _backdrop_colour(img: Image.Image) -> tuple:
    px = img.load()
    n = img.width - 3
    corners = [px[2, 2], px[n, 2], px[2, n], px[n, n]]
    return tuple(sorted(c[i] for c in corners)[1] for i in range(3))  # per-channel, ignores one odd corner


def _is_backdrop(c: tuple, backdrop: tuple) -> bool:
    if _distance(c, backdrop) < TOLERANCE:
        return True
    ratios = [a / max(1, b) for a, b in zip(c, backdrop)]
    return 0.35 < max(ratios) < 1.05 and max(ratios) - min(ratios) < 0.35  # a darker shade of it: a shadow


def remove_screen_colour(sprite: Image.Image, screen: str) -> None:
    """Clear every pixel where the screen colour dominates, at any brightness (so shadows go too)."""
    px = sprite.load()
    for y in range(sprite.height):
        for x in range(sprite.width):
            r, g, b, a = px[x, y]
            if not a:
                continue
            if screen == "green" and g > r + 25 and g > b + 15:
                px[x, y] = (0, 0, 0, 0)
            elif screen == "magenta" and r > g + 60 and b > g + 40:
                px[x, y] = (0, 0, 0, 0)


def _distance(c: tuple, backdrop: tuple) -> int:
    return sum(abs(a - b) for a, b in zip(c, backdrop))


def _coverage(img: Image.Image) -> float:
    transparent = img.getchannel("A").histogram()[0]
    return 1 - transparent / (img.width * img.height)


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
