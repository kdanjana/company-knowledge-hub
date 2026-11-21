from datetime import date

from core.records import KBDocRecord
from core.resolver import resolve_by_rules


def doc(id, path, **kw):
    return KBDocRecord(id=id, path=path, title=kw.pop("title", path), **kw)


def test_draft_loses_to_active():
    a = doc(1, "leave-v2.md", status="active", effective_date=date(2025, 1, 1))
    b = doc(2, "leave-draft.md", status="draft", effective_date=date(2025, 9, 1))
    res = resolve_by_rules(a, b)
    assert res.winner == "a" and res.rule == "status"


def test_newer_date_wins():
    a = doc(1, "refund-2023.md", status="active", effective_date=date(2023, 1, 15))
    b = doc(2, "refund-2025.md", status="active", effective_date=date(2025, 4, 1))
    res = resolve_by_rules(a, b)
    assert res.winner == "b" and res.rule == "effective_date"
    assert "refund-2023.md" in res.suggested_action


def test_higher_version_wins_for_same_title_without_dates():
    a = doc(1, "a.md", title="Leave Policy", version="2")
    b = doc(2, "b.md", title="Leave Policy", version="1")
    res = resolve_by_rules(a, b)
    assert res.winner == "a" and res.rule == "version"


def test_version_rule_uses_doc_id_when_both_have_one():
    # Same document family, different titles: still versions of one document.
    a = doc(1, "uploads/refund-policy__v3.md", title="Refund Policy 2026", doc_id="refund-policy", version="3")
    b = doc(2, "uploads/refund-policy__v2.md", title="Refund Policy", doc_id="refund-policy", version="2")
    res = resolve_by_rules(a, b)
    assert res.winner == "a" and res.rule == "version"

    # Same title but different documents: the version rule must not compare them.
    c = doc(3, "uploads/faq-india__v5.md", title="FAQ", doc_id="faq-india", version="5")
    d = doc(4, "uploads/faq-uae__v1.md", title="FAQ", doc_id="faq-uae", version="1")
    assert resolve_by_rules(c, d) is None


def test_procedure_ranks_like_policy():
    a = doc(1, "support-faq.md", doc_type="faq")
    b = doc(2, "uploads/refund-steps__v1.md", doc_type="procedure")
    assert resolve_by_rules(a, b).winner == "b"


def test_policy_beats_faq_when_dates_are_missing():
    a = doc(1, "support-faq.md")  # no metadata at all
    b = doc(2, "refund-2025.md", doc_type="policy", effective_date=date(2025, 4, 1))
    res = resolve_by_rules(a, b)
    assert res.winner == "b" and res.rule == "doc_type"


def test_no_rule_decides_between_two_faqs_without_metadata():
    a = doc(1, "support-faq.md")
    b = doc(2, "app-help-faq.md")
    assert resolve_by_rules(a, b) is None


def test_same_document_is_not_decided_by_metadata():
    a = doc(1, "refund-2025.md", doc_type="policy")
    assert resolve_by_rules(a, a) is None
