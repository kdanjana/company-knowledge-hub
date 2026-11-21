"""The chat loads only what one question needs: the documents of the chunks found, and the findings that
involve those chunks. Needs the database; no AWS calls (Bedrock and the knowledge base are replaced by fakes)."""
import json

import pytest
from django.contrib.auth.models import User

from audit.models import AuditRun, CandidatePair, Finding
from chat.models import Conversation
from chat.services import build_conflict_index, stream_reply
from core.records import KBChunkRecord, KBDocRecord


def chunk(chunk_id: int, doc_id: int, heading: str, text: str) -> KBChunkRecord:
    return KBChunkRecord(id=chunk_id, doc_id=doc_id, position=0, heading_path=heading, text=text, content_hash=f"h{chunk_id}")


# 6 chunks from 2 documents (22 and 30), most similar first. The last one is below the 0.3 threshold.
HITS = [
    (chunk(301, 22, "Leave > Annual leave", "Employees get 22 days of annual leave per year."), 0.82),
    (chunk(302, 22, "Leave > Carry over", "Up to 5 unused days can be carried over."), 0.61),
    (chunk(303, 22, "Leave > Sick leave", "Sick leave needs a doctor's note after 2 days."), 0.55),
    (chunk(501, 30, "Holidays > Public holidays", "Public holidays: 26 Jan, 15 Aug, 2 Oct."), 0.41),
    (chunk(502, 30, "Holidays > Optional holidays", "Choose 2 optional holidays per year."), 0.35),
    (chunk(304, 22, "Leave > Maternity leave", "Maternity leave is 26 weeks."), 0.25),
]
DOCS = {
    22: KBDocRecord(id=22, path="uploads/d1-leave-policy__v2.md", title="D1 Leave Policy", version="2", status="active"),
    30: KBDocRecord(id=30, path="holiday-calendar.md", title="Holiday Calendar"),
    99: KBDocRecord(id=99, path="other.md", title="A document the search did not find"),
}


class FakeAdapter:
    """Stands in for the Postgres knowledge base and records what the chat asked for."""

    requested: list[set[int]] = []

    def __init__(self, dsn):
        pass

    def search_by_vector(self, vector, k, departments=None):
        return HITS

    def get_documents(self, doc_ids):
        FakeAdapter.requested.append(set(doc_ids))
        return [DOCS[i] for i in doc_ids if i in DOCS]

    def list_documents(self):
        raise AssertionError("the chat must not load every document")

    def close(self):
        pass


class FakeLLM:
    def __init__(self, cfg):
        pass

    def embed(self, text, **kwargs):
        return [0.0, 0.0, 0.0]

    def converse_stream(self, messages, system=None, max_tokens=800):
        yield {"type": "text", "text": "You get 22 days of annual leave per year [1]."}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 5, "latency_ms": 1}

    def cost(self, input_tokens, output_tokens):
        return 0.0


@pytest.mark.django_db
def test_chat_fetches_only_the_documents_of_the_chunks_found(monkeypatch):
    FakeAdapter.requested = []
    monkeypatch.setattr("core.config.Config.require_bedrock", lambda self: None)
    monkeypatch.setattr("chat.services.PgvectorKBAdapter", FakeAdapter)
    monkeypatch.setattr("chat.services.BedrockClient", FakeLLM)

    user = User.objects.create_user(username="d1u1", password="test-only-password")
    conversation = Conversation.objects.create(user=user)
    lines = list(stream_reply(user, conversation, "How many annual leave days do I get?"))
    events = [json.loads(line) for line in lines]

    assert FakeAdapter.requested == [{22, 30}]  # not document 99, and list_documents was never called
    assert not any(e["type"] == "error" for e in events)
    done = next(e for e in events if e["type"] == "done")
    assert done["message"]["citations"][0]["doc_title"] == "D1 Leave Policy"


def _contradiction(run: AuditRun, number: int, chunk_a: int, chunk_b: int, winner: str) -> Finding:
    """A contradiction an auditor already decided (winner 'a' or 'b'): the other chunk is hidden in chat."""
    pair = CandidatePair.objects.create(
        run=run, chunk_a_id=chunk_a, chunk_b_id=chunk_b, doc_a_id=1, doc_b_id=2, pair_key=f"key{number}", similarity=0.9
    )
    hash_a, hash_b = f"ha{number}", f"hb{number}"
    return Finding.objects.create(
        run=run,
        pair=pair,
        kind=Finding.Kind.CONTRADICTION,
        severity=Finding.Severity.HIGH,
        topic=f"topic {number}",
        chunk_a_id=chunk_a,
        chunk_b_id=chunk_b,
        doc_a_path="a.md",
        doc_b_path="b.md",
        pair_key=f"key{number}",
        hash_a=hash_a,
        hash_b=hash_b,
        status=Finding.Status.CONFIRMED,
        winner_hash=hash_a if winner == "a" else hash_b,
    )


@pytest.mark.django_db
def test_only_findings_about_the_chunks_found_are_used_and_the_rules_are_the_same():
    run = AuditRun.objects.create(status=AuditRun.Status.DONE)
    _contradiction(run, 1, chunk_a=301, chunk_b=900, winner="b")  # involves a found chunk: 301 lost
    _contradiction(run, 2, chunk_a=700, chunk_b=800, winner="a")  # not about the found chunks
    _contradiction(run, 3, chunk_a=710, chunk_b=810, winner="a")  # not about the found chunks

    found = {301, 302, 303}
    filtered = build_conflict_index(found)
    everything = build_conflict_index()

    # The same rules for every chunk the search found...
    for chunk_id in found:
        assert filtered.losers.get(chunk_id) == everything.losers.get(chunk_id)
        assert filtered.held.get(chunk_id) == everything.held.get(chunk_id)
        assert filtered.unresolved.get(chunk_id) == everything.unresolved.get(chunk_id)
    assert 301 in filtered.losers

    # ...but the findings about other chunks were not loaded.
    assert 800 in everything.losers and 810 in everything.losers
    assert 800 not in filtered.losers and 810 not in filtered.losers
