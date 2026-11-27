"""S4: upload limits are checked from the bytes, before anything is drawn (design 3.16, R-IMP-1)."""

import io

import pytest
from PIL import Image

from closeout import files

pytestmark = [pytest.mark.slice("S4"), pytest.mark.req("R-IMP-1")]


def png(w: int = 100, h: int = 100) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(b, format="PNG")
    return b.getvalue()


def pdf(pages: int, size: tuple[int, int] = (850, 1100), dpi: int = 100) -> bytes:
    b = io.BytesIO()
    imgs = [Image.new("RGB", size, "white") for _ in range(pages)]
    imgs[0].save(b, format="PDF", save_all=True, append_images=imgs[1:], resolution=dpi)
    return b.getvalue()


def test_good_files() -> None:
    c = files.check(png())
    assert (c.ok, c.mime, c.pages) == (True, "image/png", 1) and len(c.sha256) == 64
    c = files.check(pdf(3))
    assert (c.ok, c.mime, c.pages) == (True, "application/pdf", 3)
    b = io.BytesIO()
    Image.new("RGB", (50, 50)).save(b, format="JPEG")
    assert files.check(b.getvalue()).mime == "image/jpeg"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "empty"),
        (b"GIF89a....", "wrong_type"),
        (b"<html>hi</html>", "wrong_type"),
        (b"\x89PNG\r\n\x1a\n" + b"x" * 50, "unreadable"),
        (b"%PDF-1.4 broken", "unreadable"),
    ],
)
def test_refused_by_type(data: bytes, reason: str) -> None:
    assert (files.check(data).ok, files.check(data).reason) == (False, reason)


def test_too_big_too_many_pages_page_too_big_too_many_pixels() -> None:
    assert files.check(b"\x89PNG\r\n\x1a\n" + b"0" * (files.MAX_BYTES + 1)).reason == "too_big"
    assert files.check(pdf(4)).reason == "too_many_pages"
    assert files.check(pdf(1, size=(850, 2300), dpi=100)).reason == "page_too_big"  # 23 inches tall
    assert files.check(pdf(1, size=(1800, 1800), dpi=100)).reason == "page_too_big"  # 18 x 18
    assert files.check(pdf(1, size=(2200, 1700), dpi=100)).ok  # 22 x 17 landscape is fine
    assert files.check(png(5001, 5001)).reason == "too_many_pixels"
    assert files.check(png(5000, 5000)).ok
