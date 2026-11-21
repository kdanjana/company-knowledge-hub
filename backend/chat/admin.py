from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "content", "input_tokens", "output_tokens", "cost_usd", "error")
    readonly_fields = fields


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "updated_at")
    list_filter = ("user",)
    inlines = [MessageInline]
