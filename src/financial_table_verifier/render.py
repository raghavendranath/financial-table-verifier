"""Render one page at a time without embedded PDF text or XBRL input."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pypdfium2 as pdfium


def page_count(path: Path) -> int:
    with pdfium.PdfDocument(path) as pdf:
        return len(pdf)


def render_page(path: Path, page_number: int, dpi: int, max_pixels: int) -> bytes:
    with pdfium.PdfDocument(path) as pdf:
        if not 1 <= page_number <= len(pdf):
            raise ValueError(f"Page {page_number} is outside PDF")
        page = pdf[page_number - 1]
        try:
            width, height = page.get_size()
            if width * height * (dpi / 72) ** 2 > max_pixels:
                raise ValueError("Rendered page would exceed configured pixel limit")
            bitmap = page.render(scale=dpi / 72)
            try:
                image = bitmap.to_pil()
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                return stream.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
