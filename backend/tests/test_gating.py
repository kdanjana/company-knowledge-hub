"""The chat rules from audit findings: one test per row of the rules table. No database, no AWS."""
from core.chat import FindingFacts, conflict_index_from

OLD, NEW = 10, 20  # chunk ids: 10 = live policy "3 days", 20 = uploaded text "10 days"


def finding(**overrides) -> FindingFacts:
    values = dict(
        chunk_a_id=OLD, chunk_b_id=NEW, hash_a="h-old", hash_b="h-new",
        doc_a_path="refund-policy-2025.md", doc_b_path="uploads/refund-update.md", topic="refund window",
    )
    values.update(overrides)
    return FindingFacts(**values)


def test_new_text_is_held_and_live_text_is_still_used():
    index = conflict_index_from([finding(new_hashes=["h-new"], suggested_truth="b", resolution_rule="effective_date")])
    assert NEW in index.held and "auditor" in index.held[NEW]
    assert OLD not in index.losers and OLD not in index.held  # the newer date must NOT win automatically


def test_new_vs_new_holds_both():
    index = conflict_index_from([finding(new_hashes=["h-old", "h-new"])])
    assert set(index.held) == {OLD, NEW}


def test_auditor_decision_wins_over_everything():
    index = conflict_index_from([finding(new_hashes=["h-new"], winner_hash="h-new", status="confirmed")])
    assert index.losers.keys() == {OLD}
    assert not index.held


def test_live_vs_live_decided_by_a_rule_hides_the_loser():
    index = conflict_index_from([finding(suggested_truth="b", resolution_rule="effective_date")])
    assert index.losers.keys() == {OLD}
    assert not index.unresolved


def test_live_vs_live_decided_only_by_the_llm_shows_both_with_a_warning():
    index = conflict_index_from([finding(suggested_truth="b", resolution_rule="llm")])
    assert not index.losers
    assert index.unresolved == {OLD: "refund window", NEW: "refund window"}


def test_live_vs_live_without_a_winner_shows_both_with_a_warning():
    index = conflict_index_from([finding(suggested_truth="", resolution_rule="unresolved")])
    assert set(index.unresolved) == {OLD, NEW}


def test_rejected_ignored_and_fixed_findings_change_nothing():
    for status in ("rejected", "ignored", "fixed"):
        index = conflict_index_from([finding(status=status, new_hashes=["h-new"], suggested_truth="a", resolution_rule="status")])
        assert not (index.losers or index.held or index.unresolved), status


def test_duplicates_change_nothing():
    index = conflict_index_from([finding(kind="duplicate", suggested_truth="a", resolution_rule="status")])
    assert not (index.losers or index.held or index.unresolved)
