"""Compare a finished run with the answer key and save the metrics (shown on the Evaluation page)."""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from audit.evaluation import evaluate_and_save
from audit.models import AuditRun


class Command(BaseCommand):
    help = "Evaluate an audit run against data/ground_truth.yaml."

    def add_arguments(self, parser):
        parser.add_argument("--run", type=int, help="Run ID (default: latest finished run)")
        parser.add_argument("--truth", default=str(settings.DATA_DIR / "ground_truth.yaml"))
        parser.add_argument("--details", action="store_true", help="Also print the outcome of every answer-key item.")

    def handle(self, *args, **options):
        if options["run"]:
            run = AuditRun.objects.filter(id=options["run"]).first()
        else:
            run = AuditRun.objects.filter(status=AuditRun.Status.DONE).order_by("-id").first()
        if run is None or run.status != AuditRun.Status.DONE:
            raise CommandError("No finished run found. Run: python manage.py run_audit")

        result = evaluate_and_save(run, Path(options["truth"]))
        self.stdout.write(self.style.SUCCESS(f"Evaluation of run {run.id}:"))
        for key, value in result.metrics.items():
            self.stdout.write(f"  {key}: {value}")
        if options["details"]:
            for item in result.details:
                note = f"  ({item['note']})" if item.get("note") else ""
                self.stdout.write(f"  {item['id']:<14} {item['kind']:<14} {item['outcome']}{note}")
