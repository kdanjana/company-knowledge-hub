"""Request IDs, health checks and safe chat errors. No AWS calls (Bedrock is replaced by fakes)."""
import json
import logging

import pytest
from botocore.exceptions import ClientError
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from rest_framework.test import APIClient

from accounts.roles import EMPLOYEE
from chat.models import Conversation, Message
from chat.services import safe_error_message
from config import health
from core.bedrock_client import BedrockError


@pytest.fixture
def api():
    return APIClient()


# ---------- request IDs ----------


def test_every_response_has_a_request_id(api):
    response = api.get("/api/health/")
    assert len(response["X-Request-ID"]) == 12


def test_a_valid_incoming_request_id_is_kept(api):
    response = api.get("/api/health/", HTTP_X_REQUEST_ID="a1b2c3d4e5f6a7b8c9d0")
    assert response["X-Request-ID"] == "a1b2c3d4e5f6a7b8c9d0"


def test_a_strange_incoming_request_id_is_replaced(api):
    for bad in ["short", "has spaces in it", "x" * 65, "<script>alert(1)</script>"]:
        response = api.get("/api/health/", HTTP_X_REQUEST_ID=bad)
        assert response["X-Request-ID"] != bad


# ---------- health checks ----------


def test_health_needs_no_login(api):
    response = api.get("/api/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_ready_checks_database_and_cache(api):
    response = api.get("/api/ready/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "cache": "ok"}


@pytest.mark.django_db
def test_ready_returns_503_without_details_when_a_check_fails(api, monkeypatch):
    def broken_database():
        raise RuntimeError("password authentication failed for user kb_auditor")

    monkeypatch.setitem(health.CHECKS, "database", broken_database)
    response = api.get("/api/ready/")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "error", "cache": "ok"}
    assert "password" not in response.content.decode()


# ---------- safe chat errors ----------


def _ask_with_failing_bedrock(api, monkeypatch, error: Exception):
    """Send one chat question while the Bedrock client fails with `error`. Returns (response, body)."""

    def failing_client(cfg):
        raise error

    monkeypatch.setattr("core.config.Config.require_bedrock", lambda self: None)
    monkeypatch.setattr("chat.services.BedrockClient", failing_client)

    user = User.objects.create_user(username="asker", password="test-only-password")
    user.groups.add(Group.objects.get_or_create(name=EMPLOYEE)[0])
    conversation = Conversation.objects.create(user=user)
    cache.delete(f"throttle_chat_{user.pk}")  # start from an empty rate-limit counter

    api.force_authenticate(user)
    response = api.post(
        f"/api/chat/conversations/{conversation.id}/messages/", {"question": "How often is my bike serviced?"}, format="json"
    )
    body = b"".join(response.streaming_content).decode()
    return response, body


@pytest.mark.django_db
def test_chat_error_shows_a_reference_but_no_internal_details(api, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    response, body = _ask_with_failing_bedrock(api, monkeypatch, RuntimeError("secret internal detail"))
    reference = response["X-Request-ID"]

    # The user sees a safe message with the reference, never the exception text.
    events = [json.loads(line) for line in body.splitlines() if line.strip()]
    error_event = next(e for e in events if e["type"] == "error")
    assert reference in error_event["message"]
    assert "secret internal detail" not in body

    saved = Message.objects.get(role=Message.Role.ASSISTANT)
    assert reference in saved.error
    assert "secret internal detail" not in saved.error

    # The server log has the full details, under the same reference.
    failed = [r for r in caplog.records if r.getMessage() == "chat answer failed"]
    assert failed and failed[0].request_id == reference
    assert "secret internal detail" in caplog.text


@pytest.mark.django_db
def test_chat_throttling_tells_the_user_to_try_again(api, monkeypatch):
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "ConverseStream")
    _, body = _ask_with_failing_bedrock(api, monkeypatch, throttled)
    assert "busy right now" in body


def test_throttling_inside_the_stream_is_recognised():
    message = safe_error_message(BedrockError("throttlingException: Too many tokens"), "abc123")
    assert "busy right now" in message and "abc123" in message


def test_other_errors_get_the_generic_message():
    message = safe_error_message(ValueError("database password is hunter2"), "abc123")
    assert message == "an internal error happened (reference: abc123)"
