"""Create an admin: manages user accounts through /api/users/. Asks for the password (nothing hard-coded).

Admins can only be created here (or with createsuperuser), never through the API.
"""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Profile
from accounts.roles import ADMIN, DEPARTMENTS


class Command(BaseCommand):
    help = "Create an admin account (user management). Asks for the password."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--department", default="", choices=[""] + DEPARTMENTS, help="Optional department.")

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"The username '{username}' is already taken.")

        password = getpass("Password: ")
        if getpass("Repeat password: ") != password:
            raise CommandError("Passwords do not match.")
        try:
            validate_password(password, user=User(username=username))
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        with transaction.atomic():
            user = User.objects.create_user(username=username, password=password)
            user.groups.set([Group.objects.get_or_create(name=ADMIN)[0]])
            Profile.objects.update_or_create(user=user, defaults={"department": options["department"]})
        self.stdout.write(self.style.SUCCESS(f"Admin '{username}' created."))
