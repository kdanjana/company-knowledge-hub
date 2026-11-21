"""Answer one chat question and stream the answer as JSON lines (NDJSON).

Events sent to the browser, one JSON object per line:
  {"type": "context", "passages": 3, "warnings": [...], "dropped": [...]}
  {"type": "delta", "text": "..."}            (many)
  {"type": "error", "message": "..."}         (only if something failed)
  {"type": "done", "message": {...saved assistant message...}}

Errors: the user only sees a safe message with a reference (the request ID). The real error, with its stack
trace, goes to the server log under the same ID. One JSON log line per answer records the time per step.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterator

from botocore.exceptions import ClientError
from django.db.models import Q

from accounts.roles import allowed_departments
from audit.models import AuditRun, Finding
from config.observability import current_request_id, new_request_id
from core.bedrock_client import BedrockClient, BedrockError
from core.chat import (
    NO_ANSWER,
    ConflictIndex,
    FindingFacts,
    build_messages,
    conflict_index_from,
    conflict_warnings,
    extract_citations,
    select_passages,
)
from core.config import load_config
from core.kb_adapter import PgvectorKBAdapter
from core.prompts import CHAT_PROMPT_VERSION, CHAT_SYSTEM

from .models import Conversation, Message
from .serializers import MessageSerializer

HISTORY_MESSAGES = 6  # earlier turns sent to the model, so follow-up questions work

logger = logging.getLogger(__name__)

# Bedrock error codes that mean "too many requests, try later" (before the stream, and inside it).
THROTTLE_CODES = {"ThrottlingException", "throttlingException", "ServiceQuotaExceededException"}


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str) + "\n"


def _ms_since(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _is_throttled(exc: Exception) -> bool:
    if isinstance(exc, ClientError):  # Bedrock refused the request (after boto3's own retries)
        return exc.response.get("Error", {}).get("Code", "") in THROTTLE_CODES
    if isinstance(exc, BedrockError):  # error inside the stream: "throttlingException: ..."
        return str(exc).split(":", 1)[0] in THROTTLE_CODES
    return False


def safe_error_message(exc: Exception, reference: str) -> str:
    """What the user sees. Never the exception text: it can contain internal details."""
    if _is_throttled(exc):
        return f"the AI service is busy right now, please try again in a minute (reference: {reference})"
    return f"an internal error happened (reference: {reference})"


def build_conflict_index(chunk_ids=None) -> ConflictIndex:
    """Chat rules from the latest finished audit (see core/chat.py: conflict_index_from).
    Findings a reviewer marked 'not a conflict' or 'ignore', and fixed ones, are not used.

    chunk_ids: only findings that involve one of these chunks (the chunks the search found). The rules are only
    ever looked up for those chunks, so the result for them is the same as loading every finding.
    None = every finding of the run."""
    run = AuditRun.objects.filter(status=AuditRun.Status.DONE).order_by("-id").first()
    if run is None:
        return ConflictIndex()
    findings = Finding.objects.filter(
        run=run,
        kind=Finding.Kind.CONTRADICTION,
        status__in=[Finding.Status.OPEN, Finding.Status.CONFIRMED],
    )
    if chunk_ids is not None:
        ids = list(chunk_ids)
        findings = findings.filter(Q(chunk_a_id__in=ids) | Q(chunk_b_id__in=ids))
    return conflict_index_from([
        FindingFacts(
            chunk_a_id=f.chunk_a_id,
            chunk_b_id=f.chunk_b_id,
            hash_a=f.hash_a,
            hash_b=f.hash_b,
            doc_a_path=f.doc_a_path,
            doc_b_path=f.doc_b_path,
            topic=f.topic,
            kind=f.kind,
            status=f.status,
            suggested_truth=f.suggested_truth,
            resolution_rule=f.resolution_rule,
            new_hashes=list(f.new_hashes or []),
            winner_hash=f.winner_hash,
        )
        for f in findings
    ])


def stream_reply(user, conversation: Conversation, question: str, request_id: str = "") -> Iterator[str]:
    # The view passes the request ID: this generator runs while the answer streams, after the view returned.
    request_id = request_id or current_request_id() or new_request_id()
    started = time.monotonic()
    timings: dict[str, int] = {}
    passage_count = 0
    history = [
        (m.role, m.content)
        for m in reversed(list(conversation.messages.filter(error="").order_by("-id")[:HISTORY_MESSAGES]))
    ]
    Message.objects.create(conversation=conversation, role=Message.Role.USER, content=question)
    if not conversation.title:
        conversation.title = question[:80]
    conversation.save()  # also bumps updated_at

    cfg = load_config()
    assistant = Message(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        model_id=cfg.chat_model_id,
        prompt_version=CHAT_PROMPT_VERSION,
    )
    parts: list[str] = []
    try:
        cfg.require_bedrock()
        llm = BedrockClient(cfg)

        # 1. Retrieve only from documents this user may see.
        adapter = PgvectorKBAdapter(cfg.kb_database_url)
        try:
            step = time.monotonic()
            vector = llm.embed(question, purpose="query")
            timings["embed_ms"] = _ms_since(step)
            step = time.monotonic()
            hits = adapter.search_by_vector(vector, k=cfg.chat_top_k * 3, departments=allowed_departments(user))
            # Details (title, date, version, status) of only the documents the search found, not all documents.
            docs = {d.id: d for d in adapter.get_documents({chunk.doc_id for chunk, _ in hits})}
            timings["search_ms"] = _ms_since(step)
        finally:
            adapter.close()

        # 2. Use the audit results: skip outdated passages, warn about unresolved conflicts.
        #    Only findings that involve the chunks found are loaded.
        step = time.monotonic()
        conflicts = build_conflict_index({chunk.id for chunk, _ in hits})
        passages, dropped = select_passages(hits, docs, conflicts, cfg.chat_top_k, cfg.chat_min_similarity)
        timings["conflicts_ms"] = _ms_since(step)
        passage_count = len(passages)
        warnings = conflict_warnings(passages, conflicts)
        assistant.warnings, assistant.dropped = warnings, dropped
        yield _line({"type": "context", "passages": len(passages), "warnings": warnings, "dropped": dropped})

        # 3. Answer (streamed), or say "not found" without calling the model.
        if not passages:
            parts.append(NO_ANSWER)
            yield _line({"type": "delta", "text": NO_ANSWER})
        else:
            messages = build_messages(history, passages, question)
            step = time.monotonic()
            for event in llm.converse_stream(messages, system=CHAT_SYSTEM, max_tokens=800):
                if event["type"] == "text":
                    parts.append(event["text"])
                    yield _line({"type": "delta", "text": event["text"]})
                elif event["type"] == "usage":
                    assistant.input_tokens = event["input_tokens"]
                    assistant.output_tokens = event["output_tokens"]
                    assistant.latency_ms = event["latency_ms"]
            timings["llm_ms"] = _ms_since(step)
            assistant.cost_usd = llm.cost(assistant.input_tokens, assistant.output_tokens)

            # 4. Check the citations.
            answer = "".join(parts)
            citations, invalid = extract_citations(answer, passages)
            assistant.citations = citations
            if invalid:
                assistant.warnings = assistant.warnings + [
                    f"The answer refers to source number(s) {invalid} that do not exist. Ignore those references."
                ]
            if not citations and "could not find" not in answer.lower():
                assistant.warnings = assistant.warnings + ["This answer does not cite any document. Treat it with care."]
    except Exception as exc:
        # Full details (with stack trace) only in the server log; the user gets a safe message + reference.
        logger.exception(
            "chat answer failed",
            extra={"request_id": request_id, "fields": {"user_id": user.pk, "conversation_id": conversation.id}},
        )
        assistant.error = safe_error_message(exc, request_id)
        yield _line({"type": "error", "message": f"The assistant could not answer: {assistant.error}"})

    assistant.content = "".join(parts)
    assistant.save()
    logger.info(
        "chat answer",
        extra={
            "request_id": request_id,
            "fields": {
                "user_id": user.pk,
                "conversation_id": conversation.id,
                "message_id": assistant.id,
                "passages": passage_count,
                "dropped": len(assistant.dropped),
                "warnings": len(assistant.warnings),
                "input_tokens": assistant.input_tokens,
                "output_tokens": assistant.output_tokens,
                "cost_usd": assistant.cost_usd,
                **timings,
                "total_ms": _ms_since(started),
                "failed": bool(assistant.error),
            },
        },
    )
    yield _line({"type": "done", "message": MessageSerializer(assistant).data})
