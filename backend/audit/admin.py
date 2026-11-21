from django.contrib import admin

from .models import AuditRun, CandidatePair, EvalResult, Feedback, Finding, Investigation, Verdict


@admin.register(AuditRun)
class AuditRunAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "created_at", "finished_at", "model_id", "prompt_version")


@admin.register(CandidatePair)
class CandidatePairAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "chunk_a_id", "chunk_b_id", "similarity", "exact_duplicate", "numbers_differ", "skipped_reason")
    list_filter = ("run",)


@admin.register(Verdict)
class VerdictAdmin(admin.ModelAdmin):
    list_display = ("id", "pair", "stage", "label", "confidence", "grounded", "is_final", "cost_usd")
    list_filter = ("stage", "label", "is_final")


@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    list_display = ("id", "pair", "final_label", "tool_calls")


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "kind", "severity", "topic", "doc_a_path", "doc_b_path", "suggested_truth", "status")
    list_filter = ("run", "kind", "severity", "status")


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "finding", "decision", "created_at")


@admin.register(EvalResult)
class EvalResultAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "created_at")
