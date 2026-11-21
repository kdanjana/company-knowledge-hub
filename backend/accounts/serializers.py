"""User management (admins only): create, edit, and the user summary shown in lists."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from .models import Profile
from .roles import ADMIN, ASSIGNABLE_ROLES, AUDITOR, DEPARTMENTS, EMPLOYEE

User = get_user_model()

USERNAME_TAKEN = "This username is already taken."
NO_ADMINS = "Admin accounts are created from the command line (manage.py create_admin)."


def role_from_groups(user) -> str:
    """Same result as accounts.roles.role_of, but uses prefetched groups (no query per user in lists)."""
    names = {group.name for group in user.groups.all()}
    if user.is_superuser or AUDITOR in names:
        return AUDITOR
    if ADMIN in names:
        return ADMIN
    return EMPLOYEE


def user_summary(user) -> dict:
    profile = getattr(user, "profile", None)
    return {
        "id": user.pk,
        "username": user.get_username(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.get_full_name(),
        "email": user.email,
        "role": role_from_groups(user),
        "department": profile.department if profile else "",
        "is_active": user.is_active,
        "is_locked": profile.is_locked() if profile else False,
        "locked_by_admin": profile.locked_by_admin if profile else False,
        "locked_until": profile.locked_until if profile else None,
        "failed_login_count": profile.failed_login_count if profile else 0,
        "last_login": user.last_login,
        "date_joined": user.date_joined,
    }


def _check_password(password: str, user) -> None:
    """Django's password rules (settings.AUTH_PASSWORD_VALIDATORS), incl. 'not too similar to the user's names'."""
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"password": list(exc.messages)})


def _check_role(value: str) -> str:
    if value not in ASSIGNABLE_ROLES:
        raise serializers.ValidationError(NO_ADMINS)
    return value


class UserCreateSerializer(serializers.Serializer):
    """An admin creates an employee or auditor account."""

    username = serializers.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    department = serializers.ChoiceField(choices=DEPARTMENTS)
    # "admin" is listed so it gets a clear message instead of "not a valid choice".
    role = serializers.ChoiceField(choices=[EMPLOYEE, AUDITOR, ADMIN], default=EMPLOYEE)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate_username(self, value: str) -> str:
        # Case-insensitive, so "D1U1" cannot be created next to "d1u1".
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(USERNAME_TAKEN)
        return value

    def validate_role(self, value: str) -> str:
        return _check_role(value)

    def validate(self, attrs: dict) -> dict:
        candidate = User(
            username=attrs["username"],
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
            email=attrs.get("email", ""),
        )
        _check_password(attrs["password"], candidate)
        return attrs

    def create(self, validated_data: dict):
        # User + role + department in one transaction: if any step fails, nothing is saved.
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=validated_data["username"],
                    password=validated_data["password"],  # stored hashed
                    first_name=validated_data["first_name"],
                    last_name=validated_data["last_name"],
                    email=validated_data["email"],
                )
                user.groups.set([Group.objects.get_or_create(name=validated_data["role"])[0]])
                Profile.objects.update_or_create(user=user, defaults={"department": validated_data["department"]})
        except IntegrityError:
            # Two requests with the same username at the same moment: the database's unique rule stops the second.
            raise serializers.ValidationError({"username": [USERNAME_TAKEN]})
        return user


class UserUpdateSerializer(serializers.Serializer):
    """PATCH: change role (employee / auditor), department, names, email, or set a new password.
    The username cannot be changed."""

    username = serializers.CharField(required=False)  # only to give a clear error
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    department = serializers.ChoiceField(choices=DEPARTMENTS, required=False)
    role = serializers.ChoiceField(choices=[EMPLOYEE, AUDITOR, ADMIN], required=False)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate_username(self, value: str) -> str:
        if value != self.instance.get_username():
            raise serializers.ValidationError("The username cannot be changed.")
        return value

    def validate_role(self, value: str) -> str:
        return _check_role(value)

    def validate(self, attrs: dict) -> dict:
        if "password" in attrs:
            candidate = User(
                username=self.instance.get_username(),
                first_name=attrs.get("first_name", self.instance.first_name),
                last_name=attrs.get("last_name", self.instance.last_name),
                email=attrs.get("email", self.instance.email),
            )
            _check_password(attrs["password"], candidate)
        return attrs

    def update(self, user, validated_data: dict):
        with transaction.atomic():
            for field in ("first_name", "last_name", "email"):
                if field in validated_data:
                    setattr(user, field, validated_data[field])
            if "password" in validated_data:
                user.set_password(validated_data["password"])
            user.save()
            if "role" in validated_data:
                user.groups.set([Group.objects.get_or_create(name=validated_data["role"])[0]])
            if "department" in validated_data:
                Profile.objects.update_or_create(user=user, defaults={"department": validated_data["department"]})
        return user
