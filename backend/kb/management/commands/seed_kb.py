"""Simulates the chatbot team's ingestion: read the QuickBite docs, chunk them, embed them,
and store them in the KB tables.

Incremental: a file whose content has not changed since the last seed is skipped (no new
embedding cost). A changed file is re-chunked and re-embedded. A deleted file is removed.

Only documents that came from the folder (source="folder") are ever changed or removed here.
Documents uploaded through the UI/API are never touched by this command.
Seeded documents are the company's existing knowledge base, so they are live immediately.
If anything changed, one audit is queued (unless --no-audit).
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.services import queue_audit
from core.bedrock_client import BedrockClient
from core.chunking import chunk_markdown, parse_date, parse_document, sha256
from core.config import load_config
from kb.models import KBChunk, KBDocument


def _departments(value) -> list[str]:
    """Front-matter `departments:` -> list of names. Missing, empty or 'all' -> [] (everyone)."""
    if not value:
        return []
    items = [value] if isinstance(value, str) else list(value)
    names = [str(item).strip() for item in items if str(item).strip()]
    return [] if any(name.lower() == "all" for name in names) else names


class Command(BaseCommand):
    help = "Chunk + embed the QuickBite docs into the simulated company knowledge base."

    def add_arguments(self, parser):
        parser.add_argument("--docs-dir", default=str(settings.DATA_DIR / "quickbite_docs"))
        parser.add_argument(
            "--reset", action="store_true", help="Delete all seeded (folder) documents first and rebuild them. Uploads are kept."
        )
        parser.add_argument("--no-audit", action="store_true", help="Do not queue an audit at the end.")

    def handle(self, *args, **options):
        cfg = load_config()
        if not cfg.embed_model_id:
            raise CommandError("EMBED_MODEL_ID is missing in .env (see README step 3).")
        client = BedrockClient(cfg)

        docs_dir = Path(options["docs_dir"])
        files = sorted(docs_dir.glob("*.md"))
        if not files:
            raise CommandError(f"No .md files found in {docs_dir}")

        folder_docs = KBDocument.objects.filter(source=KBDocument.Source.FOLDER)
        if options["reset"]:
            folder_docs.delete()
            self.stdout.write("Seeded documents cleared (uploads kept).")

        seen, created, updated, skipped, embedded = set(), 0, 0, 0, 0
        for file in files:
            raw = file.read_text(encoding="utf-8")
            seen.add(file.name)
            file_hash = sha256(raw)
            existing = folder_docs.filter(path=file.name).first()
            if existing and existing.content_hash == file_hash:
                skipped += 1
                continue
            if existing is None and KBDocument.objects.filter(path=file.name).exists():
                raise CommandError(f"'{file.name}' already exists as a document that was not seeded from this folder.")

            meta, body = parse_document(raw)
            title = str(meta.get("title") or file.stem)
            chunks = chunk_markdown(body, default_title=title)
            vectors = [client.embed(chunk.embed_text()) for chunk in chunks]
            embedded += len(vectors)

            with transaction.atomic():
                if existing:
                    existing.delete()
                    updated += 1
                else:
                    created += 1
                doc = KBDocument.objects.create(
                    path=file.name,
                    # Optional in seeded files (the existing knowledge base does not follow the upload standard).
                    doc_id=str(meta.get("doc_id") or ""),
                    region=str(meta.get("region") or ""),
                    audience=str(meta.get("audience") or ""),
                    title=title,
                    doc_type=str(meta.get("doc_type") or ""),
                    version=str(meta.get("version") or ""),
                    effective_date=parse_date(meta.get("effective_date")),
                    status=str(meta.get("status") or ""),
                    owner=str(meta.get("owner") or ""),
                    departments=_departments(meta.get("departments")),
                    metadata={k: str(v) for k, v in meta.items()},
                    content_hash=file_hash,
                    source=KBDocument.Source.FOLDER,
                    state=KBDocument.State.LIVE,
                    file_path=str(file.resolve()),
                )
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
            self.stdout.write(f"  {file.name}: {len(chunks)} chunks")

        removed, _ = folder_docs.exclude(path__in=seen).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. New: {created}, updated: {updated}, unchanged: {skipped}, removed objects: {removed}, "
                f"embeddings created: {embedded}. Total chunks in KB: {KBChunk.objects.count()}"
            )
        )

        changed = created or updated or removed or options["reset"]
        if changed and not options["no_audit"]:
            run = queue_audit()
            self.stdout.write(f"Audit queued (run {run.id})." if run else "An audit is already queued; it will include these changes.")
