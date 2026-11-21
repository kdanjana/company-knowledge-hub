"""Health checks.

/api/health/  the app process is running (nothing else is checked).
/api/ready/   the app can reach what it needs: Postgres and Redis (the cache). Used by the Docker health check.

Neither needs a login, and neither returns error details (those go to the log).
Bedrock is not checked: every call costs money, and a model outage should not mark the app as down.
"""
import logging
import uuid

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def health(request):
    return JsonResponse({"status": "ok"})


def _check_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def _check_cache() -> None:
    key = f"ready-check-{uuid.uuid4().hex}"
    cache.set(key, "ok", 10)
    value = cache.get(key)
    cache.delete(key)
    if value != "ok":
        raise RuntimeError("the cache did not return the value it stored")


CHECKS = {"database": _check_database, "cache": _check_cache}


def ready(request):
    results = {}
    for name, check in CHECKS.items():
        try:
            check()
            results[name] = "ok"
        except Exception:
            logger.exception("readiness check failed", extra={"fields": {"check": name}})
            results[name] = "error"
    ok = all(value == "ok" for value in results.values())
    return JsonResponse({"status": "ok" if ok else "error", **results}, status=200 if ok else 503)
