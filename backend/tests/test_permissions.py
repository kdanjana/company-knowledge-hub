"""Role checks through the real API. Needs the database (Docker Postgres running); no AWS calls."""
import pytest
from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient

from accounts.roles import AUDITOR, EMPLOYEE, allowed_departments

AUDIT_URLS = ["/api/runs/", "/api/findings/", "/api/evaluations/"]


def make_user(username: str, role: str, department: str = "") -> User:
    user = User.objects.create_user(username=username, password="test-only-password")
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    user.profile.department = department
    user.profile.save()
    return user


@pytest.fixture
def api():
    return APIClient()


@pytest.mark.django_db
def test_anonymous_user_is_refused(api):
    # 401 = not logged in (no token). 403 is for "logged in, but not allowed".
    for url in AUDIT_URLS + ["/api/chat/conversations/"]:
        assert api.get(url).status_code == 401, url


@pytest.mark.django_db
def test_employee_cannot_open_audit_pages(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Operations"))
    for url in AUDIT_URLS:
        assert api.get(url).status_code == 403, url


@pytest.mark.django_db
def test_auditor_can_open_audit_pages(api):
    api.force_authenticate(make_user("aud", AUDITOR, "Legal"))
    for url in AUDIT_URLS:
        assert api.get(url).status_code == 200, url


@pytest.mark.django_db
def test_both_roles_can_use_chat_and_only_see_their_own_conversations(api):
    employee = make_user("emp", EMPLOYEE, "Operations")
    auditor = make_user("aud", AUDITOR, "Legal")

    api.force_authenticate(employee)
    assert api.post("/api/chat/conversations/", {"title": "my chat"}, format="json").status_code == 201

    api.force_authenticate(auditor)
    assert api.get("/api/chat/conversations/").json() == []

    api.force_authenticate(employee)
    assert len(api.get("/api/chat/conversations/").json()) == 1


@pytest.mark.django_db
def test_me_reports_role_and_department(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Operations"))
    me = api.get("/api/auth/me/").json()
    assert me["authenticated"] is True
    assert me["role"] == "employee"
    assert me["department"] == "Operations"


@pytest.mark.django_db
def test_document_access_by_role():
    assert allowed_departments(make_user("aud", AUDITOR, "Legal")) is None  # auditors see everything
    assert allowed_departments(make_user("emp", EMPLOYEE, "Operations")) == ["Operations"]
    assert allowed_departments(make_user("new", EMPLOYEE)) == []  # only documents open to everyone
