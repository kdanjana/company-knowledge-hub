"""Login (JWT) and user management.

POST /api/auth/login/    {username, password} -> {access, refresh, user}   (lockout: accounts/lockout.py)
POST /api/auth/refresh/  {refresh} -> {access, refresh}   (a new refresh token; the old one is blacklisted)
POST /api/auth/logout/   {refresh} -> the refresh token is blacklisted
GET  /api/auth/me/       who is logged in (from "Authorization: Bearer <access>")

Admins only (UserViewSet, /api/users/):
GET list (search, role, department, status) | POST create | GET one | PATCH edit | DELETE deactivate
POST <id>/reactivate/ | POST <id>/lock/ | POST <id>/unlock/

The tokens travel in the Authorization header, never in cookies, so the API needs no CSRF token.
"""
import logging

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from . import lockout
from .permissions import IsAdmin
from .roles import ADMIN, department_of, is_admin, role_of
from .serializers import UserCreateSerializer, UserUpdateSerializer, user_summary

User = get_user_model()
logger = logging.getLogger(__name__)


def me_payload(user) -> dict:
    if not user or not user.is_authenticated:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": user.get_username(),
        "full_name": user.get_full_name(),
        "role": role_of(user),
        "department": department_of(user),
        "can_manage_users": is_admin(user),
    }


# ---------- login / refresh / logout / me ----------


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []  # an old, expired token in the header must not block logging in

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        username = data.validated_data["username"]
        account = User.objects.filter(username=username).first()

        # Locked: refuse without checking the password, so guessing cannot continue.
        if account is not None and lockout.is_locked(account):
            logger.warning("login refused: account locked", extra={"fields": {"user_id": account.pk}})
            return Response({"detail": lockout.LOCKED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)

        user = authenticate(request._request, username=username, password=data.validated_data["password"])
        if user is None:
            if account is not None and account.is_active:
                if lockout.record_failure(account):
                    logger.warning("account locked after failed logins", extra={"fields": {"user_id": account.pk}})
                    return Response({"detail": lockout.LOCKED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)
                logger.info("login failed", extra={"fields": {"user_id": account.pk}})
            else:
                logger.info("login failed: unknown or deactivated user")
            # Same message for "no such user" and "wrong password", so usernames cannot be guessed this way.
            return Response({"detail": "Wrong username or password."}, status=status.HTTP_400_BAD_REQUEST)

        lockout.record_success(user)
        refresh = RefreshToken.for_user(user)
        update_last_login(None, user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": me_payload(user)})


class RefreshSerializer(TokenRefreshSerializer):
    """Like simplejwt's, but deactivated and admin-locked users cannot get new tokens.
    (An automatic lock after failed logins does not count: the real user's session keeps working.)"""

    def validate(self, attrs):
        try:
            token = RefreshToken(attrs["refresh"])  # checks signature, expiry and blacklist
        except TokenError as exc:
            raise InvalidToken(exc.args[0])
        user = User.objects.select_related("profile").filter(pk=token.payload.get(jwt_settings.USER_ID_CLAIM)).first()
        profile = getattr(user, "profile", None) if user else None
        if user is None or not user.is_active or (profile is not None and profile.locked_by_admin):
            raise InvalidToken("This account can no longer be used.")
        return super().validate(attrs)


class RefreshView(TokenRefreshView):
    serializer_class = RefreshSerializer


class LogoutView(APIView):
    """Blacklists the refresh token. The access token still works until it expires (see SIMPLE_JWT)."""

    permission_classes = [AllowAny]
    authentication_classes = []  # logging out must work even when the access token has expired

    def post(self, request):
        token = request.data.get("refresh", "")
        if token:
            try:
                RefreshToken(token).blacklist()
            except TokenError:
                pass  # already expired, invalid or blacklisted: nothing left to revoke
        return Response({"authenticated": False})


class MeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(me_payload(request.user))


# ---------- user management (admins only) ----------


class UserPagination(PageNumberPagination):
    page_size = 50


class UserViewSet(viewsets.ViewSet):
    """Admins manage employee and auditor accounts. Admin and superuser accounts can be viewed but not changed
    here (they are managed from the command line). Every change is logged with who did it."""

    permission_classes = [IsAdmin]
    lookup_value_regex = r"\d+"  # /api/users/abc/ -> 404 instead of an error

    def _users(self):
        return User.objects.select_related("profile").prefetch_related("groups").order_by("username")

    def _get(self, pk):
        return get_object_or_404(self._users(), pk=pk)

    def _get_manageable(self, pk):
        user = self._get(pk)
        if user.is_superuser or user.groups.filter(name=ADMIN).exists():
            raise PermissionDenied("Admin accounts are managed from the command line.")
        return user

    def _log(self, event: str, request, user, **fields):
        logger.info(event, extra={"fields": {"admin_id": request.user.pk, "user_id": user.pk, **fields}})

    def list(self, request):
        """?search=<text> (username / name), ?role=, ?department=, ?status=active|locked|deactivated.
        Deactivated users are only listed with status=deactivated."""
        users = self._users()
        params = request.query_params
        if search := params.get("search", "").strip():
            users = users.filter(
                Q(username__icontains=search) | Q(first_name__icontains=search) | Q(last_name__icontains=search)
            )
        if role := params.get("role"):
            users = users.filter(groups__name=role)
        if department := params.get("department"):
            users = users.filter(profile__department=department)
        now = timezone.now()
        locked = Q(profile__locked_by_admin=True) | Q(profile__locked_until__gt=now)
        state = params.get("status", "")
        if state == "deactivated":
            users = users.filter(is_active=False)
        elif state == "locked":
            users = users.filter(locked, is_active=True)
        elif state == "active":
            users = users.filter(is_active=True).exclude(locked)
        else:
            users = users.filter(is_active=True)
        paginator = UserPagination()
        page = paginator.paginate_queryset(users.distinct(), request, view=self)
        return paginator.get_paginated_response([user_summary(u) for u in page])

    def create(self, request):
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        self._log("user created", request, user, role=role_of(user), department=department_of(user))
        return Response(user_summary(self._get(user.pk)), status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        return Response(user_summary(self._get(pk)))

    def partial_update(self, request, pk=None):
        user = self._get_manageable(pk)
        serializer = UserUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        changed = sorted(k for k in serializer.validated_data if k != "username")
        if "password" in serializer.validated_data:
            lockout.log_out_everywhere(user)  # a new password set by an admin ends the old sessions
        self._log("user updated", request, user, changed=changed)  # field names only, never the values
        return Response(user_summary(self._get(pk)))

    def destroy(self, request, pk=None):
        """Deactivate (soft delete): no login, existing tokens refused, history kept."""
        user = self._get_manageable(pk)
        user.is_active = False
        user.save(update_fields=["is_active"])
        lockout.log_out_everywhere(user)
        self._log("user deactivated", request, user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        user = self._get_manageable(pk)
        user.is_active = True
        user.save(update_fields=["is_active"])
        self._log("user reactivated", request, user)
        return Response(user_summary(self._get(pk)))

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        user = self._get_manageable(pk)
        lockout.lock_by_admin(user)
        self._log("user locked", request, user)
        return Response(user_summary(self._get(pk)))

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        user = self._get_manageable(pk)
        lockout.unlock(user)
        self._log("user unlocked", request, user)
        return Response(user_summary(self._get(pk)))
