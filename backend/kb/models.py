from django.conf import settings
from django.db import models
from pgvector.django import HnswIndex, VectorField


class KBDocument(models.Model):
    class Source(models.TextChoices):
        FOLDER = "folder"  # loaded by `seed_kb` (the company's existing knowledge base)
        UPLOAD = "upload"  # uploaded by a user through the UI / API

    class State(models.TextChoices):
        """Where the document is in its life. Only LIVE documents are used by the chat.

        upload -> PROCESSING -> AWAITING_AUDIT -> LIVE                (new document)
                                               -> AWAITING_APPROVAL  (replaces another document:
                                                                      an auditor must approve it)
                            -> FAILED (chunking / embedding failed)
        AWAITING_APPROVAL -> LIVE (approved) or REJECTED (rejected)
        """

        PROCESSING = "processing"
        AWAITING_AUDIT = "awaiting_audit"
        LIVE = "live"
        AWAITING_APPROVAL = "awaiting_approval"
        REJECTED = "rejected"
        FAILED = "failed"

    # Uploads: the standard name "uploads/<doc_id>__v<version>.md" (see kb/standards.py).
    # Seeded documents: their original file name.
    path = models.CharField(max_length=500, unique=True)
    # Stable ID shared by all versions of a document. Empty for seeded documents without one.
    doc_id = models.CharField(max_length=60, blank=True, db_index=True)
    title = models.CharField(max_length=300)
    doc_type = models.CharField(max_length=50, blank=True)
    version = models.CharField(max_length=50, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=50, blank=True)
    owner = models.CharField(max_length=200, blank=True)
    # Scope of the content (optional). Shown to the judge, so "India 3 days" vs "UAE 5 days" is not a conflict.
    region = models.CharField(max_length=30, blank=True)
    audience = models.CharField(max_length=50, blank=True)
    # Departments allowed to see this document in chat. Empty list = everyone.
    departments = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    # Upload and life-cycle tracking. Defaults describe the existing (seeded) knowledge base.
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.FOLDER)
    state = models.CharField(max_length=20, choices=State.choices, default=State.LIVE, db_index=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploaded_documents"
    )
    uploaded_at = models.DateTimeField(null=True, blank=True)
    original_name = models.CharField(max_length=255, blank=True)  # file name as uploaded
    # Uploads: file name inside UPLOAD_DIR. Seeded documents: full path of the source file.
    file_path = models.CharField(max_length=500, blank=True)
    # The document this one is a new version of (it becomes "superseded" when this one is approved).
    replaces = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replaced_by")
    error = models.TextField(blank=True)  # why processing failed

    class Meta:
        db_table = "kb_documents"
        ordering = ["path"]

    def __str__(self) -> str:
        return self.path


class KBChunk(models.Model):
    document = models.ForeignKey(KBDocument, on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()
    heading_path = models.CharField(max_length=500, blank=True)
    text = models.TextField()
    content_hash = models.CharField(max_length=64, db_index=True)
    embedding = VectorField(dimensions=settings.EMBED_DIM)

    class Meta:
        db_table = "kb_chunks"
        ordering = ["document_id", "position"]
        indexes = [
            HnswIndex(
                name="kb_chunks_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            )
        ]

    def __str__(self) -> str:
        return f"{self.document.path} #{self.position}"
