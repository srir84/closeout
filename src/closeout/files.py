"""Uploaded files: type from the first bytes, size and page limits, storage by hash (design 3.16)."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from closeout.settings import get_settings

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_PAGES = 3
MAX_PAGE_POINTS = (17 * 72, 22 * 72)  # 17 x 22 inches
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses bigger images outright

MESSAGES = {
    "too_big": "The file is over 10 MB.",
    "wrong_type": "Only PNG, JPG or PDF files can be uploaded.",
    "too_many_pixels": "The image is over 25 megapixels.",
    "unreadable": "The file couldn't be opened.",
    "too_many_pages": "A PDF can have at most 3 pages.",
    "page_too_big": "A PDF page is larger than 17 x 22 inches.",
    "empty": "The file is empty.",
}


@dataclass(frozen=True)
class Checked:
    ok: bool
    reason: str | None
    mime: str | None = None
    pages: int = 1
    sha256: str = ""


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def check(data: bytes) -> Checked:
    """Checked before anything is drawn: type from the bytes, size, pixels or pages."""
    if not data:
        return Checked(False, "empty")
    if len(data) > MAX_BYTES:
        return Checked(False, "too_big")
    mime = sniff(data)
    if mime is None:
        return Checked(False, "wrong_type")
    sha = hashlib.sha256(data).hexdigest()
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(data)
        except Exception:
            return Checked(False, "unreadable")
        try:
            n = len(pdf)
            if n == 0:
                return Checked(False, "unreadable")
            if n > MAX_PAGES:
                return Checked(False, "too_many_pages")
            for i in range(n):
                w, h = pdf[i].get_size()
                lo, hi = sorted((w, h))
                if lo > MAX_PAGE_POINTS[0] + 0.5 or hi > MAX_PAGE_POINTS[1] + 0.5:
                    return Checked(False, "page_too_big")
        finally:
            pdf.close()
        return Checked(True, None, mime, n, sha)
    try:
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
            if w * h > MAX_PIXELS:
                return Checked(False, "too_many_pixels")
            img.verify()
    except Image.DecompressionBombError:
        return Checked(False, "too_many_pixels")
    except Exception:
        return Checked(False, "unreadable")
    return Checked(True, None, mime, 1, sha)


EXT = {"image/png": "png", "image/jpeg": "jpg", "application/pdf": "pdf"}


def store(data: bytes, mime: str) -> str:
    """Save under the uploads folder by hash. Returns the path relative to that folder."""
    sha = hashlib.sha256(data).hexdigest()
    rel = f"{sha[:2]}/{sha}.{EXT[mime]}"
    p = Path(get_settings().uploads_dir) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_bytes(data)
    return rel


def store_raw(data: bytes, rel: str) -> Path:
    p = Path(get_settings().uploads_dir) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def path_of(rel: str) -> Path:
    return Path(get_settings().uploads_dir) / rel
