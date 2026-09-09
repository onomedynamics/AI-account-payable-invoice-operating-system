"""Turn a stored document into plain text.

M2a: born-digital PDFs via pdfplumber. Scanned PDFs (no text layer) and image
uploads return empty text -- the caller treats that as a hard failure until the
OCR fallback lands (M2, later). No OCR engine is a dependency yet.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber


@dataclass(frozen=True)
class DocumentText:
    text: str
    page_count: int
    method: str  # "pdfplumber" | "ocr" (later)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def extract_text(data: bytes, content_type: str) -> DocumentText:
    if content_type == "application/pdf":
        return _pdf_text(data)
    if content_type.startswith("image/"):
        # OCR fallback not implemented yet.
        return DocumentText(text="", page_count=1, method="ocr")
    raise ValueError(f"cannot extract text from content type: {content_type}")


def _pdf_text(data: bytes) -> DocumentText:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return DocumentText(
        text="\n\n".join(pages).strip(),
        page_count=len(pages),
        method="pdfplumber",
    )
