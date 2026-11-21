"""Worker side of uploads: processing, publishing after an audit, queueing audits.
Uses a fake embedder, so no AWS calls. Needs the database (Docker Postgres)."""
import pytest
from django.conf import settings

from accounts.roles import EMPLOYEE
from audit.models import AuditRun
from audit.services import queue_audit
from kb.models import KBDocument
from kb.services import (
    UploadFields,
    claim_and_process_next,
    documents_awaiting_audit,
    process_document,
    publish_after_audit,
    save_upload,
)
from tests.test_permissions import make_user

DOC = b"""# Refund update

## Refund window
Customers can request a refund within 10 days.

## How to request
Open the app, tap Help, choose Request a refund.
"""
FIELDS = UploadFields(
    title="Refund update", doc_id="refund-update", doc_type="policy",
    effective_date="2026-01-10", status="active", owner="Customer Support",
)


def fake_embed(text: str) -> list[float]:
    return [0.1] * settings.EMBED_DIM


def broken_embed(text: str) -> list[float]:
    raise RuntimeError("Bedrock is throttling")


@pytest.fixture(autouse=True)
def upload_dir(settings, tmp_path):
    settings.UPLOAD_DIR = tmp_path


@pytest.fixture
def uploaded():
    return save_upload(make_user("emp", EMPLOYEE, "Customer Support"), "refund-update.md", DOC, FIELDS)


@pytest.mark.django_db
def test_processing_saves_all_chunks_and_waits_for_the_audit(uploaded):
    process_document(uploaded, fake_embed)
    uploaded.refresh_from_db()
    assert uploaded.state == KBDocument.State.AWAITING_AUDIT
    assert [c.heading_path for c in uploaded.chunks.order_by("position")] == [
        "Refund update > Refund window",
        "Refund update > How to request",
    ]


@pytest.mark.django_db
def test_failed_processing_keeps_no_chunks(uploaded):
    process_document(uploaded, broken_embed)
    uploaded.refresh_from_db()
    assert uploaded.state == KBDocument.State.FAILED
    assert "throttling" in uploaded.error
    assert uploaded.chunks.count() == 0


@pytest.mark.django_db
def test_worker_claims_the_next_document_once(uploaded):
    assert claim_and_process_next(fake_embed).id == uploaded.id
    assert claim_and_process_next(fake_embed) is None  # nothing left in "processing"


@pytest.mark.django_db
def test_after_audit_new_document_goes_live_and_new_version_waits_for_approval(uploaded):
    old = KBDocument.objects.create(path="refund-policy-2025.md", title="Refund Policy", content_hash="old")
    version = KBDocument.objects.create(
        path="uploads/v3.md", title="Refund Policy", content_hash="v3", replaces=old,
        state=KBDocument.State.AWAITING_AUDIT,
    )
    process_document(uploaded, fake_embed)

    ids = documents_awaiting_audit()
    assert set(ids) == {uploaded.id, version.id}
    late = KBDocument.objects.create(path="uploads/late.md", title="x", content_hash="late", state=KBDocument.State.AWAITING_AUDIT)

    assert publish_after_audit(ids) == {"documents_live": 1, "documents_awaiting_approval": 1}
    for doc in (uploaded, version, late):
        doc.refresh_from_db()
    assert uploaded.state == KBDocument.State.LIVE
    assert version.state == KBDocument.State.AWAITING_APPROVAL  # replacing always needs an auditor
    assert late.state == KBDocument.State.AWAITING_AUDIT  # arrived during the audit: next run checks it


@pytest.mark.django_db
def test_queue_audit_adds_at_most_one_waiting_run():
    first = queue_audit()
    assert first is not None and first.status == AuditRun.Status.QUEUED
    assert queue_audit() is None  # one is already waiting and will see the change

    first.status = AuditRun.Status.RUNNING
    first.save()
    second = queue_audit()  # the running one may have started before the change
    assert second is not None and second.id != first.id
