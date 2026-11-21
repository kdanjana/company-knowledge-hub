from rest_framework import serializers

from .models import AuditRun, EvalResult, Finding, Investigation, Verdict


class AuditRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditRun
        fields = [
            "id", "status", "created_at", "started_at", "finished_at", "model_id",
            "prompt_version", "settings", "progress", "totals", "error",
        ]


def _side_of(finding: Finding, text_hash: str) -> str:
    return "a" if text_hash and text_hash == finding.hash_a else ("b" if text_hash and text_hash == finding.hash_b else "")


class FindingSerializer(serializers.ModelSerializer):
    # "a" / "b" / "both" / "": which side is a new text the chat does not use until an auditor decides
    new_side = serializers.SerializerMethodField()
    # "a" / "b" / "": the side the auditor chose as correct
    winner = serializers.SerializerMethodField()

    class Meta:
        model = Finding
        fields = [
            "id", "run", "kind", "severity", "topic", "chunk_a_id", "chunk_b_id", "doc_a_path", "doc_b_path",
            "owner_a", "owner_b", "quote_a", "quote_b", "reason", "suggested_truth", "resolution_rule",
            "resolution_reason", "suggested_action", "status", "new_side", "winner", "created_at",
        ]

    def get_new_side(self, finding: Finding) -> str:
        sides = {_side_of(finding, h) for h in (finding.new_hashes or [])} - {""}
        return "both" if sides == {"a", "b"} else (sides.pop() if sides else "")

    def get_winner(self, finding: Finding) -> str:
        return _side_of(finding, finding.winner_hash)


class VerdictSerializer(serializers.ModelSerializer):
    class Meta:
        model = Verdict
        fields = [
            "id", "stage", "label", "confidence", "reason", "quote_a", "quote_b", "grounded", "is_final",
            "input_tokens", "output_tokens", "latency_ms", "cost_usd", "error", "created_at",
        ]


class InvestigationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Investigation
        fields = ["steps", "final_label", "tool_calls", "input_tokens", "output_tokens", "error"]


class FeedbackInputSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["real", "not_conflict", "ignore"])
    # With "real" on a contradiction: which side is correct ("A is correct" / "B is correct"). Optional.
    winner = serializers.ChoiceField(choices=["a", "b"], required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        if data.get("winner") and data["decision"] != "real":
            raise serializers.ValidationError("A winner can only be chosen together with the decision 'real'.")
        return data


class EvalResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvalResult
        fields = ["id", "run", "created_at", "metrics", "details"]
