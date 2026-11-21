"""Step 4 of the audit: for a contradiction, suggest which side is the source of truth.

Rules first (cheap, predictable, easy to explain):
  1. status: a draft / superseded / archived document loses to an active one
  2. effective date: the newer document wins (only if both have a date)
  3. version: the higher version wins (only for versions of the same document: same doc_id,
     or the same title for documents without a doc_id)
  4. document type: policy / procedure beat faq beat guide / slides
Only if no rule decides do we ask the LLM, and "unknown" is an allowed answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .judge import extract_json, format_passage
from .prompts import RESOLVER_SYSTEM, RESOLVER_USER_TEMPLATE

INACTIVE_STATUSES = {"draft", "superseded", "archived", "retired"}
DOC_TYPE_RANK = {"policy": 3, "procedure": 3, "faq": 2, "guide": 1, "slides": 1}
VERSION_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Resolution:
    winner: str  # "a", "b" or "" (unknown)
    rule: str
    reason: str
    suggested_action: str


def _action(winner_doc, loser_doc) -> str:
    return f"Review '{loser_doc.path}': archive it or update the conflicting text to match '{winner_doc.path}'."


def _pick(side: str, rule: str, reason: str, doc_a, doc_b) -> Resolution:
    winner, loser = (doc_a, doc_b) if side == "a" else (doc_b, doc_a)
    return Resolution(winner=side, rule=rule, reason=reason, suggested_action=_action(winner, loser))


def _version_number(version: str) -> float | None:
    match = VERSION_RE.search(version or "")
    return float(match.group()) if match else None


def _same_document(doc_a, doc_b) -> bool:
    """Two versions of one document: same doc_id when both have one, otherwise the same title."""
    id_a, id_b = getattr(doc_a, "doc_id", ""), getattr(doc_b, "doc_id", "")
    if id_a and id_b:
        return id_a == id_b
    return (doc_a.title or "").strip().lower() == (doc_b.title or "").strip().lower()


def resolve_by_rules(doc_a, doc_b) -> Resolution | None:
    if doc_a.id == doc_b.id:
        return None  # same document: metadata cannot decide

    status_a, status_b = (doc_a.status or "").lower(), (doc_b.status or "").lower()
    if status_a in INACTIVE_STATUSES and status_b not in INACTIVE_STATUSES:
        return _pick("b", "status", f"'{doc_a.path}' is marked '{status_a}'.", doc_a, doc_b)
    if status_b in INACTIVE_STATUSES and status_a not in INACTIVE_STATUSES:
        return _pick("a", "status", f"'{doc_b.path}' is marked '{status_b}'.", doc_a, doc_b)

    if doc_a.effective_date and doc_b.effective_date and doc_a.effective_date != doc_b.effective_date:
        side = "a" if doc_a.effective_date > doc_b.effective_date else "b"
        newer = doc_a if side == "a" else doc_b
        return _pick(side, "effective_date", f"'{newer.path}' is newer ({newer.effective_date}).", doc_a, doc_b)

    if _same_document(doc_a, doc_b):
        version_a, version_b = _version_number(doc_a.version), _version_number(doc_b.version)
        if version_a is not None and version_b is not None and version_a != version_b:
            side = "a" if version_a > version_b else "b"
            higher = doc_a if side == "a" else doc_b
            return _pick(side, "version", f"'{higher.path}' has the higher version ({higher.version}).", doc_a, doc_b)

    rank_a = DOC_TYPE_RANK.get((doc_a.doc_type or "").lower(), 0)
    rank_b = DOC_TYPE_RANK.get((doc_b.doc_type or "").lower(), 0)
    if rank_a != rank_b and max(rank_a, rank_b) > 0:
        side = "a" if rank_a > rank_b else "b"
        stronger = doc_a if side == "a" else doc_b
        return _pick(side, "doc_type", f"'{stronger.path}' is a {stronger.doc_type}, which takes priority.", doc_a, doc_b)

    return None


def resolve_with_llm(llm, topic: str, chunk_a, doc_a, chunk_b, doc_b) -> tuple[Resolution, tuple[int, int]]:
    """Fallback when no rule decides. Returns (resolution, (input_tokens, output_tokens))."""
    user = RESOLVER_USER_TEMPLATE.format(
        topic=topic or "(not given)",
        passage_a=format_passage("A", chunk_a, doc_a),
        passage_b=format_passage("B", chunk_b, doc_b),
    )
    result = llm.converse([{"role": "user", "content": [{"text": user}]}], system=RESOLVER_SYSTEM, max_tokens=300)
    usage = (result.input_tokens, result.output_tokens)
    try:
        data = extract_json(result.text)
        winner = str(data.get("winner", "")).strip().lower()
        reason = str(data.get("reason") or "").strip()[:1000]
    except ValueError:
        winner, reason = "unknown", "The resolver did not give a valid answer."

    if winner in ("a", "b"):
        return _pick(winner, "llm", reason, doc_a, doc_b), usage
    return (
        Resolution(
            winner="",
            rule="unresolved",
            reason=reason or "The documents do not show which one is current.",
            suggested_action=f"Ask the owners of '{doc_a.path}' and '{doc_b.path}' which one is correct.",
        ),
        usage,
    )
