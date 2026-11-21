from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAuditor
from core.config import load_config
from core.kb_adapter import PgvectorKBAdapter

from .models import AuditRun, EvalResult, Feedback, Finding, Investigation
from .serializers import (
    AuditRunSerializer,
    EvalResultSerializer,
    FeedbackInputSerializer,
    FindingSerializer,
    InvestigationSerializer,
    VerdictSerializer,
)

FEEDBACK_TO_STATUS = {
    Feedback.Decision.REAL: Finding.Status.CONFIRMED,
    Feedback.Decision.NOT_CONFLICT: Finding.Status.REJECTED,
    Feedback.Decision.IGNORE: Finding.Status.IGNORED,
}


def _adapter() -> PgvectorKBAdapter:
    return PgvectorKBAdapter(load_config().kb_database_url)


# Every view in this file is for auditors only. Employees get 403 Forbidden.


class DocumentListView(APIView):
    """Documents in the company knowledge base (read through the adapter)."""

    permission_classes = [IsAuditor]

    def get(self, request):
        adapter = _adapter()
        try:
            docs = adapter.list_documents()
        finally:
            adapter.close()
        return Response([d.to_dict() for d in docs])


class RunListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuditor]
    queryset = AuditRun.objects.order_by("-id")
    serializer_class = AuditRunSerializer

    def create(self, request, *args, **kwargs):
        if AuditRun.objects.filter(status__in=[AuditRun.Status.QUEUED, AuditRun.Status.RUNNING]).exists():
            return Response({"detail": "A run is already queued or running."}, status=status.HTTP_409_CONFLICT)
        run = AuditRun.objects.create(status=AuditRun.Status.QUEUED)
        return Response(AuditRunSerializer(run).data, status=status.HTTP_201_CREATED)


class RunDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuditor]
    queryset = AuditRun.objects.all()
    serializer_class = AuditRunSerializer


class FindingListView(generics.ListAPIView):
    """Filters: ?run=<id> (default: latest finished run), &severity=, &kind=, &status="""

    permission_classes = [IsAuditor]
    serializer_class = FindingSerializer

    def get_queryset(self):
        params = self.request.query_params
        run_id = params.get("run")
        if not run_id:
            latest = AuditRun.objects.filter(status=AuditRun.Status.DONE).order_by("-id").first()
            run_id = latest.id if latest else None
        if run_id is None:
            return Finding.objects.none()

        qs = Finding.objects.filter(run_id=run_id)
        for field in ("severity", "kind", "status"):
            if params.get(field):
                qs = qs.filter(**{field: params[field]})
        severity_order = Case(
            When(severity="high", then=Value(0)),
            When(severity="medium", then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
        return qs.annotate(severity_order=severity_order).order_by("severity_order", "id")


def _chunk_info(adapter, chunk_id: int):
    chunk = adapter.get_chunk(chunk_id)
    if chunk is None:
        return None  # the KB changed after this run
    doc = adapter.get_document(chunk.doc_id)
    return {
        "id": chunk.id,
        "heading_path": chunk.heading_path,
        "text": chunk.text,
        "document": doc.to_dict() if doc else None,
    }


class FindingDetailView(APIView):
    permission_classes = [IsAuditor]

    def get(self, request, pk: int):
        finding = get_object_or_404(Finding.objects.select_related("pair"), pk=pk)
        data = dict(FindingSerializer(finding).data)
        adapter = _adapter()
        try:
            data["chunk_a"] = _chunk_info(adapter, finding.chunk_a_id)
            data["chunk_b"] = _chunk_info(adapter, finding.chunk_b_id)
        finally:
            adapter.close()
        data["verdicts"] = VerdictSerializer(finding.pair.verdicts.order_by("id"), many=True).data
        investigation = Investigation.objects.filter(pair=finding.pair).first()
        data["investigation"] = InvestigationSerializer(investigation).data if investigation else None
        return Response(data)


class FindingFeedbackView(APIView):
    permission_classes = [IsAuditor]

    def post(self, request, pk: int):
        finding = get_object_or_404(Finding, pk=pk)
        payload = FeedbackInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        decision = payload.validated_data["decision"]
        winner = payload.validated_data.get("winner")
        if winner and finding.kind != Finding.Kind.CONTRADICTION:
            return Response({"detail": "A winner can only be chosen for a contradiction."}, status=status.HTTP_400_BAD_REQUEST)
        # Saved by text fingerprint, so the decision still points to the right text in later audits.
        winner_hash = {"a": finding.hash_a, "b": finding.hash_b}.get(winner or "", "")
        Feedback.objects.create(
            finding=finding,
            pair_key=finding.pair_key,
            decision=decision,
            winner_hash=winner_hash,
            note=payload.validated_data.get("note", ""),
        )
        finding.status = FEEDBACK_TO_STATUS[decision]
        finding.winner_hash = winner_hash
        finding.save(update_fields=["status", "winner_hash"])
        return Response(FindingSerializer(finding).data)


class EvalResultListView(generics.ListAPIView):
    permission_classes = [IsAuditor]
    queryset = EvalResult.objects.order_by("-id")
    serializer_class = EvalResultSerializer
