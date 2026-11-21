import json
from types import SimpleNamespace

from core.judge import extract_json, judge_pair, parse_verdict
from core.records import KBChunkRecord, KBDocRecord

TEXT_A = "Customers in India can request a refund within 7 days of delivery."
TEXT_B = "Customers in India can request a refund within 3 days of delivery."


def _answer(**overrides) -> str:
    data = {
        "label": "contradiction",
        "topic": "refund window",
        "quote_a": "within 7 days of delivery",
        "quote_b": "within 3 days of delivery",
        "confidence": "high",
        "reason": "Same region, different number of days.",
    }
    data.update(overrides)
    return json.dumps(data)


class FakeLLM:
    """Returns prepared answers in order, like a recorded model."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def converse(self, messages, system=None, tools=None, max_tokens=1024):
        self.calls.append(messages)
        return SimpleNamespace(text=self.answers.pop(0), input_tokens=100, output_tokens=20, latency_ms=5)


def _pair():
    doc = KBDocRecord(id=1, path="refund.md", title="Refund Policy")
    return (
        KBChunkRecord(1, 1, 0, "Refund Policy > India", TEXT_A, "h1"),
        doc,
        KBChunkRecord(2, 1, 1, "Refund Policy > India", TEXT_B, "h2"),
        doc,
    )


def test_extract_json_ignores_text_around_it():
    assert extract_json('Sure! {"label": "unrelated"} Hope that helps.') == {"label": "unrelated"}


def test_valid_contradiction_is_accepted_even_with_different_case_and_spaces():
    verdict, problem = parse_verdict(_answer(quote_a="WITHIN 7  days of delivery"), TEXT_A, TEXT_B)
    assert problem == ""
    assert verdict["label"] == "contradiction"


def test_made_up_quote_is_rejected():
    verdict, problem = parse_verdict(_answer(quote_b="within 10 days"), TEXT_A, TEXT_B)
    assert verdict is None
    assert "quote_b" in problem


def test_unknown_label_is_rejected():
    verdict, problem = parse_verdict(_answer(label="conflict"), TEXT_A, TEXT_B)
    assert verdict is None
    assert "label" in problem


def test_non_flag_labels_do_not_need_quotes():
    verdict, problem = parse_verdict(_answer(label="scope_difference", quote_a="", quote_b=""), TEXT_A, TEXT_B)
    assert problem == ""
    assert verdict["label"] == "scope_difference"


def test_judge_retries_once_after_a_bad_answer():
    llm = FakeLLM(["not json at all", _answer()])
    result = judge_pair(llm, *_pair())
    assert result.label == "contradiction"
    assert result.grounded
    assert result.input_tokens == 200  # usage from both calls is counted
    assert len(llm.calls) == 2


def test_judge_returns_unsure_when_answers_stay_invalid():
    llm = FakeLLM([_answer(quote_a="made up"), _answer(quote_a="still made up")])
    result = judge_pair(llm, *_pair())
    assert result.label == "unsure"
    assert not result.grounded
    assert "quote_a" in result.error
