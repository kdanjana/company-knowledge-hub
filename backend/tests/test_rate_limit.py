"""Chat rate limit. Needs the database (Docker Postgres running); no AWS calls.

The limit is counted in Django's cache. gunicorn runs several processes, so the cache must be shared
(Redis in Docker); a cache in process memory would give each process its own counter.
"""
import os
import subprocess
import sys
import uuid

import pytest
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.roles import EMPLOYEE
from chat.models import Conversation

USES_REDIS = "redis" in settings.CACHES["default"]["BACKEND"].lower()


def fake_stream_reply(user, conversation, question, request_id=""):
    # Stands in for the real answer, so no Bedrock call is made.
    yield '{"type": "done"}\n'


@pytest.mark.django_db
def test_chat_is_refused_after_the_limit(monkeypatch):
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "chat", "3/min")
    monkeypatch.setattr("chat.views.stream_reply", fake_stream_reply)

    user = User.objects.create_user(username="limited", password="test-only-password")
    user.groups.add(Group.objects.get_or_create(name=EMPLOYEE)[0])
    conversation = Conversation.objects.create(user=user)
    url = f"/api/chat/conversations/{conversation.id}/messages/"
    counter_key = f"throttle_chat_{user.pk}"  # the cache key DRF uses for this user's counter

    api = APIClient()
    api.force_authenticate(user)
    cache.delete(counter_key)
    try:
        codes = [api.post(url, {"question": f"question {i}"}, format="json").status_code for i in range(4)]
    finally:
        cache.delete(counter_key)

    assert codes == [200, 200, 200, 429]


@pytest.mark.skipif(not USES_REDIS, reason="REDIS_URL is not set (in-memory cache: one process only)")
def test_two_processes_share_one_counter():
    """A value written to the cache by another Python process must be visible here.
    This is what makes one rate limit per user work across all gunicorn processes."""
    key = f"rate-limit-test-{uuid.uuid4().hex}"
    other_process = (
        "import django; django.setup(); "
        "from django.core.cache import cache; "
        f"cache.set('{key}', 'written by another process', 60)"
    )
    try:
        subprocess.run(
            [sys.executable, "-c", other_process],
            cwd=settings.BASE_DIR,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
            check=True,
            timeout=60,
        )
        assert cache.get(key) == "written by another process"
    finally:
        cache.delete(key)
