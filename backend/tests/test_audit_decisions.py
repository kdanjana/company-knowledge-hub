"""What an audit carries over from earlier audits: reviewer decisions, held texts, re-checks, "fixed",
resolver cache, and approving a new version. Needs the database; no AWS calls."""
import itertools
from dataclasses import dataclass

import pytest
from rest_framework.test import APIClient

from accounts.roles import AUDITOR
from audit.models import AuditRun, CandidatePair, Feedback, Finding
from audit.services import (
    _cached_resolution,
    accept_new_version,
    latest_decisions,
    mark_fixed,
    new_hashes_for,
    recheck_candidates,
)
from tests.test_permissions import make_user

_ids = itertools.count(1000)


@dataclass
class Chunk:
    id: int
    doc_id: int
    content_hash: str
    text: str = "Refund within 3 days."


def done_run() -> AuditRun:
    return AuditRun.objects.create(status=AuditRun.Status.DONE)


def make_finding(run, pkey="pair-1", hash_a="h-old", hash_b="h-new", **extra) -> Finding:
    a, b = next(_ids), next(_ids)
    pair = CandidatePair.objects.create(
        run=run, chunk_a_id=a, chunk_b_id=b, doc_a_id=1, doc_b_id=2, pair_key=pkey, similarity=0.9
    )
    values = dict(
        run=run, pair=pair, kind=Finding.Kind.CONTRADICTION, severity=Finding.Severity.HIGH, topic="refund window",
        chunk_a_id=a, chunk_b_id=b, doc_a_path="refund-policy-2025.md", doc_b_path="uploads/update.md",
        pair_key=pkey, hash_a=hash_a, hash_b=hash_b,
    )
    values.update(extra)
    return Finding.objects.create(**values)


@pytest.mark.django_db
def test_the_newest_decision_per_pair_wins():
    f = make_finding(done_run())
    Feedback.objects.create(finding=f, pair_key="pair-1", decision=Feedback.Decision.NOT_CONFLICT)
    Feedback.objects.create(finding=f, pair_key="pair-1", decision=Feedback.Decision.REAL, winner_hash="h-old")
    assert latest_decisions()["pair-1"].decision == Feedback.Decision.REAL


@pytest.mark.django_db
def test_held_text_stays_held_in_later_audits():
    first = done_run()
    # First audit: the uploaded document (id 2) is new.
    assert new_hashes_for("pair-1", first, [("h-old", 1), ("h-new", 2)], new_doc_ids={2}) == ["h-new"]
    make_finding(first, new_hashes=["h-new"])

    # Next audit: the upload is live now (not new), but its conflicting text must stay held.
    second = AuditRun.objects.create(status=AuditRun.Status.RUNNING)
    assert new_hashes_for("pair-1", second, [("h-old", 1), ("h-new", 2)], new_doc_ids=set()) == ["h-new"]


@pytest.mark.django_db
def test_open_findings_are_rechecked_even_if_the_cap_left_them_out():
    previous = done_run()
    make_finding(previous, pkey="p1", hash_a="h1", hash_b="h2")
    make_finding(previous, pkey="p2", hash_a="h3", hash_b="gone")  # one text no longer exists
    make_finding(previous, pkey="p3", hash_a="h4", hash_b="h5", status=Finding.Status.REJECTED)
    chunks = [Chunk(1, 1, "h1"), Chunk(2, 2, "h2"), Chunk(3, 1, "h3"), Chunk(4, 1, "h4"), Chunk(5, 2, "h5")]

    found = recheck_candidates(previous, chunks, already=set())
    assert [(c.a_id, c.b_id) for c in found] == [(1, 2)]
    assert recheck_candidates(previous, chunks, already={(1, 2)}) == []  # already a candidate


@pytest.mark.django_db
def test_findings_that_disappear_are_marked_fixed():
    previous = done_run()
    gone = make_finding(previous, pkey="gone")
    stays = make_finding(previous, pkey="stays")
    current = AuditRun.objects.create(status=AuditRun.Status.RUNNING)
    make_finding(current, pkey="stays")

    assert mark_fixed(current, previous) == 1
    gone.refresh_from_db()
    stays.refresh_from_db()
    assert gone.status == Finding.Status.FIXED
    assert stays.status == Finding.Status.OPEN


@pytest.mark.django_db
def test_cached_resolver_answer_follows_the_text_not_the_side():
    make_finding(done_run(), hash_a="h1", hash_b="h2", suggested_truth="a", resolution_rule="llm", resolution_key="k")
    # This time the same texts come in the other order.
    res = _cached_resolution("k", hash_a="h2", hash_b="h1")
    assert res.winner == "b" and res.rule == "llm"
    assert _cached_resolution("other-key", "h1", "h2") is None


@pytest.mark.django_db
def test_approving_a_new_version_decides_its_conflicts():
    f = make_finding(done_run(), new_hashes=["h-new"])
    assert accept_new_version(doc_id=2, chunk_hashes={"h-new", "h-other"}) == 1
    f.refresh_from_db()
    assert f.status == Finding.Status.CONFIRMED and f.winner_hash == "h-new"
    assert Feedback.objects.get(finding=f).winner_hash == "h-new"  # remembered for later audits


@pytest.mark.django_db
def test_auditor_picks_the_correct_side_and_it_is_saved_by_fingerprint():
    f = make_finding(done_run())
    api = APIClient()
    api.force_authenticate(make_user("aud", AUDITOR, "Legal"))

    response = api.post(f"/api/findings/{f.id}/feedback/", {"decision": "real", "winner": "a"}, format="json")
    assert response.status_code == 200
    assert response.json()["winner"] == "a"
    assert Feedback.objects.get(finding=f).winner_hash == "h-old"

    bad = api.post(f"/api/findings/{f.id}/feedback/", {"decision": "not_conflict", "winner": "a"}, format="json")
    assert bad.status_code == 400
