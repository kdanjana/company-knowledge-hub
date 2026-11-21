"""Step 1 of the audit: find candidate pairs cheaply, without an LLM.

Comparing every chunk with every other chunk grows as n^2 (10,000 chunks -> ~50 million pairs).
Instead, for each chunk we ask the vector index for its top-k most similar chunks (n * k pairs),
remove duplicates, and rank them.

Important: very high similarity does NOT mean "duplicate, skip it". "Refund within 7 days" and
"Refund within 3 days" are almost identical text -- contradictions often look like duplicates
to embeddings. Only EXACT same text (after normalising case/spaces) is marked duplicate by code;
everything else goes to the LLM judge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .chunking import sha256

NUMBER_RE = re.compile(r"\d+(?:[.,:]\d+)?")


class _HasText(Protocol):
    id: int
    text: str


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def extract_numbers(text: str) -> set[str]:
    return set(NUMBER_RE.findall(text))


def pair_key(hash_a: str, hash_b: str) -> str:
    """Stable ID for a pair of chunk contents, independent of order and of database IDs."""
    first, second = sorted((hash_a, hash_b))
    return sha256(f"{first}|{second}")


@dataclass
class Candidate:
    a_id: int
    b_id: int
    similarity: float
    exact_duplicate: bool
    numbers_differ: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def priority(self) -> int:
        # Pairs whose numbers differ are the most likely contradictions: judge them first.
        if self.numbers_differ:
            return 2
        if self.exact_duplicate:
            return 1
        return 0


def build_candidate(chunk_a: _HasText, chunk_b: _HasText, similarity: float) -> Candidate:
    if chunk_a.id > chunk_b.id:
        chunk_a, chunk_b = chunk_b, chunk_a
    exact = normalize(chunk_a.text) == normalize(chunk_b.text)
    numbers_a, numbers_b = extract_numbers(chunk_a.text), extract_numbers(chunk_b.text)
    numbers_differ = bool(numbers_a or numbers_b) and numbers_a != numbers_b
    reasons = [f"similarity {similarity:.2f}"]
    if exact:
        reasons.append("exact same text")
    if numbers_differ:
        reasons.append("numbers differ")
    return Candidate(
        a_id=chunk_a.id,
        b_id=chunk_b.id,
        similarity=similarity,
        exact_duplicate=exact,
        numbers_differ=numbers_differ,
        reasons=reasons,
    )


def audiences_overlap(departments_a: list[str], departments_b: list[str]) -> bool:
    """True if at least one person can see both documents in chat.
    An empty list means "everyone", so a company-wide document overlaps with every document."""
    return not departments_a or not departments_b or bool(set(departments_a) & set(departments_b))


def generate_candidates(
    adapter,
    chunks: list,
    top_k: int,
    min_similarity: float,
    max_pairs: int,
    allowed: Callable[[object, object], bool] | None = None,
    stats: dict | None = None,
) -> list[Candidate]:
    """adapter must provide find_similar(chunk_id, k) -> [(other_id, similarity), ...].

    allowed(chunk_a, chunk_b): optional filter, e.g. "can one person see both documents?".
    stats: optional dict, filled with pairs_found / pairs_cut_by_cap / pairs_skipped_no_shared_audience,
    so a run can show what it did NOT check (the cap is no longer silent)."""
    by_id = {c.id: c for c in chunks}
    found: dict[tuple[int, int], Candidate] = {}
    skipped: set[tuple[int, int]] = set()
    for chunk in chunks:
        for other_id, similarity in adapter.find_similar(chunk.id, top_k):
            if similarity < min_similarity or other_id not in by_id:
                continue
            key = (min(chunk.id, other_id), max(chunk.id, other_id))
            if key in found or key in skipped:
                continue
            if allowed is not None and not allowed(by_id[key[0]], by_id[key[1]]):
                skipped.add(key)
                continue
            found[key] = build_candidate(by_id[key[0]], by_id[key[1]], similarity)
    ranked = sorted(found.values(), key=lambda c: (-c.priority, -c.similarity))
    if stats is not None:
        stats["pairs_found"] = len(ranked)
        stats["pairs_cut_by_cap"] = max(0, len(ranked) - max_pairs)
        stats["pairs_skipped_no_shared_audience"] = len(skipped)
    return ranked[:max_pairs]
