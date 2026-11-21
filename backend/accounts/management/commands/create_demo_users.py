"""Create demo users for trying roles and department access. Asks for one password (nothing hard-coded)."""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Profile
from accounts.roles import AUDITOR, EMPLOYEE

DEMO_USERS = [
    # username,        role,     department
    ("auditor", AUDITOR, "Legal"),
    ("support_agent", EMPLOYEE, "Customer Support"),
    ("ops_agent", EMPLOYEE, "Operations"),
    ("finance_staff", EMPLOYEE, "Finance"),
]


class Command(BaseCommand):
    help = "Create (or update) demo users: 1 auditor + 3 employees in different departments."

    def handle(self, *args, **options):
        password = getpass("Password for all demo users: ")
        if len(password) < 8:
            raise CommandError("Use at least 8 characters.")
        if getpass("Repeat password: ") != password:
            raise CommandError("Passwords do not match.")

        User = get_user_model()
        for username, role, department in DEMO_USERS:
            user, created = User.objects.get_or_create(username=username)
            user.set_password(password)
            user.save()
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.set([group])
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.department = department
            profile.save()
            action = "created" if created else "updated"
            self.stdout.write(f"  {username:<14} {role:<9} {department:<17} ({action})")
        self.stdout.write(self.style.SUCCESS("Demo users ready."))
