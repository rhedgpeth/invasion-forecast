"""Turn FLUX paintings into game art.

Aliens: FLUX paints each alien as a black stencil on white, with its eyes as holes. That makes the cut-out
trivial (dark = alien) and gives the classic arcade look: one flat colour, gaps for eyes. Code then shrinks it
to a 16x16 grid, makes it symmetric, and colours it by its planet's climate.

Skyline: FLUX paints a square scene; we keep a wide band with the skyline along the bottom.
"""
from io import BytesIO

from PIL import Image

GRID = 16  # a sprite is 16x16 big, square pixels; the game draws it scaled up
BACKGROUND_PX = (320, 200)  # the game scales this up with crisp, square pixels
WORK_PX = 256
DARK = 110  # 0-255 brightness below which a pixel counts as stencil
MIN_COVERAGE = 0.03


class BadSprite(Exception):
    """The painting can't be used as a sprite: nothing on it, or the shape runs off the edge (e.g. a floor)."""


def cut_out(png: bytes, colour: str) -> bytes:
    """Black-on-white painting → 16x16 symmetric sprite in `colour` (see rules.sprite_colour)."""
    grey = Image.open(BytesIO(png)).convert("L").resize((WORK_PX, WORK_PX), Image.LANCZOS)
    stencil = grey.point(lambda v: 255 if v < DARK else 0)

    box = stencil.getbbox()
    if box is None or 1 - stencil.histogram()[0] / WORK_PX**2 < MIN_COVERAGE:
        raise BadSprite("nothing on the painting")
    if box[0] < 2 or box[1] < 2 or box[2] > WORK_PX - 2 or box[3] > WORK_PX - 2:
        raise BadSprite("the shape runs off the edge of the painting")

    # Centre the shape on a square, shrink it to the grid, keep cells that are at least half stencil.
    shape = stencil.crop(box)
    side = max(shape.size)
    square = Image.new("L", (side, side))
    square.paste(shape, ((side - shape.width) // 2, (side - shape.height) // 2))
    cells = square.resize((GRID, GRID), Image.BOX).load()

    # Classic arcade sprites are symmetric: a cell is filled if it or its mirror image is.
    filled = [[cells[x, y] >= 128 or cells[GRID - 1 - x, y] >= 128 for x in range(GRID)] for y in range(GRID)]
    # FLUX sometimes stands the alien on a ground line: drop solid bars along the bottom.
    while filled and sum(filled[-1]) >= GRID * 0.75:
        filled.pop()
    if not any(map(any, filled)):
        raise BadSprite("nothing left once the ground line is removed")

    sprite = Image.new("RGBA", (GRID, GRID))
    px, rgba, top = sprite.load(), _rgba(colour), GRID - len(filled)  # keep it sitting on the bottom edge
    for y, row in enumerate(filled):
        for x, on in enumerate(row):
            if on:
                px[x, y + top] = rgba
    return _png(sprite)


def background(png: bytes) -> bytes:
    """Keep a wide band of the square painting with the skyline at the bottom, shrunk to arcade size."""
    img = Image.open(BytesIO(png)).convert("RGB")
    w, h = img.size
    band = round(w * BACKGROUND_PX[1] / BACKGROUND_PX[0])
    top = max(0, h - band - h // 16)  # skyline sits in the bottom quarter; keep a little ground under it
    return _png(img.crop((0, top, w, top + band)).resize(BACKGROUND_PX, Image.LANCZOS))


def _rgba(hex_colour: str) -> tuple:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
