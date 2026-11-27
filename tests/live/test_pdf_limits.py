"""S3 proof on the owner's Mac (live): a 3-page PDF read fits the model's limits (design 3.10)."""

import io
from pathlib import Path

import pytest
from PIL import Image

from closeout.gateway import core
from closeout.gateway.core import Gateway
from closeout.settings import Settings

pytestmark = [pytest.mark.live, pytest.mark.slice("S3")]
REG = Path(__file__).parents[2] / "evals" / "sets" / "regression"


def three_page_pdf() -> bytes:
    pages = [Image.open(REG / f).convert("RGB") for f in ("inv01.png", "inv02.png", "inv04.png")]
    pages = [p.resize((1275, 1650)) for p in pages]  # letter size at 150 dpi
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buf.getvalue()


@pytest.mark.parametrize("reader", ["A", "B"])
def test_three_page_pdf_fits_num_ctx(reader: str) -> None:
    gw = Gateway(Settings(closeout_mode="full", ollama_url="http://127.0.0.1:11434"))
    data = three_page_pdf()
    assert len(core.page_images(data, "application/pdf")) == 3
    res = gw.read(data, "application/pdf", reader)  # type: ignore[arg-type]
    assert res.tokens_in is not None and res.tokens_in < core.SETTINGS["num_ctx"], res.tokens_in
    assert res.valid, res.errors
    print(f"reader {reader}: {res.tokens_in} prompt tokens for 3 pages, {res.ms} ms")
