"""Background worker. Each loop:
  1. process one uploaded document (chunk + embed), then queue an audit for it
  2. otherwise run one queued audit (from the UI button, an upload or seed_kb)

Documents go first, so a queued audit includes the documents that were just processed.

Postgres is used as the queue. `select_for_update(skip_locked=True)` locks the row being taken,
so two workers can never pick the same document or the same run.
"""
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from audit.models import AuditRun
from audit.services import execute_run, queue_audit
from core.bedrock_client import BedrockClient
from core.config import load_config
from kb.models import KBDocument
from kb.services import claim_and_process_next


class Command(BaseCommand):
    help = "Process uploaded documents and audit runs."

    def add_arguments(self, parser):
        parser.add_argument("--poll-seconds", type=float, default=3.0)
        parser.add_argument("--once", action="store_true", help="Do at most one job (document or run), then exit.")

    def handle(self, *args, **options):
        self.stdout.write("Worker started. Waiting for uploads and queued runs (Ctrl+C to stop)...")
        self._client = None
        while True:
            did_work = self._process_one_document() or self._run_one_audit()
            if options["once"]:
                if not did_work:
                    self.stdout.write("Nothing to do.")
                return
            if not did_work:
                time.sleep(options["poll_seconds"])

    # --- uploaded documents ---

    def _embed(self, text: str) -> list[float]:
        if self._client is None:
            cfg = load_config()
            if not cfg.embed_model_id:
                raise RuntimeError("EMBED_MODEL_ID is missing in .env, so the document cannot be embedded.")
            self._client = BedrockClient(cfg)
        return self._client.embed(text)

    def _process_one_document(self) -> bool:
        doc = claim_and_process_next(self._embed)
        if doc is None:
            return False
        if doc.state == KBDocument.State.FAILED:
            self.stdout.write(f"Document {doc.id} ({doc.original_name}) failed: {doc.error}")
        else:
            run = queue_audit()
            queued = f"audit run {run.id} queued" if run else "an audit is already queued"
            self.stdout.write(f"Document {doc.id} ({doc.original_name}) processed; {queued}.")
        return True

    # --- audit runs ---

    def _run_one_audit(self) -> bool:
        run = self._claim_next_run()
        if run is None:
            return False
        self.stdout.write(f"Picked run {run.id}")
        execute_run(run, log=self.stdout.write)
        run.refresh_from_db()
        self.stdout.write(f"Run {run.id} finished with status: {run.status}")
        return True

    def _claim_next_run(self):
        with transaction.atomic():
            run = (
                AuditRun.objects.select_for_update(skip_locked=True)
                .filter(status=AuditRun.Status.QUEUED)
                .order_by("id")
                .first()
            )
            if run is None:
                return None
            run.status = AuditRun.Status.RUNNING
            run.started_at = timezone.now()
            run.save(update_fields=["status", "started_at"])
            return run
