"""Account lockout and forced log-out.

- LOGIN_MAX_FAILED_ATTEMPTS wrong passwords in a row lock the account for LOGIN_LOCK_MINUTES (then it unlocks
  by itself, with a fresh set of attempts). A correct login resets the count.
- While an account is locked, the password is not checked at all, so guessing cannot continue.
- An admin can lock an account with no end time, and unlock any lock (accounts/views.py: UserViewSet).
- Failed guesses by someone else do NOT log the real user out: their tokens keep working. Only an admin lock,
  a deactivation or a password change by an admin logs the user out everywhere.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .models import Profile

LOCKED_MESSAGE = "This account is locked. Try again later or contact an administrator."


def is_locked(user) -> bool:
    profile = Profile.objects.filter(user=user).first()
    return profile is not None and profile.is_locked()


def record_failure(user) -> bool:
    """Count one wrong password. Returns True if this failure locked the account.
    The profile row is locked while counting, so failures arriving at the same moment are all counted."""
    with transaction.atomic():
        profile, _ = Profile.objects.select_for_update().get_or_create(user=user)
        profile.failed_login_count += 1
        locked = profile.failed_login_count >= settings.LOGIN_MAX_FAILED_ATTEMPTS
        if locked:
            profile.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
            profile.failed_login_count = 0  # a fresh set of attempts once the lock ends
        profile.save(update_fields=["failed_login_count", "locked_until"])
    return locked


def record_success(user) -> None:
    Profile.objects.filter(user=user).exclude(failed_login_count=0, locked_until=None).update(
        failed_login_count=0, locked_until=None
    )


def lock_by_admin(user) -> None:
    Profile.objects.update_or_create(user=user, defaults={"locked_by_admin": True})
    log_out_everywhere(user)


def unlock(user) -> None:
    Profile.objects.update_or_create(
        user=user, defaults={"locked_by_admin": False, "locked_until": None, "failed_login_count": 0}
    )


def log_out_everywhere(user) -> int:
    """Blacklist every refresh token issued to this user that has not expired yet. Their access tokens still work
    until they expire (at most JWT_ACCESS_MINUTES). The refresh endpoint also refuses deactivated and
    admin-locked users (accounts/views.py: RefreshSerializer), so no new access token can be obtained."""
    blacklisted = 0
    for token in OutstandingToken.objects.filter(user=user, expires_at__gt=timezone.now()):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        blacklisted += int(created)
    return blacklisted
