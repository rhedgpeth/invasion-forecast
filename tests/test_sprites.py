from io import BytesIO

import pytest
from PIL import Image

from app.sprites import BACKGROUND_PX, SPRITE_PX, BadSprite, background, cut_out
from tests.conftest import painting


def test_alien_is_cut_out_of_its_backdrop():
    sprite = Image.open(BytesIO(cut_out(painting())))
    assert sprite.size == (SPRITE_PX, SPRITE_PX)
    assert sprite.getpixel((SPRITE_PX // 2, SPRITE_PX // 2))[3] == 255  # the alien stays
    assert sprite.getpixel((0, 0))[3] == 0  # the backdrop is gone


def test_a_painted_floor_touching_the_edge_is_rejected():
    with pytest.raises(BadSprite):
        cut_out(painting(alien_box=(0, 200, 256, 256)))


def test_an_empty_painting_is_rejected():
    with pytest.raises(BadSprite):
        cut_out(painting(alien_box=(0, 0, 0, 0)))


def test_background_is_a_wide_arcade_sized_band():
    assert Image.open(BytesIO(background(painting()))).size == BACKGROUND_PX
