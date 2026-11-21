"""Compare one audit run with the answer key (data/ground_truth.yaml).

Measured per stage, so a bad result can be traced to the stage that caused it:
- candidate_recall: did the planted pair even reach the judge? (if not, no judge can fix it)
- recall:           planted conflicts/duplicates that ended up as findings
- precision:        findings that are correct (false alarms on traps and unexpected findings count against it)
- trap_false_alarm_rate: traps that were wrongly reported
- resolver_accuracy:     contradictions where the suggested source of truth was right
"""
from __future__ import annotations

from pathlib import Path

import yaml

from core.config import load_config
from core.kb_adapter import PgvectorKBAdapter

from .models import AuditRun, EvalResult, Verdict

POSITIVE_KINDS = {"contradiction", "duplicate"}


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def load_ground_truth(path: Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("items", [])


def evaluate_run(run: AuditRun, items: list[dict], adapter) -> tuple[dict, list[dict]]:
    docs = {d.id: d for d in adapter.list_documents()}
    chunks = adapter.list_chunks()
    path_of = {doc_id: doc.path for doc_id, doc in docs.items()}

    def locate(doc_path: str, snippet: str) -> int | None:
        wanted = _norm(snippet)
        for chunk in chunks:
            if path_of.get(chunk.doc_id) == doc_path and wanted in _norm(chunk.text):
                return chunk.id
        return None

    pairs = {(p.chunk_a_id, p.chunk_b_id) for p in run.pairs.all()}
    findings = {(f.chunk_a_id, f.chunk_b_id): f for f in run.findings.all()}
    final_labels = {
        (v.pair.chunk_a_id, v.pair.chunk_b_id): v.label
        for v in Verdict.objects.filter(pair__run=run, is_final=True).select_related("pair").order_by("id")
    }

    details: list[dict] = []
    matched: set[tuple[int, int]] = set()
    trap_keys: set[tuple[int, int]] = set()
    c = {"positives": 0, "reached": 0, "found": 0, "traps": 0, "traps_reached": 0, "false_alarms": 0,
         "resolver_total": 0, "resolver_ok": 0, "setup_errors": 0}

    for item in items:
        entry = {"id": item["id"], "kind": item["kind"], "topic": item.get("topic", "")}
        a = locate(item["doc_a"], item["text_a"])
        b = locate(item["doc_b"], item["text_b"])
        if a is None or b is None:
            c["setup_errors"] += 1
            entry.update(outcome="setup_error", note="Passage text not found in the KB. Check ground_truth.yaml or re-seed.")
            details.append(entry)
            continue

        key = (min(a, b), max(a, b))
        reached = key in pairs
        finding = findings.get(key)
        entry.update(reached_judge=reached, final_label=final_labels.get(key))

        if item["kind"] in POSITIVE_KINDS:
            c["positives"] += 1
            c["reached"] += reached
            if finding and finding.kind == item["kind"]:
                c["found"] += 1
                matched.add(key)
                entry["outcome"] = "found"
                if item["kind"] == "contradiction" and "expected_truth" in item:
                    c["resolver_total"] += 1
                    expected = item["expected_truth"]
                    if expected is None:
                        ok = finding.suggested_truth == ""
                    else:
                        side = "a" if finding.doc_a_path == expected else ("b" if finding.doc_b_path == expected else None)
                        ok = side is not None and finding.suggested_truth == side
                    c["resolver_ok"] += ok
                    entry["resolver_correct"] = ok
            elif finding:
                entry["outcome"] = "wrong_kind"
                entry["note"] = f"Reported as {finding.kind}, expected {item['kind']}."
            else:
                entry["outcome"] = "missed_by_judge" if reached else "missed_not_a_candidate"
        else:  # trap
            c["traps"] += 1
            c["traps_reached"] += reached
            trap_keys.add(key)
            if finding:
                c["false_alarms"] += 1
                entry["outcome"] = "false_alarm"
            else:
                entry["outcome"] = "correctly_ignored"
        details.append(entry)

    unexpected = [key for key in findings if key not in matched and key not in trap_keys]
    for key in unexpected:
        f = findings[key]
        details.append({
            "id": f"finding-{f.id}",
            "kind": f.kind,
            "topic": f.topic,
            "outcome": "unexpected_finding",
            "note": f"{f.doc_a_path} vs {f.doc_b_path}: not in the answer key. Review it; it may be a real problem we did not plant.",
        })

    false_positives = c["false_alarms"] + len(unexpected)
    duration = (run.finished_at - run.started_at).total_seconds() if run.finished_at and run.started_at else None
    totals = run.totals or {}
    metrics = {
        "positives": c["positives"],
        "candidate_recall": _ratio(c["reached"], c["positives"]),
        "recall": _ratio(c["found"], c["positives"]),
        "precision": _ratio(c["found"], c["found"] + false_positives),
        "traps": c["traps"],
        "traps_reached_judge": c["traps_reached"],
        "trap_false_alarm_rate": _ratio(c["false_alarms"], c["traps"]),
        "unexpected_findings": len(unexpected),
        "resolver_accuracy": _ratio(c["resolver_ok"], c["resolver_total"]),
        "setup_errors": c["setup_errors"],
        "findings_total": len(findings),
        "cost_usd": totals.get("cost_usd"),
        "input_tokens": totals.get("input_tokens"),
        "output_tokens": totals.get("output_tokens"),
        "duration_seconds": round(duration, 1) if duration is not None else None,
    }
    return metrics, details


def evaluate_and_save(run: AuditRun, truth_path: Path) -> EvalResult:
    items = load_ground_truth(truth_path)
    adapter = PgvectorKBAdapter(load_config().kb_database_url)
    try:
        metrics, details = evaluate_run(run, items, adapter)
    finally:
        adapter.close()
    return EvalResult.objects.create(run=run, metrics=metrics, details=details)
