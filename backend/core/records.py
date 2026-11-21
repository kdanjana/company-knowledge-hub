"""Simple data records for what we read from the company knowledge base.

No database code here, so tests can create these objects directly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class KBDocRecord:
    id: int
    path: str
    title: str
    doc_type: str = ""
    version: str = ""
    effective_date: date | None = None
    status: str = ""
    owner: str = ""
    chunk_count: int = 0
    departments: list[str] = field(default_factory=list)  # empty = everyone
    source: str = "folder"  # "folder" (existing knowledge base) or "upload"
    state: str = "live"  # life-cycle state; only "live" documents are used by the chat
    doc_id: str = ""  # stable ID shared by all versions (uploads; empty for most seeded documents)
    region: str = ""  # scope of the content, e.g. "India", "UAE" (optional)
    audience: str = ""  # e.g. "employees", "delivery partners" (optional)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["effective_date"] = self.effective_date.isoformat() if self.effective_date else None
        return data


@dataclass
class KBChunkRecord:
    id: int
    doc_id: int
    position: int
    heading_path: str
    text: str
    content_hash: str

    def to_dict(self) -> dict:
        return asdict(self)
