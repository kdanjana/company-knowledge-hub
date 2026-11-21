"""Run one audit directly from the command line (no worker needed)."""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from audit.evaluation import evaluate_and_save
from audit.models import AuditRun
from audit.services import execute_run


class Command(BaseCommand):
    help = "Run one audit of the company knowledge base now."

    def add_arguments(self, parser):
        parser.add_argument("--evaluate", action="store_true", help="Compare the result with data/ground_truth.yaml afterwards.")
        parser.add_argument("--truth", default=str(settings.DATA_DIR / "ground_truth.yaml"))

    def handle(self, *args, **options):
        if AuditRun.objects.filter(status__in=[AuditRun.Status.QUEUED, AuditRun.Status.RUNNING]).exists():
            raise CommandError("A run is already queued or running (check the Runs page or the admin).")

        run = AuditRun.objects.create(status=AuditRun.Status.QUEUED)
        self.stdout.write(f"Run {run.id} started")
        execute_run(run, log=self.stdout.write)
        run.refresh_from_db()

        if run.status != AuditRun.Status.DONE:
            raise CommandError(f"Run {run.id} failed:\n{run.error}")

        totals = run.totals
        self.stdout.write(self.style.SUCCESS(f"Run {run.id} done"))
        for key, value in totals.items():
            self.stdout.write(f"  {key}: {value}")

        if options["evaluate"]:
            result = evaluate_and_save(run, Path(options["truth"]))
            self.stdout.write(self.style.SUCCESS("Evaluation:"))
            for key, value in result.metrics.items():
                self.stdout.write(f"  {key}: {value}")
