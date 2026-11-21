from django.conf import settings
from django.db import models
from django.utils import timezone

from .roles import DEPARTMENTS


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    department = models.CharField(max_length=50, blank=True, choices=[(d, d) for d in DEPARTMENTS])

    # Account lockout (see accounts/lockout.py).
    failed_login_count = models.PositiveIntegerField(default=0)  # wrong passwords in a row
    locked_until = models.DateTimeField(null=True, blank=True)  # automatic lock after too many failed logins
    locked_by_admin = models.BooleanField(default=False)  # lasts until an admin unlocks

    def __str__(self) -> str:
        return f"{self.user} ({self.department or 'no department'})"

    def is_locked(self, now=None) -> bool:
        now = now or timezone.now()
        return self.locked_by_admin or (self.locked_until is not None and self.locked_until > now)
