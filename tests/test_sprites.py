from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.sprites import BACKGROUND_PX, GRID, BadSprite, background, cut_out
from tests.conftest import painting

ORANGE = "#FFA23A"


def sprite_of(png: bytes) -> Image.Image:
    return Image.open(BytesIO(cut_out(png, ORANGE))).convert("RGBA")


def test_stencil_becomes_a_coloured_16x16_sprite_with_eye_holes():
    sprite = sprite_of(painting())
    assert sprite.size == (GRID, GRID)
    assert sprite.getpixel((GRID // 2, GRID - 3)) == (255, 162, 58, 255)  # body, in the climate colour
    assert sprite.getpixel((0, 0))[3] == 0  # outside the alien is transparent
    assert any(sprite.getpixel((x, y))[3] == 0 for x in range(4, 8) for y in range(4, 8))  # an eye hole


def test_sprites_are_symmetric():
    img = Image.open(BytesIO(painting(eyes=False))).convert("RGB")
    ImageDraw.Draw(img).rectangle((60, 60, 90, 90), fill=(0, 0, 0))  # a lump on one side only
    buf = BytesIO()
    img.save(buf, "PNG")
    sprite = sprite_of(buf.getvalue())
    for y in range(GRID):
        for x in range(GRID):
            assert sprite.getpixel((x, y))[3] == sprite.getpixel((GRID - 1 - x, y))[3]


def test_a_ground_line_under_the_alien_is_dropped():
    img = Image.open(BytesIO(painting())).convert("RGB")
    ImageDraw.Draw(img).rectangle((40, 176, 216, 186), fill=(0, 0, 0))  # wide floor under the feet
    buf = BytesIO()
    img.save(buf, "PNG")
    sprite = sprite_of(buf.getvalue())
    bottom = sum(1 for x in range(GRID) if sprite.getpixel((x, GRID - 1))[3])
    assert bottom < GRID * 0.75


def test_a_shape_running_off_the_edge_is_rejected():
    with pytest.raises(BadSprite):
        cut_out(painting(alien_box=(0, 200, 256, 300)), ORANGE)


def test_an_empty_painting_is_rejected():
    with pytest.raises(BadSprite):
        cut_out(painting(alien_box=(0, 0, 0, 0), eyes=False), ORANGE)


def test_background_is_a_wide_arcade_sized_band():
    assert Image.open(BytesIO(background(painting()))).size == BACKGROUND_PX
