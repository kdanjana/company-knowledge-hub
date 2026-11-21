from rest_framework import serializers

from .models import KBDocument
from .services import VISIBILITY_DEPARTMENT, VISIBILITY_EVERYONE


class DocumentSerializer(serializers.ModelSerializer):
    uploaded_by = serializers.CharField(source="uploaded_by.username", default=None, read_only=True)
    chunk_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = KBDocument
        fields = [
            "id",
            "path",
            "doc_id",
            "title",
            "doc_type",
            "version",
            "effective_date",
            "status",
            "owner",
            "region",
            "audience",
            "departments",
            "source",
            "state",
            "uploaded_by",
            "uploaded_at",
            "original_name",
            "replaces",
            "error",
            "chunk_count",
            "updated_at",
        ]


class UploadSerializer(serializers.Serializer):
    """Form fields. Everything except the file may be left empty and taken from the front-matter
    (or, for a new version, from the replaced document); the service checks the combined result
    against kb/standards.py. There is no version field: the system sets it."""

    file = serializers.FileField()
    title = serializers.CharField(required=False, allow_blank=True, max_length=300)
    doc_id = serializers.CharField(required=False, allow_blank=True, max_length=100)
    doc_type = serializers.CharField(required=False, allow_blank=True, max_length=50)
    effective_date = serializers.CharField(required=False, allow_blank=True, max_length=20)
    status = serializers.CharField(required=False, allow_blank=True, max_length=50)
    owner = serializers.CharField(required=False, allow_blank=True, max_length=200)
    region = serializers.CharField(required=False, allow_blank=True, max_length=50)
    audience = serializers.CharField(required=False, allow_blank=True, max_length=50)
    visibility = serializers.ChoiceField(
        choices=[VISIBILITY_DEPARTMENT, VISIBILITY_EVERYONE], required=False, default=VISIBILITY_DEPARTMENT
    )
    replaces = serializers.IntegerField(required=False, allow_null=True)
