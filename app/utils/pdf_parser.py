"""PDF text extraction using PyMuPDF (fitz)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from app.utils.logger import get_logger

log = get_logger("pdf_parser")

# Common section headers found in biomedical articles (case-insensitive).
SECTION_PATTERNS = {
    "abstract": r"^\s*abstract\b",
    "introduction": r"^\s*(introduction|background)\b",
    "methods": r"^\s*(methods|materials and methods|patients and methods|methodology)\b",
    "results": r"^\s*results\b",
    "discussion": r"^\s*discussion\b",
    "conclusion": r"^\s*(conclusion|conclusions)\b",
    "references": r"^\s*(references|bibliography)\b",
}


@dataclass
class ParsedPDF:
    path: str
    page_count: int
    text: str
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)


def _clean(text: str) -> str:
    # Collapse hyphenation across line breaks and excess whitespace.
    text = re.sub(r"-\n(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_sections(text: str) -> dict[str, str]:
    """Best-effort split of the full text into canonical sections."""
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current = "body"
    sections[current] = []
    for line in lines:
        matched = None
        for name, pattern in SECTION_PATTERNS.items():
            if re.match(pattern, line.strip(), flags=re.IGNORECASE) and len(line.strip()) < 40:
                matched = name
                break
        if matched:
            current = matched
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    return {k: _clean("\n".join(v)) for k, v in sections.items() if "".join(v).strip()}


def parse_pdf(path: str | Path, max_chars: int | None = 120_000) -> ParsedPDF:
    """Extract and lightly structure text from a PDF file.

    ``max_chars`` truncates very large documents to keep LLM context bounded.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    pages: list[str] = []
    with fitz.open(path) as doc:
        page_count = doc.page_count
        for page in doc:
            pages.append(page.get_text("text"))

    full = _clean("\n".join(pages))
    if max_chars and len(full) > max_chars:
        log.info("pdf_truncated", path=str(path), original=len(full), kept=max_chars)
        full = full[:max_chars]

    sections = _split_sections(full)
    log.info("pdf_parsed", path=str(path), pages=page_count, chars=len(full), sections=list(sections))
    return ParsedPDF(path=str(path), page_count=page_count, text=full, sections=sections)
