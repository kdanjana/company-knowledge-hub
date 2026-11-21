from django.contrib import admin

from .models import KBChunk, KBDocument


@admin.register(KBDocument)
class KBDocumentAdmin(admin.ModelAdmin):
    list_display = ("path", "title", "doc_type", "version", "effective_date", "status", "owner", "departments")
    list_filter = ("doc_type", "status")


@admin.register(KBChunk)
class KBChunkAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "position", "heading_path")
    list_filter = ("document",)
    exclude = ("embedding",)
