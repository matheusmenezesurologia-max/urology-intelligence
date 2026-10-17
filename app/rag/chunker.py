"""Text chunking for embedding/indexing.

Paragraph-aware splitter with character-based target size and overlap. Keeps
chunks within a comfortable token budget for ``text-embedding-3-large`` while
preserving sentence boundaries where possible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_PARA_SPLIT = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    index: int
    text: str


def _sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(paragraph) if s.strip()]


def chunk_text(text: str, *, target_chars: int = 1400, overlap_chars: int = 200) -> list[Chunk]:
    """Split ``text`` into overlapping, paragraph/sentence-aware chunks."""
    text = (text or "").strip()
    if not text:
        return []

    units: list[str] = []
    for para in _PARA_SPLIT.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= target_chars:
            units.append(para)
        else:
            # Break oversized paragraphs into sentences.
            buf = ""
            for sent in _sentences(para):
                if len(buf) + len(sent) + 1 > target_chars and buf:
                    units.append(buf.strip())
                    buf = sent
                else:
                    buf = f"{buf} {sent}".strip()
            if buf:
                units.append(buf.strip())

    # Greedily pack units into chunks, then add tail overlap for continuity.
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(current) + len(unit) + 1 > target_chars and current:
            chunks.append(current.strip())
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = f"{tail} {unit}".strip()
        else:
            current = f"{current} {unit}".strip()
    if current.strip():
        chunks.append(current.strip())

    return [Chunk(index=i, text=c) for i, c in enumerate(chunks)]
