"""Uploads and the document life cycle: the knowledge base's own logic.

The auditor still READS the knowledge base through the read-only adapter (core/kb_adapter.py).
Changes to documents (upload, processing, publishing after an audit, approve / reject) go
through the functions in this file only.

Flow of an upload:
  save_upload()          checks the file + metadata (kb/standards.py), names it
                         uploads/<doc_id>__v<version>.md, saves the file, then the row (state PROCESSING)
  process_document()     worker: chunk + embed + save all chunks at once (state AWAITING_AUDIT)
  publish_after_audit()  after an audit: new document -> LIVE, new version -> AWAITING_APPROVAL
  approve_version()      auditor: new version -> LIVE, old one -> status "superseded"
  reject_version()       auditor: new version -> REJECTED
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from accounts.roles import allowed_departments, department_of
from core.chunking import chunk_markdown, parse_date, parse_document, sha256

from . import standards
from .models import KBChunk, KBDocument

UPLOAD_STATUSES = ("active", "draft")  # "superseded" is only ever set by an approval
VISIBILITY_DEPARTMENT = "department"
VISIBILITY_EVERYONE = "everyone"
VERSION_NUMBER_RE = re.compile(r"\d+")

Embed = Callable[[str], list[float]]


class UploadError(Exception):
    """A problem the user can fix (shown to them as a 400 error)."""


@dataclass
class UploadFields:
    """Values from the upload form. Empty values are filled from the file's front-matter and, for a
    new version, from the document it replaces. The version is never typed: the system sets it."""

    title: str = ""
    doc_id: str = ""
    doc_type: str = ""
    effective_date: str = ""
    status: str = ""
    owner: str = ""
    region: str = ""
    audience: str = ""
    visibility: str = VISIBILITY_DEPARTMENT
    replaces_id: int | None = None


@dataclass
class StandardName:
    doc_id: str
    version: int
    path: str
    replaces: KBDocument | None


# ---------- who can see what ----------


def visible_documents(user) -> QuerySet[KBDocument]:
    """Documents this user may see in lists: auditors see all; others see LIVE documents open to
    everyone or to their department, plus everything they uploaded themselves."""
    qs = KBDocument.objects.annotate(chunk_count=Count("chunks")).select_related("uploaded_by")
    departments = allowed_departments(user)
    if departments is None:
        return qs
    open_to_user = Q(departments=[])
    for name in departments:
        open_to_user |= Q(departments__contains=[name])
    return qs.filter((open_to_user & Q(state=KBDocument.State.LIVE)) | Q(uploaded_by=user))


def read_original(doc: KBDocument) -> str:
    if not doc.file_path:
        raise UploadError("The original file of this document is not available.")
    path = Path(doc.file_path) if doc.source == KBDocument.Source.FOLDER else _upload_dir() / doc.file_path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UploadError("The original file of this document could not be read.") from exc


# ---------- the standard name ----------


def family_id(doc: KBDocument) -> str:
    """The doc_id of a document; seeded documents without one get it from their title."""
    return doc.doc_id or standards.slugify_doc_id(doc.title)


def _version_number(value: str) -> int:
    match = VERSION_NUMBER_RE.search(value or "")
    return int(match.group()) if match else 0


def _doc_id_taken(doc_id: str) -> KBDocument | None:
    """A document already uses this ID (seeded documents without an ID count by their title)."""
    found = KBDocument.objects.filter(doc_id=doc_id).first()
    if found:
        return found
    for doc in KBDocument.objects.filter(doc_id="").only("id", "title", "path"):
        if standards.slugify_doc_id(doc.title) == doc_id:
            return doc
    return None


def _replaced_document(user, replaces_id: int | None) -> KBDocument | None:
    if replaces_id is None:
        return None
    doc = visible_documents(user).filter(pk=replaces_id).first()
    if doc is None:
        raise UploadError("The document to replace does not exist or you cannot see it.")
    if doc.state != KBDocument.State.LIVE:
        raise UploadError("Only a live document can be replaced.")
    return doc


def resolve_name(user, doc_id: str, replaces_id: int | None, title: str = "") -> StandardName:
    """Work out uploads/<doc_id>__v<version>.md.
    New version: the ID of the replaced document, version = highest so far + 1 (rejected and failed
    versions count too, so a name is never reused). New document: the given ID (or one made from the
    title), which must not be used yet; version 1."""
    replaces = _replaced_document(user, replaces_id)
    if replaces is not None:
        family = family_id(replaces)
        versions = [_version_number(v) for v in KBDocument.objects.filter(doc_id=family).values_list("version", flat=True)]
        version = max(versions + [_version_number(replaces.version)]) + 1
        return StandardName(family, version, standards.standard_path(family, version), replaces)

    doc_id = (doc_id or standards.slugify_doc_id(title)).strip()
    if not doc_id:
        raise UploadError("Enter a Document ID (or a title to make one from).")
    if not standards.is_valid_doc_id(doc_id):
        raise UploadError(
            f"Document ID '{doc_id}' is not valid: use lowercase letters, digits and hyphens "
            f"(e.g. refund-policy-uae), at most {standards.DOC_ID_MAX} characters."
        )
    existing = _doc_id_taken(doc_id)
    if existing is not None:
        raise UploadError(
            f"Document ID '{doc_id}' is already used by '{existing.title}' ({existing.path}). "
            "To update that document, choose 'New version of' instead."
        )
    return StandardName(doc_id, 1, standards.standard_path(doc_id, 1), None)


def preview_name(user, doc_id: str, replaces_id: int | None, title: str = "") -> dict:
    """For the upload form: the name the document would get, or why it cannot get one."""
    try:
        name = resolve_name(user, doc_id, replaces_id, title)
    except UploadError as exc:
        return {"doc_id": doc_id, "version": None, "name": None, "error": str(exc)}
    return {"doc_id": name.doc_id, "version": name.version, "name": name.path, "error": ""}


# ---------- upload ----------


def _upload_dir() -> Path:
    return Path(settings.UPLOAD_DIR)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")  # also removes a byte-order mark added by some Windows editors
    except UnicodeDecodeError as exc:
        raise UploadError("The file must be UTF-8 text.") from exc


def _choice(value: str, allowed: list[str], label: str, required: bool) -> str:
    """Match a value to the allowed list, ignoring case; returns the standard spelling."""
    value = (value or "").strip()
    if not value:
        if required:
            raise UploadError(f"{label} is required.")
        return ""
    for option in allowed:
        if option.lower() == value.lower():
            return option
    raise UploadError(f"{label} must be one of: {', '.join(allowed)}.")


def _departments_for(user, visibility: str) -> list[str]:
    if visibility == VISIBILITY_EVERYONE:
        return []
    if visibility != VISIBILITY_DEPARTMENT:
        raise UploadError("Visibility must be 'department' or 'everyone'.")
    department = department_of(user)
    if not department:
        raise UploadError("Your account has no department. Choose 'everyone', or ask an admin to set your department.")
    return [department]


def save_upload(user, file_name: str, data: bytes, fields: UploadFields) -> KBDocument:
    """Check the upload, name it by the standard, save the file, then the database row (state PROCESSING).
    The worker picks it up from there. Raises UploadError with a readable message."""
    # 1. The file itself.
    if not file_name.lower().endswith(".md"):
        raise UploadError("Only Markdown files (.md) can be uploaded.")
    max_bytes = int(settings.UPLOAD_MAX_MB * 1024 * 1024)
    if len(data) > max_bytes:
        raise UploadError(f"The file is larger than {settings.UPLOAD_MAX_MB:g} MB.")
    raw = _decode(data)
    try:
        meta, body = parse_document(raw)
    except yaml.YAMLError as exc:
        raise UploadError("The front-matter (the --- block at the top) is not valid YAML.") from exc
    if not chunk_markdown(body):
        raise UploadError("The document has no text.")

    # 2. The same content must not be uploaded twice.
    content_hash = sha256(raw)
    duplicate = (
        KBDocument.objects.filter(content_hash=content_hash)
        .exclude(state__in=[KBDocument.State.REJECTED, KBDocument.State.FAILED])
        .first()
    )
    if duplicate:
        raise UploadError(f"This exact content already exists as '{duplicate.path}'.")

    # 3. Name: uploads/<doc_id>__v<version>.md
    def from_meta(key: str) -> str:
        return str(meta.get(key) or "").strip()

    name = resolve_name(
        user, fields.doc_id.strip() or from_meta("doc_id"), fields.replaces_id, fields.title or from_meta("title")
    )
    old = name.replaces

    # 4. Metadata: form first, then front-matter, then (new version) the replaced document.
    def pick(form_value: str, key: str) -> str:
        return (form_value or "").strip() or from_meta(key) or (str(getattr(old, key) or "") if old else "")

    title = pick(fields.title, "title")
    if not title:
        raise UploadError("Title is required.")
    if len(title) > standards.TITLE_MAX:
        raise UploadError(f"Title must be at most {standards.TITLE_MAX} characters.")
    doc_type = _choice(pick(fields.doc_type, "doc_type"), standards.DOC_TYPES, "Type", required=True)
    owner = _choice(pick(fields.owner, "owner"), standards.OWNERS, "Owner", required=True)
    region = _choice(pick(fields.region, "region"), standards.REGIONS, "Region", required=False)
    audience = _choice(pick(fields.audience, "audience"), standards.AUDIENCES, "Audience", required=False)
    status = _choice(fields.status or from_meta("status"), list(UPLOAD_STATUSES), "Status", required=True)
    effective_date = parse_date(fields.effective_date.strip() or meta.get("effective_date"))
    if effective_date is None:
        raise UploadError("Effective date is required (YYYY-MM-DD).")
    departments = _departments_for(user, fields.visibility)

    # 5. Save the file first, then the row. If the row cannot be saved, remove the file again,
    #    so the volume never keeps files the database does not know about. The file on disk has a
    #    random prefix, so two uploads at the same moment can never overwrite each other's file.
    stored_name = f"{uuid.uuid4().hex[:12]}-{Path(name.path).name}"
    target = _upload_dir() / stored_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    try:
        return KBDocument.objects.create(
            path=name.path,
            doc_id=name.doc_id,
            title=title,
            doc_type=doc_type,
            version=str(name.version),
            effective_date=effective_date,
            status=status,
            owner=owner,
            region=region,
            audience=audience,
            departments=departments,
            metadata={k: str(v) for k, v in meta.items()},
            content_hash=content_hash,
            source=KBDocument.Source.UPLOAD,
            state=KBDocument.State.PROCESSING,
            uploaded_by=user,
            uploaded_at=timezone.now(),
            original_name=file_name[:255],
            file_path=stored_name,
            replaces=old,
        )
    except IntegrityError as exc:
        target.unlink(missing_ok=True)
        raise UploadError(f"'{name.path}' was just created by another upload. Please try again.") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise


# ---------- processing (worker) ----------


def process_document(doc: KBDocument, embed: Embed) -> None:
    """Chunk + embed one uploaded document and save all its chunks at once.
    Success -> AWAITING_AUDIT. Any error -> FAILED with the reason (no chunks are kept)."""
    try:
        raw = read_original(doc)
        _, body = parse_document(raw)
        chunks = chunk_markdown(body, default_title=doc.title)
        vectors = [embed(chunk.embed_text()) for chunk in chunks]  # Bedrock calls happen before any write
        with transaction.atomic():
            doc.chunks.all().delete()  # in case an earlier attempt left something behind
            KBChunk.objects.bulk_create(
                [
                    KBChunk(
                        document=doc,
                        position=chunk.position,
                        heading_path=chunk.heading_path,
                        text=chunk.text,
                        content_hash=chunk.content_hash,
                        embedding=vector,
                    )
                    for chunk, vector in zip(chunks, vectors)
                ]
            )
            doc.state = KBDocument.State.AWAITING_AUDIT
            doc.error = ""
            doc.save(update_fields=["state", "error", "updated_at"])
    except Exception as exc:
        doc.state = KBDocument.State.FAILED
        doc.error = str(exc)[:2000] or exc.__class__.__name__
        doc.save(update_fields=["state", "error", "updated_at"])


def claim_and_process_next(embed: Embed) -> KBDocument | None:
    """Take the oldest document waiting for processing and process it.
    The row stays locked while it is processed (SKIP LOCKED), so two workers never take the same one."""
    with transaction.atomic():
        doc = (
            KBDocument.objects.select_for_update(skip_locked=True)
            .filter(state=KBDocument.State.PROCESSING)
            .order_by("id")
            .first()
        )
        if doc is None:
            return None
        process_document(doc, embed)
    return doc


# ---------- after an audit ----------


def documents_awaiting_audit() -> list[int]:
    """Called when an audit starts: the new documents this audit will check."""
    return list(KBDocument.objects.filter(state=KBDocument.State.AWAITING_AUDIT).values_list("id", flat=True))


def publish_after_audit(doc_ids: list[int]) -> dict:
    """Called when an audit finished. New documents go live; new versions wait for an auditor.
    Documents uploaded while the audit ran are not in doc_ids: the next audit checks them.
    Sections that conflict with live documents are held by the chat rules (core/chat.py)."""
    docs = KBDocument.objects.filter(id__in=doc_ids, state=KBDocument.State.AWAITING_AUDIT)
    to_approval = docs.filter(replaces__isnull=False).update(state=KBDocument.State.AWAITING_APPROVAL)
    to_live = docs.filter(replaces__isnull=True).update(state=KBDocument.State.LIVE)
    return {"documents_live": to_live, "documents_awaiting_approval": to_approval}


# ---------- auditor decisions on new versions ----------


def approve_version(doc: KBDocument) -> KBDocument:
    """The new version goes live and the document it replaces is marked superseded."""
    if doc.state != KBDocument.State.AWAITING_APPROVAL:
        raise UploadError("Only a document awaiting approval can be approved.")
    with transaction.atomic():
        doc.state = KBDocument.State.LIVE
        doc.save(update_fields=["state", "updated_at"])
        if doc.replaces_id:
            old = KBDocument.objects.select_for_update().get(pk=doc.replaces_id)
            old.status = "superseded"
            old.metadata = {**old.metadata, "status": "superseded"}
            old.save(update_fields=["status", "metadata", "updated_at"])
    return doc


def reject_version(doc: KBDocument) -> KBDocument:
    if doc.state != KBDocument.State.AWAITING_APPROVAL:
        raise UploadError("Only a document awaiting approval can be rejected.")
    doc.state = KBDocument.State.REJECTED
    doc.save(update_fields=["state", "updated_at"])
    return doc
