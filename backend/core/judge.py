"""Step 2 of the audit: the LLM judge.

Sends two passages to the model and gets back a label as JSON.
Safety check in plain code: for "contradiction" / "duplicate", the quotes must appear in the
passages. If the model invents a quote, the answer is rejected and we ask once more.

`llm` is anything with converse(messages, system=..., max_tokens=...) returning an object with
.text, .input_tokens, .output_tokens and .latency_ms (BedrockClient, or a fake in tests).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .prompts import JUDGE_SYSTEM, JUDGE_USER_TEMPLATE

LABELS = ("contradiction", "duplicate", "scope_difference", "exception", "same_meaning", "unrelated", "unsure")
FLAG_LABELS = {"contradiction", "duplicate"}
CONFIDENCES = {"high", "medium", "low"}


@dataclass
class JudgeVerdict:
    label: str
    topic: str = ""
    quote_a: str = ""
    quote_b: str = ""
    confidence: str = "low"
    reason: str = ""
    grounded: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str = ""


def extract_json(text: str) -> dict:
    """Take the first {...} block from the model's answer."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("The answer did not contain a JSON object.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"The JSON could not be parsed: {exc.msg}.") from exc
    if not isinstance(data, dict):
        raise ValueError("The JSON was not an object.")
    return data


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def quote_in_text(quote: str, text: str) -> bool:
    q = _norm(quote)
    return bool(q) and q in _norm(text)


def parse_verdict(answer: str, text_a: str, text_b: str) -> tuple[dict | None, str]:
    """Return (clean verdict dict, "") or (None, problem description)."""
    try:
        data = extract_json(answer)
    except ValueError as exc:
        return None, str(exc)

    label = str(data.get("label", "")).strip().lower()
    if label not in LABELS:
        return None, f'"label" must be one of: {", ".join(LABELS)}.'

    confidence = str(data.get("confidence", "")).strip().lower()
    verdict = {
        "label": label,
        "topic": str(data.get("topic") or "").strip()[:300],
        "quote_a": str(data.get("quote_a") or "").strip(),
        "quote_b": str(data.get("quote_b") or "").strip(),
        "confidence": confidence if confidence in CONFIDENCES else "low",
        "reason": str(data.get("reason") or "").strip()[:1000],
    }
    if label in FLAG_LABELS:
        if not quote_in_text(verdict["quote_a"], text_a):
            return None, "quote_a is not an exact copy of text from Passage A."
        if not quote_in_text(verdict["quote_b"], text_b):
            return None, "quote_b is not an exact copy of text from Passage B."
    return verdict, ""


def _scope(doc) -> str:
    """Region / audience of the document, when set: they decide whether two values really conflict."""
    parts = [f"{label}: {value}" for label, value in (("region", getattr(doc, "region", "")), ("audience", getattr(doc, "audience", ""))) if value]
    return (" | " + " | ".join(parts)) if parts else ""


def format_passage(name: str, chunk, doc, with_ids: bool = False) -> str:
    ids = f" (chunk_id={chunk.id}, doc_id={doc.id})" if with_ids else ""
    return (
        f"Passage {name}{ids}\n"
        f"Document: {doc.title} ({doc.path}) | type: {doc.doc_type or 'unknown'} | "
        f"version: {doc.version or 'unknown'} | effective date: {doc.effective_date or 'unknown'} | "
        f"status: {doc.status or 'unknown'}{_scope(doc)}\n"
        f"Section: {chunk.heading_path or '(none)'}\n"
        f'Text:\n"""\n{chunk.text}\n"""'
    )


def judge_pair(llm, chunk_a, doc_a, chunk_b, doc_b, max_retries: int = 1) -> JudgeVerdict:
    user = JUDGE_USER_TEMPLATE.format(
        passage_a=format_passage("A", chunk_a, doc_a),
        passage_b=format_passage("B", chunk_b, doc_b),
    )
    messages = [{"role": "user", "content": [{"text": user}]}]
    totals = {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
    problem = ""

    for _ in range(max_retries + 1):
        result = llm.converse(messages, system=JUDGE_SYSTEM, max_tokens=600)
        totals["input_tokens"] += result.input_tokens
        totals["output_tokens"] += result.output_tokens
        totals["latency_ms"] += result.latency_ms

        verdict, problem = parse_verdict(result.text, chunk_a.text, chunk_b.text)
        if verdict:
            return JudgeVerdict(**verdict, grounded=True, **totals)

        # Tell the model what was wrong and ask once more.
        messages.append({"role": "assistant", "content": [{"text": result.text or "(empty answer)"}]})
        messages.append(
            {"role": "user", "content": [{"text": f"Your answer had a problem: {problem} Reply again with only the corrected JSON object."}]}
        )

    return JudgeVerdict(label="unsure", reason="The judge did not give a valid answer.", grounded=False, error=problem, **totals)
