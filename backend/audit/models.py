"""The auditor's own tables. Chunk and document IDs are plain integers (not foreign keys),
because in a real company the knowledge base is another team's database."""
from django.db import models


class AuditRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    model_id = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    progress = models.JSONField(default=dict, blank=True)  # {"stage": ..., "pairs_done": .., "pairs_total": ..}
    totals = models.JSONField(default=dict, blank=True)  # counts, tokens, cost
    error = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"Run {self.id} ({self.status})"


class CandidatePair(models.Model):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="pairs")
    chunk_a_id = models.BigIntegerField()
    chunk_b_id = models.BigIntegerField()
    doc_a_id = models.BigIntegerField()
    doc_b_id = models.BigIntegerField()
    pair_key = models.CharField(max_length=64, db_index=True)
    similarity = models.FloatField()
    exact_duplicate = models.BooleanField(default=False)
    numbers_differ = models.BooleanField(default=False)
    reasons = models.JSONField(default=list, blank=True)
    skipped_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("run", "chunk_a_id", "chunk_b_id")]


class Verdict(models.Model):
    class Stage(models.TextChoices):
        RULE = "rule"  # decided by plain code (exact duplicate)
        CACHE = "cache"  # reused from an earlier run
        JUDGE = "judge"
        INVESTIGATOR = "investigator"

    pair = models.ForeignKey(CandidatePair, on_delete=models.CASCADE, related_name="verdicts")
    stage = models.CharField(max_length=20, choices=Stage.choices)
    label = models.CharField(max_length=30)
    topic = models.CharField(max_length=300, blank=True)
    quote_a = models.TextField(blank=True)
    quote_b = models.TextField(blank=True)
    confidence = models.CharField(max_length=10, blank=True)
    reason = models.TextField(blank=True)
    grounded = models.BooleanField(default=False)
    is_final = models.BooleanField(default=False)
    model_id = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    cache_key = models.CharField(max_length=64, blank=True, db_index=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    cost_usd = models.FloatField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Investigation(models.Model):
    pair = models.OneToOneField(CandidatePair, on_delete=models.CASCADE, related_name="investigation")
    steps = models.JSONField(default=list, blank=True)
    final_label = models.CharField(max_length=30, blank=True)
    tool_calls = models.IntegerField(default=0)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    error = models.TextField(blank=True)


class Finding(models.Model):
    class Kind(models.TextChoices):
        CONTRADICTION = "contradiction"
        DUPLICATE = "duplicate"

    class Severity(models.TextChoices):
        HIGH = "high"
        MEDIUM = "medium"
        INFO = "info"

    class Status(models.TextChoices):
        OPEN = "open"
        CONFIRMED = "confirmed"
        REJECTED = "rejected"
        IGNORED = "ignored"
        # Set by a later audit: the pair was re-checked and is no longer a problem,
        # or one of the two texts does not exist any more (changed, removed or rejected).
        FIXED = "fixed"

    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="findings")
    pair = models.OneToOneField(CandidatePair, on_delete=models.CASCADE, related_name="finding")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    topic = models.CharField(max_length=300, blank=True)
    chunk_a_id = models.BigIntegerField()
    chunk_b_id = models.BigIntegerField()
    doc_a_path = models.CharField(max_length=500)
    doc_b_path = models.CharField(max_length=500)
    quote_a = models.TextField(blank=True)
    quote_b = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    suggested_truth = models.CharField(max_length=1, blank=True)  # "a", "b" or "" (unknown)
    resolution_rule = models.CharField(max_length=30, blank=True)
    resolution_reason = models.TextField(blank=True)
    suggested_action = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    pair_key = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Texts are identified by their fingerprint (content_hash), not by chunk ID or by "a"/"b":
    # IDs change when a document is re-loaded, and the a/b order follows the IDs.
    hash_a = models.CharField(max_length=64, blank=True)
    hash_b = models.CharField(max_length=64, blank=True)
    owner_a = models.CharField(max_length=200, blank=True)  # who to contact to fix each document
    owner_b = models.CharField(max_length=200, blank=True)
    # Texts that were NEW (uploaded, not reviewed) when this conflict was first found. The chat does not use
    # them until an auditor decides. Carried over from run to run for the same pair.
    new_hashes = models.JSONField(default=list, blank=True)
    winner_hash = models.CharField(max_length=64, blank=True)  # the auditor's decision (fingerprint of the correct text)
    resolution_key = models.CharField(max_length=64, blank=True, db_index=True)  # cache key of an LLM resolver answer


class Feedback(models.Model):
    class Decision(models.TextChoices):
        REAL = "real"
        NOT_CONFLICT = "not_conflict"
        IGNORE = "ignore"

    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name="feedback")
    pair_key = models.CharField(max_length=64, db_index=True)  # so future runs reuse the decision for the same texts
    decision = models.CharField(max_length=20, choices=Decision.choices)
    winner_hash = models.CharField(max_length=64, blank=True)  # "real" + which text is correct (fingerprint)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EvalResult(models.Model):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="evaluations")
    created_at = models.DateTimeField(auto_now_add=True)
    metrics = models.JSONField(default=dict)
    details = models.JSONField(default=list)
