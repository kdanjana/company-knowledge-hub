"""Reading Markdown documents and splitting them into chunks by heading.

Used by the simulated "chatbot team" ingestion (`seed_kb`), not by the auditor itself.
Each chunk keeps its heading path (e.g. "Refund Policy > India > Refund window"), so a
condition written in a heading (like a region) stays attached to the text.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

import yaml

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_document(raw: str) -> tuple[dict, str]:
    """Split a Markdown file into (front-matter metadata, body). Missing front-matter -> ({}, raw)."""
    match = FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, raw[match.end():]


def parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


@dataclass
class Chunk:
    position: int
    heading_path: str
    text: str

    @property
    def content_hash(self) -> str:
        return sha256(f"{self.heading_path}\n{self.text}")

    def embed_text(self) -> str:
        """Text sent to the embedding model: headings + body, so context is part of the meaning."""
        return f"{self.heading_path}\n{self.text}" if self.heading_path else self.text


def chunk_markdown(body: str, default_title: str = "", max_chars: int = 1200) -> list[Chunk]:
    """One chunk per heading section; long sections are split by paragraph."""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []

    def current_path() -> str:
        names = [title for _, title in stack]
        if not names and default_title:
            names = [default_title]
        return " > ".join(names)

    path = current_path()
    lines: list[str] = []
    for line in body.splitlines():
        match = HEADING_RE.match(line)
        if match:
            sections.append((path, lines))
            lines = []
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            path = current_path()
        else:
            lines.append(line)
    sections.append((path, lines))

    chunks: list[Chunk] = []
    for heading_path, section_lines in sections:
        text = "\n".join(section_lines).strip()
        if not text:
            continue
        for piece in _split_long(text, max_chars):
            chunks.append(Chunk(position=len(chunks), heading_path=heading_path, text=piece))
    return chunks


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if buffer and len(buffer) + len(paragraph) + 2 > max_chars:
            pieces.append(buffer)
            buffer = paragraph
        else:
            buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
    if buffer:
        pieces.append(buffer)
    return pieces
