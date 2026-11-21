from datetime import date

from core.chat import (
    ConflictIndex,
    build_messages,
    conflict_warnings,
    extract_citations,
    format_passages,
    select_passages,
)
from core.records import KBChunkRecord, KBDocRecord

DOCS = {
    1: KBDocRecord(id=1, path="refund-policy-2023.md", title="Refund Policy"),
    2: KBDocRecord(id=2, path="refund-policy-2025.md", title="Refund Policy"),
}
OLD = KBChunkRecord(10, 1, 0, "Refund Policy > India", "Refund within 7 days.", "h10")
NEW = KBChunkRecord(20, 2, 0, "Refund Policy > India", "Refund within 3 days.", "h20")
OTHER = KBChunkRecord(21, 2, 1, "Refund Policy > UAE", "Refund within 5 days.", "h21")


def test_outdated_passage_is_dropped():
    conflicts = ConflictIndex(losers={10: "Outdated"})
    passages, dropped = select_passages([(OLD, 0.9), (NEW, 0.88)], DOCS, conflicts, limit=5, min_similarity=0.3)
    assert [p.chunk.id for p in passages] == [20]
    assert passages[0].number == 1
    assert dropped == [{"doc_path": "refund-policy-2023.md", "section": "Refund Policy > India", "reason": "Outdated"}]


def test_held_passage_is_dropped_with_its_reason():
    conflicts = ConflictIndex(held={20: "Not used yet: waiting for an auditor's review."})
    passages, dropped = select_passages([(NEW, 0.9), (OTHER, 0.8)], DOCS, conflicts, limit=5, min_similarity=0.3)
    assert [p.chunk.id for p in passages] == [21]
    assert dropped[0]["reason"].startswith("Not used yet")


def test_passages_show_date_version_and_status():
    docs = {2: KBDocRecord(id=2, path="refund-policy-2025.md", title="Refund Policy", version="2",
                           effective_date=date(2025, 4, 1), status="active")}
    passages, _ = select_passages([(NEW, 0.9)], docs, ConflictIndex(), limit=5, min_similarity=0.3)
    text = format_passages(passages)
    assert "Effective date: 2025-04-01 | Version: 2 | Status: active" in text


def test_low_similarity_and_limit():
    passages, _ = select_passages(
        [(NEW, 0.9), (OTHER, 0.8), (OLD, 0.1)], DOCS, ConflictIndex(), limit=1, min_similarity=0.3
    )
    assert [p.chunk.id for p in passages] == [20]


def test_unresolved_conflict_gives_one_warning_per_topic():
    conflicts = ConflictIndex(unresolved={20: "refund window", 21: "refund window"})
    passages, _ = select_passages([(NEW, 0.9), (OTHER, 0.8)], DOCS, conflicts, limit=5, min_similarity=0.3)
    warnings = conflict_warnings(passages, conflicts)
    assert len(warnings) == 1
    assert "refund window" in warnings[0]


def test_citations_are_checked():
    passages, _ = select_passages([(NEW, 0.9), (OTHER, 0.8)], DOCS, ConflictIndex(), limit=5, min_similarity=0.3)
    citations, invalid = extract_citations("India: 3 days [1]. UAE: 5 days [2]. Also [1] and [7].", passages)
    assert [c["number"] for c in citations] == [1, 2]
    assert citations[0]["doc_path"] == "refund-policy-2025.md"
    assert invalid == [7]


def test_messages_alternate_and_start_with_user():
    passages, _ = select_passages([(NEW, 0.9)], DOCS, ConflictIndex(), limit=5, min_similarity=0.3)
    history = [("assistant", "stray answer"), ("user", "first question"), ("user", "repeated question")]
    messages = build_messages(history, passages, "How many days for a refund?")
    roles = [m["role"] for m in messages]
    assert roles[0] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert "How many days for a refund?" in messages[-1]["content"][0]["text"]
    assert "[1] Refund Policy (refund-policy-2025.md)" in messages[-1]["content"][0]["text"]
