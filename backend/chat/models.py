from django.conf import settings
from django.db import models


class Conversation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title or f"Conversation {self.id}"


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user"
        ASSISTANT = "assistant"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField(blank=True)
    citations = models.JSONField(default=list, blank=True)  # sources used: doc, section, text
    warnings = models.JSONField(default=list, blank=True)  # e.g. unresolved conflict on this topic
    dropped = models.JSONField(default=list, blank=True)  # outdated passages that were skipped
    model_id = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    cost_usd = models.FloatField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
