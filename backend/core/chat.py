"""Chat: answer employees' questions from the knowledge base, using the audit results.

Conflict-aware (rules in conflict_index_from, applied to contradictions that are open or confirmed):
  1. an auditor chose the correct side        -> the other side is hidden ("loser")
  2. a NEW text (upload) not reviewed yet     -> the new text is held (hidden) until an auditor decides;
                                                 the live text it conflicts with is still used.
                                                 New vs new -> both are held.
  3. live vs live, a RULE chose the winner    -> the loser is hidden (status / date / version / doc type)
  4. live vs live, only the LLM chose, or nobody could -> both are used and the user gets a warning
     (an LLM mistake must never hide the correct policy)

Citations: the model must cite passages as [1], [2]. Code checks every number points to a real passage.
Plain Python (no Django, no AWS), so it is easy to unit-test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .prompts import CHAT_USER_TEMPLATE

NO_ANSWER = "I could not find this in the company documents you have access to."
CITATION_RE = re.compile(r"\[(\d+)\]")


RULE_RESOLUTIONS = {"status", "effective_date", "version", "doc_type"}  # decided by plain rules, not the LLM
ACTIVE_FINDING_STATUSES = {"open", "confirmed"}


@dataclass
class ConflictIndex:
    losers: dict[int, str] = field(default_factory=dict)  # chunk_id -> why it is outdated
    unresolved: dict[int, str] = field(default_factory=dict)  # chunk_id -> topic of the open conflict
    held: dict[int, str] = field(default_factory=dict)  # chunk_id -> why it is not used yet (new, not reviewed)


@dataclass
class FindingFacts:
    """What the chat needs to know about one finding of the latest audit."""

    chunk_a_id: int
    chunk_b_id: int
    hash_a: str
    hash_b: str
    doc_a_path: str
    doc_b_path: str
    topic: str = ""
    kind: str = "contradiction"
    status: str = "open"  # open / confirmed / rejected / ignored / fixed
    suggested_truth: str = ""  # resolver's suggestion: "a", "b" or ""
    resolution_rule: str = ""  # status / effective_date / version / doc_type / llm / unresolved
    new_hashes: list[str] = field(default_factory=list)  # texts that were new (unreviewed uploads)
    winner_hash: str = ""  # the auditor's decision: fingerprint of the correct text


def conflict_index_from(findings: list[FindingFacts]) -> ConflictIndex:
    index = ConflictIndex()
    for f in findings:
        if f.kind != "contradiction" or f.status not in ACTIVE_FINDING_STATUSES:
            continue
        a = (f.chunk_a_id, f.hash_a, f.doc_a_path)
        b = (f.chunk_b_id, f.hash_b, f.doc_b_path)
        topic = f.topic or "this topic"

        # 1. An auditor decided.
        if f.winner_hash and f.winner_hash in (f.hash_a, f.hash_b):
            winner, loser = (a, b) if f.winner_hash == f.hash_a else (b, a)
            index.losers[loser[0]] = f"Outdated: an auditor confirmed '{winner[2]}' is correct about '{topic}'."
            continue

        # 2. New text that nobody reviewed yet: hold it. The live side keeps being used.
        held = [side for side in (a, b) if side[1] in f.new_hashes]
        if held:
            for side in held:
                other = b if side is a else a
                index.held[side[0]] = (
                    f"Not used yet: conflicts with '{other[2]}' about '{topic}' and is waiting for an auditor's review."
                )
            continue

        # 3. Live vs live, decided by a rule: hide the loser.
        if f.suggested_truth in ("a", "b") and f.resolution_rule in RULE_RESOLUTIONS:
            winner, loser = (a, b) if f.suggested_truth == "a" else (b, a)
            index.losers[loser[0]] = f"Outdated: conflicts with '{winner[2]}' about '{topic}'."
            continue

        # 4. Only the LLM decided, or nobody could: use both and warn.
        index.unresolved[f.chunk_a_id] = topic
        index.unresolved[f.chunk_b_id] = topic
    return index


@dataclass
class ContextPassage:
    number: int
    chunk: Any  # KBChunkRecord
    doc: Any  # KBDocRecord
    similarity: float


def select_passages(hits, docs: dict, conflicts: ConflictIndex, limit: int, min_similarity: float):
    """hits: [(chunk, similarity)] most similar first. Returns (passages, dropped)."""
    passages: list[ContextPassage] = []
    dropped: list[dict] = []
    for chunk, similarity in hits:
        if similarity < min_similarity:
            continue
        doc = docs.get(chunk.doc_id)
        if doc is None:
            continue
        reason = conflicts.losers.get(chunk.id) or conflicts.held.get(chunk.id)
        if reason:
            dropped.append({"doc_path": doc.path, "section": chunk.heading_path, "reason": reason})
            continue
        passages.append(ContextPassage(len(passages) + 1, chunk, doc, similarity))
        if len(passages) >= limit:
            break
    return passages, dropped


def conflict_warnings(passages: list[ContextPassage], conflicts: ConflictIndex) -> list[str]:
    warnings, seen = [], set()
    for p in passages:
        topic = conflicts.unresolved.get(p.chunk.id)
        if topic is None or topic in seen:
            continue
        seen.add(topic)
        warnings.append(
            f"Company documents disagree about '{topic or p.chunk.heading_path}' and this is not resolved yet. "
            "Check with the document owner before relying on this answer. (It has been reported to reviewers.)"
        )
    return warnings


def _scope_line(doc) -> str:
    parts = [f"{label}: {value}" for label, value in (("Region", getattr(doc, "region", "")), ("Audience", getattr(doc, "audience", ""))) if value]
    return (" | " + " | ".join(parts)) if parts else ""


def format_passages(passages: list[ContextPassage]) -> str:
    """Each passage carries its document's date, version and status, so the model can tell which is newer."""
    return "\n\n".join(
        f"[{p.number}] {p.doc.title} ({p.doc.path}) - {p.chunk.heading_path or 'no section'}\n"
        f"Effective date: {p.doc.effective_date or 'unknown'} | Version: {p.doc.version or 'unknown'} | "
        f"Status: {p.doc.status or 'unknown'}{_scope_line(p.doc)}\n"
        f"{p.chunk.text}"
        for p in passages
    )


def build_messages(history: list[tuple[str, str]], passages: list[ContextPassage], question: str) -> list[dict]:
    """history: earlier (role, text) turns, oldest first. Returns Converse messages that start with
    a user turn and alternate user/assistant (Bedrock requires both)."""
    final_text = CHAT_USER_TEMPLATE.format(passages=format_passages(passages), question=question)
    turns = [(role, text) for role, text in history if text.strip()] + [("user", final_text)]
    messages: list[dict] = []
    for role, text in turns:
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"][0]["text"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": [{"text": text}]})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def extract_citations(answer: str, passages: list[ContextPassage]) -> tuple[list[dict], list[int]]:
    """Returns (citations in order of first use, numbers that point to no passage)."""
    by_number = {p.number: p for p in passages}
    citations, invalid, seen = [], [], set()
    for match in CITATION_RE.finditer(answer):
        number = int(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        p = by_number.get(number)
        if p is None:
            invalid.append(number)
            continue
        citations.append(
            {
                "number": number,
                "chunk_id": p.chunk.id,
                "doc_path": p.doc.path,
                "doc_title": p.doc.title,
                "section": p.chunk.heading_path,
                "text": p.chunk.text,
            }
        )
    return citations, invalid
