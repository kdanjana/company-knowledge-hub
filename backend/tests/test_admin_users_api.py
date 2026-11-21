"""User management API (/api/users/, admins only). Needs the database; no AWS calls."""
import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient

from accounts import lockout
from accounts.roles import ADMIN, AUDITOR, EMPLOYEE, department_of, role_of
from chat.models import Conversation

URL = "/api/users/"
OLD_PASSWORD = "test-only-password"
GOOD_PASSWORD = "Blue-Kettle-4821"


def make_user(username: str, role: str, department: str = "Legal", superuser: bool = False) -> User:
    if superuser:
        user = User.objects.create_superuser(username=username, password=OLD_PASSWORD)
    else:
        user = User.objects.create_user(username=username, password=OLD_PASSWORD)
        user.groups.add(Group.objects.get_or_create(name=role)[0])
    user.profile.department = department
    user.profile.save()
    return user


def new_user(**changes) -> dict:
    data = {"username": "d1u1", "password": GOOD_PASSWORD, "department": "Finance"}
    data.update(changes)
    return data


def login(username: str, password: str):
    return APIClient().post("/api/auth/login/", {"username": username, "password": password}, format="json")


@pytest.fixture
def admin_api(db):
    api = APIClient()
    api.force_authenticate(make_user("boss", ADMIN, "IT"))
    return api


# ---------- who may use it ----------


@pytest.mark.django_db
def test_only_admins_can_manage_users():
    for role in (EMPLOYEE, AUDITOR):
        api = APIClient()
        api.force_authenticate(make_user(f"someone-{role}", role))
        assert api.get(URL).status_code == 403
        assert api.post(URL, new_user(), format="json").status_code == 403
    assert APIClient().get(URL).status_code == 401  # not logged in
    assert not User.objects.filter(username="d1u1").exists()


# ---------- create ----------


def test_admin_creates_an_employee_and_an_auditor(admin_api):
    response = admin_api.post(URL, new_user(first_name="Ravi", last_name="Kumar"), format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == EMPLOYEE and body["department"] == "Finance" and body["full_name"] == "Ravi Kumar"
    assert "password" not in body

    response = admin_api.post(URL, new_user(username="aud2", role=AUDITOR, department="Legal"), format="json")
    assert response.status_code == 201
    assert response.json()["role"] == AUDITOR

    assert authenticate(username="d1u1", password=GOOD_PASSWORD) is not None
    assert role_of(User.objects.get(username="aud2")) == AUDITOR


def test_admins_cannot_be_created_through_the_api(admin_api):
    response = admin_api.post(URL, new_user(role=ADMIN), format="json")
    assert response.status_code == 400
    assert "role" in response.json()
    assert not User.objects.filter(username="d1u1").exists()


def test_create_checks_username_department_and_password(admin_api):
    assert admin_api.post(URL, new_user(), format="json").status_code == 201
    assert admin_api.post(URL, new_user(username="D1U1"), format="json").status_code == 400  # taken, any case
    assert admin_api.post(URL, new_user(username="x2", department="Space"), format="json").status_code == 400
    for weak in ["abc", "12345678", "password", "87654321098"]:
        response = admin_api.post(URL, new_user(username="x3", password=weak), format="json")
        assert response.status_code == 400 and "password" in response.json()
    assert not User.objects.filter(username__in=["x2", "x3"]).exists()


# ---------- list / detail ----------


def test_list_search_and_filters(admin_api):
    make_user("fin1", EMPLOYEE, "Finance")
    make_user("fin2", AUDITOR, "Finance")
    make_user("ops1", EMPLOYEE, "Operations")

    def names(query: str = "") -> set[str]:
        body = admin_api.get(URL + query).json()
        return {u["username"] for u in body["results"]}

    assert {"fin1", "fin2", "ops1", "boss"} <= names()
    assert names("?department=Finance") == {"fin1", "fin2"}
    assert names("?role=auditor") == {"fin2"}
    assert names("?search=ops") == {"ops1"}


def test_deactivated_users_are_listed_only_when_asked(admin_api):
    gone = make_user("gone", EMPLOYEE)
    assert admin_api.delete(f"{URL}{gone.id}/").status_code == 204
    assert "gone" not in {u["username"] for u in admin_api.get(URL).json()["results"]}
    assert {u["username"] for u in admin_api.get(URL + "?status=deactivated").json()["results"]} == {"gone"}


def test_detail_shows_the_lock_status(admin_api):
    user = make_user("victim", EMPLOYEE)
    for _ in range(5):
        login("victim", "wrong-password")
    body = admin_api.get(f"{URL}{user.id}/").json()
    assert body["is_locked"] is True
    assert body["locked_by_admin"] is False
    assert body["locked_until"] is not None


# ---------- edit ----------


def test_edit_role_department_and_password(admin_api):
    user = make_user("emp", EMPLOYEE, "Operations")
    response = admin_api.patch(
        f"{URL}{user.id}/", {"role": AUDITOR, "department": "HR", "password": GOOD_PASSWORD}, format="json"
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert role_of(user) == AUDITOR
    assert department_of(user) == "HR"
    assert authenticate(username="emp", password=GOOD_PASSWORD) is not None
    assert authenticate(username="emp", password=OLD_PASSWORD) is None


def test_edit_refuses_admin_role_username_change_and_weak_password(admin_api):
    user = make_user("emp", EMPLOYEE)
    assert admin_api.patch(f"{URL}{user.id}/", {"role": ADMIN}, format="json").status_code == 400
    assert admin_api.patch(f"{URL}{user.id}/", {"username": "renamed"}, format="json").status_code == 400
    assert admin_api.patch(f"{URL}{user.id}/", {"password": "12345678"}, format="json").status_code == 400
    assert role_of(User.objects.get(pk=user.id)) == EMPLOYEE


def test_admin_and_superuser_accounts_cannot_be_changed(admin_api):
    for target in (make_user("other-admin", ADMIN), make_user("root", "", superuser=True)):
        assert admin_api.patch(f"{URL}{target.id}/", {"department": "HR"}, format="json").status_code == 403
        assert admin_api.delete(f"{URL}{target.id}/").status_code == 403
        assert admin_api.post(f"{URL}{target.id}/lock/").status_code == 403
        assert User.objects.get(pk=target.id).is_active


# ---------- deactivate / reactivate ----------


def test_deactivate_blocks_login_and_tokens_but_keeps_the_data(admin_api):
    user = make_user("leaver", EMPLOYEE)
    Conversation.objects.create(user=user, title="my questions")
    tokens = login("leaver", OLD_PASSWORD).json()

    assert admin_api.delete(f"{URL}{user.id}/").status_code == 204

    assert login("leaver", OLD_PASSWORD).status_code == 400
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert api.get("/api/chat/conversations/").status_code == 401  # access token refused at once
    assert APIClient().post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code == 401
    assert Conversation.objects.filter(user=user).exists()  # history kept


def test_reactivate_allows_login_again(admin_api):
    user = make_user("back", EMPLOYEE)
    admin_api.delete(f"{URL}{user.id}/")
    assert admin_api.post(f"{URL}{user.id}/reactivate/").status_code == 200
    assert login("back", OLD_PASSWORD).status_code == 200


# ---------- lock / unlock ----------


def test_admin_lock_blocks_login_and_refresh_until_unlocked(admin_api):
    user = make_user("suspect", EMPLOYEE)
    refresh = login("suspect", OLD_PASSWORD).json()["refresh"]

    assert admin_api.post(f"{URL}{user.id}/lock/").json()["locked_by_admin"] is True
    assert login("suspect", OLD_PASSWORD).status_code == 403
    assert APIClient().post("/api/auth/refresh/", {"refresh": refresh}, format="json").status_code == 401

    assert admin_api.post(f"{URL}{user.id}/unlock/").json()["is_locked"] is False
    assert login("suspect", OLD_PASSWORD).status_code == 200


def test_unlock_ends_an_automatic_lock_early(admin_api):
    user = make_user("forgetful", EMPLOYEE)
    for _ in range(5):
        login("forgetful", "wrong-password")
    assert lockout.is_locked(user)

    admin_api.post(f"{URL}{user.id}/unlock/")
    assert login("forgetful", OLD_PASSWORD).status_code == 200
