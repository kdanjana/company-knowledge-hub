"""Runs one audit end to end and saves every step in the database.

Candidate pairs:
  similar pairs from the vector index, only if one person can see both documents (top-k, capped)
  + pairs that still have an open/confirmed finding from the previous audit (always re-checked)

Flow per candidate pair:
  latest reviewer decision is "not a conflict"? -> skip
  exact same text?               -> "duplicate" by rule (no LLM)
  judged before (same content, prompt version and model)? -> reuse cached verdict (no LLM)
  otherwise                      -> LLM judge
  judge says "unsure"            -> investigator agent
  final label is contradiction/duplicate -> Finding (+ resolver for contradictions)

A new Finding also gets, from earlier runs of the same pair (same texts):
  the reviewer's decision (confirmed + chosen winner, or ignored), and which texts are "new" (held)

At the end: findings of the previous audit that disappeared are marked "fixed", and the
new documents are published (kb.services.publish_after_audit).
"""
from __future__ import annotations

import traceback
from typing import Callable

from django.db import connection, transaction
from django.utils import timezone

from core.bedrock_client import BedrockClient
from core.candidates import Candidate, audiences_overlap, build_candidate, generate_candidates, pair_key
from core.chunking import sha256
from core.config import Config, load_config
from core.investigator import run_investigation
from core.judge import FLAG_LABELS, judge_pair
from core.kb_adapter import PgvectorKBAdapter
from core.prompts import JUDGE_PROMPT_VERSION, RESOLVER_PROMPT_VERSION
from core.resolver import Resolution, resolve_by_rules, resolve_with_llm
from kb.services import documents_awaiting_audit, publish_after_audit

from .models import AuditRun, CandidatePair, Feedback, Finding, Investigation, Verdict

QUEUE_LOCK_ID = 7_310_001  # any fixed number: identifies the "queue an audit" lock in Postgres


def queue_audit() -> AuditRun | None:
    """Queue one audit after the knowledge base changed (upload processed, seed_kb).

    - an audit is already QUEUED  -> nothing to do: it has not started yet, so it will see the change
    - an audit is only RUNNING    -> queue one more: the running one may have started before the change
    The advisory lock makes this check-then-create safe when two processes call it at the same time.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [QUEUE_LOCK_ID])
        if AuditRun.objects.filter(status=AuditRun.Status.QUEUED).exists():
            return None
        return AuditRun.objects.create(status=AuditRun.Status.QUEUED)


def _cache_key(pkey: str, model_id: str) -> str:
    return sha256(f"{pkey}|{JUDGE_PROMPT_VERSION}|{model_id}")


def _excerpt(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _new_totals() -> dict:
    return {
        "chunks": 0,
        "candidates": 0,
        "pairs_found": 0,
        "pairs_cut_by_cap": 0,
        "pairs_skipped_no_shared_audience": 0,
        "pairs_rechecked": 0,
        "skipped_by_feedback": 0,
        "decisions_reused": 0,
        "resolver_from_cache": 0,
        "findings_fixed": 0,
        "decided_by_rule": 0,
        "from_cache": 0,
        "judged": 0,
        "investigated": 0,
        "findings": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def _add_usage(totals: dict, llm: BedrockClient, input_tokens: int, output_tokens: int) -> float:
    cost = llm.cost(input_tokens, output_tokens)
    totals["input_tokens"] += input_tokens
    totals["output_tokens"] += output_tokens
    totals["cost_usd"] = round(totals["cost_usd"] + cost, 6)
    return cost


def _save_progress(run: AuditRun, stage: str, done: int, total: int, totals: dict) -> None:
    run.progress = {"stage": stage, "pairs_done": done, "pairs_total": total}
    run.totals = totals
    run.save(update_fields=["progress", "totals"])


# ---------- carrying knowledge from earlier audits ----------


def latest_decisions() -> dict[str, Feedback]:
    """The most recent reviewer decision per pair of texts (pair_key). A newer decision replaces an older one."""
    decisions: dict[str, Feedback] = {}
    for fb in Feedback.objects.order_by("id"):
        decisions[fb.pair_key] = fb
    return decisions


def recheck_candidates(previous_run: AuditRun | None, chunks: list, already: set, allowed=None) -> list[Candidate]:
    """Pairs with an open or confirmed finding in the previous audit are always checked again, even if the
    top-k / cap would leave them out this time. Otherwise a held text could silently become usable.
    The verdict usually comes from the cache, so this costs nothing."""
    if previous_run is None:
        return []
    by_hash: dict[str, object] = {}
    for chunk in chunks:
        by_hash.setdefault(chunk.content_hash, chunk)
    found: list[Candidate] = []
    seen = set(already)
    open_findings = Finding.objects.filter(
        run=previous_run, status__in=[Finding.Status.OPEN, Finding.Status.CONFIRMED]
    ).exclude(hash_a="")
    for f in open_findings:
        chunk_a, chunk_b = by_hash.get(f.hash_a), by_hash.get(f.hash_b)
        if chunk_a is None or chunk_b is None or chunk_a.id == chunk_b.id:
            continue  # a text no longer exists: the finding will be marked fixed
        key = (min(chunk_a.id, chunk_b.id), max(chunk_a.id, chunk_b.id))
        if key in seen or (allowed is not None and not allowed(chunk_a, chunk_b)):
            continue
        seen.add(key)
        candidate = build_candidate(chunk_a, chunk_b, similarity=0.0)
        candidate.reasons.append("re-checked: open finding in the previous audit")
        found.append(candidate)
    return found


def mark_fixed(run: AuditRun, previous_run: AuditRun | None) -> int:
    """Open/confirmed findings of the previous audit that this audit did not report again are fixed:
    the pair was re-checked and is no longer a problem, or one of its texts does not exist any more."""
    if previous_run is None:
        return 0
    still_there = set(run.findings.values_list("pair_key", flat=True))
    return (
        Finding.objects.filter(run=previous_run, status__in=[Finding.Status.OPEN, Finding.Status.CONFIRMED])
        .exclude(pair_key__in=still_there)
        .update(status=Finding.Status.FIXED)
    )


def new_hashes_for(pkey: str, run: AuditRun, sides: list[tuple[str, int]], new_doc_ids: set[int]) -> list[str]:
    """Which texts of this pair are "new" (held until an auditor decides).
    If the pair was reported before, keep what was decided then: a held text stays held even after its
    document went live, until an auditor decides. Otherwise: the texts from documents new in this audit."""
    previous = Finding.objects.filter(pair_key=pkey).exclude(run=run).order_by("-id").first()
    if previous is not None:
        return list(previous.new_hashes or [])
    return [text_hash for text_hash, doc_id in sides if doc_id in new_doc_ids]


def _resolution_key(chunk_a, doc_a, chunk_b, doc_b, model_id: str) -> str:
    """Cache key for an LLM resolver answer: both texts, their document details, prompt version and model.
    Sides are sorted by text fingerprint, so the key does not depend on the a/b order."""
    sides = sorted(
        (
            chunk.content_hash,
            f"{doc.title}|{doc.doc_type}|{doc.version}|{doc.effective_date}|{doc.status}",
        )
        for chunk, doc in ((chunk_a, doc_a), (chunk_b, doc_b))
    )
    return sha256(f"{sides}|{RESOLVER_PROMPT_VERSION}|{model_id}")


def _cached_resolution(key: str, hash_a: str, hash_b: str) -> Resolution | None:
    previous = Finding.objects.filter(resolution_key=key).order_by("-id").first()
    if previous is None:
        return None
    winner_hash = {"a": previous.hash_a, "b": previous.hash_b}.get(previous.suggested_truth, "")
    winner = "a" if winner_hash and winner_hash == hash_a else ("b" if winner_hash and winner_hash == hash_b else "")
    return Resolution(
        winner=winner,
        rule=previous.resolution_rule,
        reason=previous.resolution_reason,
        suggested_action=previous.suggested_action,
    )


def accept_new_version(doc_id: int, chunk_hashes: set[str]) -> int:
    """An auditor approved a new version: in the latest audit, every open conflict between one of its texts
    and another text is decided in favour of the new version. Its held texts become usable."""
    run = AuditRun.objects.filter(status=AuditRun.Status.DONE).order_by("-id").first()
    if run is None or not chunk_hashes:
        return 0
    decided = 0
    findings = Finding.objects.filter(
        run=run, kind=Finding.Kind.CONTRADICTION, status__in=[Finding.Status.OPEN, Finding.Status.CONFIRMED]
    )
    for f in findings:
        new_side = [h for h in (f.hash_a, f.hash_b) if h in chunk_hashes]
        if len(new_side) != 1 or f.winner_hash:
            continue
        Feedback.objects.create(
            finding=f,
            pair_key=f.pair_key,
            decision=Feedback.Decision.REAL,
            winner_hash=new_side[0],
            note=f"New version (document {doc_id}) approved by an auditor.",
        )
        f.status = Finding.Status.CONFIRMED
        f.winner_hash = new_side[0]
        f.save(update_fields=["status", "winner_hash"])
        decided += 1
    return decided


def execute_run(run: AuditRun, log: Callable[[str], None] = print) -> AuditRun:
    cfg = load_config()
    totals = _new_totals()
    run.status = AuditRun.Status.RUNNING
    run.started_at = timezone.now()
    run.model_id = cfg.chat_model_id
    run.prompt_version = JUDGE_PROMPT_VERSION
    run.settings = {
        "top_k": cfg.top_k,
        "min_similarity": cfg.min_similarity,
        "max_pairs": cfg.max_pairs,
        "investigator_max_steps": cfg.investigator_max_steps,
        "embed_model_id": cfg.embed_model_id,
    }
    run.error = ""
    run.save()

    # The new documents this audit checks. Uploads that finish processing during the run are
    # not in this list; they stay "awaiting audit" and the next (already queued) run checks them.
    new_doc_ids = documents_awaiting_audit()
    totals["new_documents"] = len(new_doc_ids)

    previous_run = AuditRun.objects.filter(status=AuditRun.Status.DONE, id__lt=run.id).order_by("-id").first()

    adapter = None
    try:
        cfg.require_bedrock()
        llm = BedrockClient(cfg)
        adapter = PgvectorKBAdapter(cfg.kb_database_url)

        docs = {d.id: d for d in adapter.list_documents()}
        chunks = adapter.list_chunks()
        if not chunks:
            raise RuntimeError("The knowledge base is empty. Run: python manage.py seed_kb")
        chunk_by_id = {c.id: c for c in chunks}
        totals["chunks"] = len(chunks)

        def can_compare(chunk_a, chunk_b) -> bool:
            # Compare two passages only if at least one person can see both documents.
            return audiences_overlap(docs[chunk_a.doc_id].departments, docs[chunk_b.doc_id].departments)

        _save_progress(run, "finding candidate pairs", 0, 0, totals)
        stats: dict = {}
        candidates = generate_candidates(
            adapter, chunks, cfg.top_k, cfg.min_similarity, cfg.max_pairs, allowed=can_compare, stats=stats
        )
        totals.update(stats)
        rechecked = recheck_candidates(previous_run, chunks, {(c.a_id, c.b_id) for c in candidates}, can_compare)
        totals["pairs_rechecked"] = len(rechecked)
        candidates += rechecked
        totals["candidates"] = len(candidates)
        log(
            f"{len(chunks)} chunks -> {len(candidates)} candidate pairs "
            f"({stats['pairs_cut_by_cap']} cut by the cap, {len(rechecked)} re-checked from the previous audit)"
        )

        decisions = latest_decisions()

        for i, cand in enumerate(candidates, start=1):
            chunk_a, chunk_b = chunk_by_id[cand.a_id], chunk_by_id[cand.b_id]
            doc_a, doc_b = docs[chunk_a.doc_id], docs[chunk_b.doc_id]
            pkey = pair_key(chunk_a.content_hash, chunk_b.content_hash)
            pair = CandidatePair.objects.create(
                run=run,
                chunk_a_id=chunk_a.id,
                chunk_b_id=chunk_b.id,
                doc_a_id=doc_a.id,
                doc_b_id=doc_b.id,
                pair_key=pkey,
                similarity=cand.similarity,
                exact_duplicate=cand.exact_duplicate,
                numbers_differ=cand.numbers_differ,
                reasons=cand.reasons,
            )

            decision = decisions.get(pkey)
            if decision is not None and decision.decision == Feedback.Decision.NOT_CONFLICT:
                pair.skipped_reason = "A reviewer marked this pair as not a conflict."
                pair.save(update_fields=["skipped_reason"])
                totals["skipped_by_feedback"] += 1
            else:
                final = _decide(pair, cand, chunk_a, doc_a, chunk_b, doc_b, llm, adapter, cfg, totals)
                if final.label in FLAG_LABELS:
                    _create_finding(
                        run, pair, final, chunk_a, doc_a, chunk_b, doc_b, llm, cfg, totals,
                        decision=decision, new_doc_ids=set(new_doc_ids),
                    )
                    totals["findings"] += 1

            if i % 5 == 0 or i == len(candidates):
                _save_progress(run, "judging pairs", i, len(candidates), totals)
                log(f"  {i}/{len(candidates)} pairs, {totals['findings']} findings, ${totals['cost_usd']:.4f}")

        totals["findings_fixed"] = mark_fixed(run, previous_run)
        # Only after a successful audit: new documents go live, new versions wait for an auditor.
        totals.update(publish_after_audit(new_doc_ids))
        run.status = AuditRun.Status.DONE
        run.progress = {**run.progress, "stage": "done"}
    except Exception as exc:
        run.status = AuditRun.Status.FAILED
        run.error = f"{exc}\n\n{traceback.format_exc()}"
        log(f"Run failed: {exc}")
    finally:
        if adapter is not None:
            adapter.close()
        run.totals = totals
        run.finished_at = timezone.now()
        run.save()
    return run


def _decide(pair, cand, chunk_a, doc_a, chunk_b, doc_b, llm: BedrockClient, adapter, cfg: Config, totals: dict) -> Verdict:
    base = {"pair": pair, "model_id": cfg.chat_model_id, "prompt_version": JUDGE_PROMPT_VERSION}

    # 1. Exact same text: no LLM needed.
    if cand.exact_duplicate:
        totals["decided_by_rule"] += 1
        return Verdict.objects.create(
            **base,
            stage=Verdict.Stage.RULE,
            label="duplicate",
            topic=chunk_a.heading_path.split(" > ")[-1],
            quote_a=_excerpt(chunk_a.text),
            quote_b=_excerpt(chunk_b.text),
            confidence="high",
            reason="The two passages have the same text.",
            grounded=True,
            is_final=True,
        )

    # 2. Same pair judged before with the same prompt and model: reuse it.
    ckey = _cache_key(pair.pair_key, cfg.chat_model_id)
    cached = Verdict.objects.filter(cache_key=ckey, is_final=True).order_by("-id").first()
    if cached:
        totals["from_cache"] += 1
        return Verdict.objects.create(
            **base,
            stage=Verdict.Stage.CACHE,
            label=cached.label,
            topic=cached.topic,
            quote_a=cached.quote_a,
            quote_b=cached.quote_b,
            confidence=cached.confidence,
            reason=cached.reason,
            grounded=cached.grounded,
            is_final=True,
            cache_key=ckey,
        )

    # 3. LLM judge.
    totals["judged"] += 1
    jv = judge_pair(llm, chunk_a, doc_a, chunk_b, doc_b)
    cost = _add_usage(totals, llm, jv.input_tokens, jv.output_tokens)
    judge_is_final = jv.label != "unsure"
    verdict = Verdict.objects.create(
        **base,
        stage=Verdict.Stage.JUDGE,
        label=jv.label,
        topic=jv.topic,
        quote_a=jv.quote_a,
        quote_b=jv.quote_b,
        confidence=jv.confidence,
        reason=jv.reason,
        grounded=jv.grounded,
        is_final=judge_is_final,
        cache_key=ckey if judge_is_final else "",
        input_tokens=jv.input_tokens,
        output_tokens=jv.output_tokens,
        latency_ms=jv.latency_ms,
        cost_usd=cost,
        error=jv.error,
    )
    if judge_is_final:
        return verdict

    # 4. Unsure: send to the investigator agent.
    totals["investigated"] += 1
    try:
        result = run_investigation(
            llm, adapter, llm.embed, chunk_a, doc_a, chunk_b, doc_b,
            judge_reason=jv.reason or jv.error, max_steps=cfg.investigator_max_steps,
        )
    except Exception as exc:  # e.g. the chosen model does not support tool use
        Investigation.objects.create(pair=pair, final_label="unsure", error=f"{exc}")
        verdict.is_final = True
        verdict.save(update_fields=["is_final"])
        return verdict

    cost = _add_usage(totals, llm, result.input_tokens, result.output_tokens)
    Investigation.objects.create(
        pair=pair,
        steps=result.steps,
        final_label=result.final["label"],
        tool_calls=result.tool_calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        error=result.error,
    )
    final = result.final
    return Verdict.objects.create(
        **base,
        stage=Verdict.Stage.INVESTIGATOR,
        label=final["label"],
        topic=final.get("topic", ""),
        quote_a=final.get("quote_a", ""),
        quote_b=final.get("quote_b", ""),
        confidence=final.get("confidence", "low"),
        reason=final.get("reason", ""),
        grounded=result.grounded,
        is_final=True,
        cache_key=ckey,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost,
        error=result.error,
    )


def _create_finding(
    run, pair, verdict: Verdict, chunk_a, doc_a, chunk_b, doc_b, llm: BedrockClient, cfg: Config, totals: dict,
    decision: Feedback | None = None, new_doc_ids: set[int] | None = None,
) -> Finding:
    kind = verdict.label
    resolution_key = ""
    if kind == Finding.Kind.DUPLICATE:
        severity = Finding.Severity.INFO
        resolution = {
            "suggested_truth": "",
            "resolution_rule": "",
            "resolution_reason": "",
            "suggested_action": f"Keep the text in one place and link to it from the other document "
            f"('{doc_a.path}' / '{doc_b.path}').",
        }
    else:
        severity = Finding.Severity.HIGH if "policy" in {doc_a.doc_type, doc_b.doc_type} else Finding.Severity.MEDIUM
        res = resolve_by_rules(doc_a, doc_b)
        if res is None:
            # The LLM resolver is paid per call: reuse an earlier answer for the same texts and document details.
            resolution_key = _resolution_key(chunk_a, doc_a, chunk_b, doc_b, cfg.chat_model_id)
            res = _cached_resolution(resolution_key, chunk_a.content_hash, chunk_b.content_hash)
            if res is not None:
                totals["resolver_from_cache"] += 1
            else:
                res, (in_tokens, out_tokens) = resolve_with_llm(llm, verdict.topic, chunk_a, doc_a, chunk_b, doc_b)
                _add_usage(totals, llm, in_tokens, out_tokens)
        resolution = {
            "suggested_truth": res.winner,
            "resolution_rule": res.rule,
            "resolution_reason": res.reason,
            "suggested_action": res.suggested_action,
        }

    # Reuse the reviewer's latest decision for the same texts.
    status, winner_hash = Finding.Status.OPEN, ""
    if decision is not None:
        totals["decisions_reused"] += 1
        if decision.decision == Feedback.Decision.REAL:
            status = Finding.Status.CONFIRMED
            if decision.winner_hash in (chunk_a.content_hash, chunk_b.content_hash):
                winner_hash = decision.winner_hash
        elif decision.decision == Feedback.Decision.IGNORE:
            status = Finding.Status.IGNORED

    new_hashes = new_hashes_for(
        pair.pair_key, run, [(chunk_a.content_hash, doc_a.id), (chunk_b.content_hash, doc_b.id)], new_doc_ids or set()
    )

    return Finding.objects.create(
        status=status,
        winner_hash=winner_hash,
        new_hashes=new_hashes,
        hash_a=chunk_a.content_hash,
        hash_b=chunk_b.content_hash,
        owner_a=doc_a.owner,
        owner_b=doc_b.owner,
        resolution_key=resolution_key,
        run=run,
        pair=pair,
        kind=kind,
        severity=severity,
        topic=verdict.topic,
        chunk_a_id=chunk_a.id,
        chunk_b_id=chunk_b.id,
        doc_a_path=doc_a.path,
        doc_b_path=doc_b.path,
        quote_a=verdict.quote_a,
        quote_b=verdict.quote_b,
        reason=verdict.reason,
        pair_key=pair.pair_key,
        **resolution,
    )
