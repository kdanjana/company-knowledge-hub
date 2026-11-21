"""JWT login: tokens, refresh rotation, logout blacklist. Needs the database; no AWS calls."""
import pytest
from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient

from accounts.roles import EMPLOYEE

PASSWORD = "test-only-password"


@pytest.fixture
def user(db):
    user = User.objects.create_user(username="jwtuser", password=PASSWORD)
    user.groups.add(Group.objects.get_or_create(name=EMPLOYEE)[0])
    user.profile.department = "Operations"
    user.profile.save()
    return user


def login(api: APIClient, password: str = PASSWORD):
    return api.post("/api/auth/login/", {"username": "jwtuser", "password": password}, format="json")


def test_login_returns_tokens_and_user(user):
    response = login(APIClient())
    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["username"] == "jwtuser"
    assert body["user"]["role"] == EMPLOYEE
    assert body["user"]["department"] == "Operations"
    user.refresh_from_db()
    assert user.last_login is not None


def test_wrong_password_gives_no_tokens(user):
    response = login(APIClient(), password="wrong-password")
    assert response.status_code == 400
    assert "access" not in response.json()


def test_me_uses_the_bearer_token(user):
    api = APIClient()
    assert api.get("/api/auth/me/").json() == {"authenticated": False}

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login(api).json()['access']}")
    me = api.get("/api/auth/me/").json()
    assert me["authenticated"] is True
    assert me["username"] == "jwtuser"


def test_no_csrf_token_is_needed_with_a_bearer_token(user):
    api = APIClient(enforce_csrf_checks=True)  # would refuse a cookie-based POST without a CSRF token
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login(api).json()['access']}")
    assert api.post("/api/chat/conversations/", {"title": "hello"}, format="json").status_code == 201


def test_refresh_gives_new_tokens_and_the_old_refresh_token_stops_working(user):
    api = APIClient()
    first_refresh = login(api).json()["refresh"]

    response = api.post("/api/auth/refresh/", {"refresh": first_refresh}, format="json")
    assert response.status_code == 200
    body = response.json()
    assert body["access"]
    assert body["refresh"] and body["refresh"] != first_refresh  # rotated

    # The old refresh token was blacklisted by the rotation.
    assert api.post("/api/auth/refresh/", {"refresh": first_refresh}, format="json").status_code == 401


def test_logout_blacklists_the_refresh_token(user):
    api = APIClient()
    refresh = login(api).json()["refresh"]

    assert api.post("/api/auth/logout/", {"refresh": refresh}, format="json").status_code == 200
    assert api.post("/api/auth/refresh/", {"refresh": refresh}, format="json").status_code == 401


def test_logout_without_a_valid_token_still_answers(user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer expired-or-garbage")
    assert api.post("/api/auth/logout/", {"refresh": "garbage"}, format="json").status_code == 200


def test_a_garbage_token_is_refused(user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    assert api.get("/api/chat/conversations/").status_code == 401
