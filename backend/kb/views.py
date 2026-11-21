from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAuditor
from audit.services import accept_new_version

from . import standards
from .models import KBDocument
from .serializers import DocumentSerializer, UploadSerializer
from .services import (
    UploadError,
    UploadFields,
    approve_version,
    preview_name,
    read_original,
    reject_version,
    save_upload,
    visible_documents,
)


def _error(exc: UploadError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class DocumentListUploadView(APIView):
    """GET: documents this user may see (+ their own uploads). POST: upload a Markdown file.
    Any logged-in user may upload; who uploaded what is recorded."""

    def get(self, request):
        docs = visible_documents(request.user).order_by(F("uploaded_at").desc(nulls_last=True), "path")
        return Response(DocumentSerializer(docs, many=True).data)

    def post(self, request):
        form = UploadSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        upload = data["file"]
        fields = UploadFields(
            title=data.get("title", ""),
            doc_id=data.get("doc_id", ""),
            doc_type=data.get("doc_type", ""),
            effective_date=data.get("effective_date", ""),
            status=data.get("status", ""),
            owner=data.get("owner", ""),
            region=data.get("region", ""),
            audience=data.get("audience", ""),
            visibility=data.get("visibility"),
            replaces_id=data.get("replaces"),
        )
        try:
            doc = save_upload(request.user, upload.name, upload.read(), fields)
        except UploadError as exc:
            return _error(exc)
        doc = visible_documents(request.user).get(pk=doc.pk)  # adds chunk_count for the response
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


class StandardsView(APIView):
    """The allowed values for uploads (types, owners, regions, audiences, ID format)."""

    def get(self, request):
        return Response(standards.as_dict())


class NextNameView(APIView):
    """Preview for the upload form: ?doc_id=...&title=... (new document) or ?replaces=<id> (new version)
    -> the standard name the upload would get, or why it cannot get one."""

    def get(self, request):
        params = request.query_params
        replaces = params.get("replaces") or ""
        if replaces and not replaces.isdigit():
            return Response({"detail": "replaces must be a document id."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            preview_name(request.user, params.get("doc_id", ""), int(replaces) if replaces else None, params.get("title", ""))
        )


class DocumentOriginalView(APIView):
    """The full original text of a document the user may see."""

    def get(self, request, pk: int):
        doc = get_object_or_404(visible_documents(request.user), pk=pk)
        try:
            return Response({"id": doc.id, "path": doc.path, "text": read_original(doc)})
        except UploadError as exc:
            return _error(exc)


class DocumentApproveView(APIView):
    """Auditors only: a new version goes live, the old one becomes superseded."""

    permission_classes = [IsAuditor]

    def post(self, request, pk: int):
        doc = get_object_or_404(KBDocument, pk=pk)
        try:
            approve_version(doc)
        except UploadError as exc:
            return _error(exc)
        # Approving the version also decides its open conflicts in its favour, so its held texts are used.
        accept_new_version(doc.id, set(doc.chunks.values_list("content_hash", flat=True)))
        return Response(DocumentSerializer(visible_documents(request.user).get(pk=pk)).data)


class DocumentRejectView(APIView):
    """Auditors only: a new version is rejected and never used."""

    permission_classes = [IsAuditor]

    def post(self, request, pk: int):
        doc = get_object_or_404(KBDocument, pk=pk)
        try:
            reject_version(doc)
        except UploadError as exc:
            return _error(exc)
        return Response(DocumentSerializer(visible_documents(request.user).get(pk=pk)).data)
