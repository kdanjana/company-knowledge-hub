"""Account lockout after failed logins. Needs the database; no AWS calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import lockout
from accounts.models import Profile
from accounts.roles import EMPLOYEE

PASSWORD = "test-only-password"


@pytest.fixture
def user(db):
    user = User.objects.create_user(username="lockme", password=PASSWORD)
    user.groups.add(Group.objects.get_or_create(name=EMPLOYEE)[0])
    return user


def login(password: str, username: str = "lockme"):
    return APIClient().post("/api/auth/login/", {"username": username, "password": password}, format="json")


def test_four_wrong_passwords_do_not_lock(user):
    for _ in range(4):
        assert login("wrong-password").status_code == 400
    assert login(PASSWORD).status_code == 200


def test_the_fifth_wrong_password_locks_the_account(user):
    for _ in range(4):
        assert login("wrong-password").status_code == 400
    fifth = login("wrong-password")
    assert fifth.status_code == 403
    assert fifth.json()["detail"] == lockout.LOCKED_MESSAGE

    # Even the correct password is refused while locked.
    locked = login(PASSWORD)
    assert locked.status_code == 403
    assert "access" not in locked.json()


def test_a_correct_login_resets_the_count(user):
    for _ in range(4):
        login("wrong-password")
    assert login(PASSWORD).status_code == 200
    for _ in range(4):
        assert login("wrong-password").status_code == 400  # a fresh set of 5 attempts
    assert login(PASSWORD).status_code == 200


def test_the_lock_ends_by_itself(user):
    for _ in range(5):
        login("wrong-password")
    assert login(PASSWORD).status_code == 403

    Profile.objects.filter(user=user).update(locked_until=timezone.now() - timedelta(seconds=1))  # 30 min later
    assert login(PASSWORD).status_code == 200


def test_the_limit_comes_from_settings(user, settings):
    settings.LOGIN_MAX_FAILED_ATTEMPTS = 2
    assert login("wrong-password").status_code == 400
    assert login("wrong-password").status_code == 403


def test_an_unknown_username_is_never_locked(db):
    for _ in range(10):
        response = login("whatever", username="nobody")
        assert response.status_code == 400
        assert response.json()["detail"] == "Wrong username or password."


def test_failed_guesses_do_not_log_out_the_real_user(user):
    refresh = login(PASSWORD).json()["refresh"]
    for _ in range(5):
        login("wrong-password")  # someone else guessing: the account is now locked for new logins
    response = APIClient().post("/api/auth/refresh/", {"refresh": refresh}, format="json")
    assert response.status_code == 200  # the real user's session keeps working


@pytest.mark.django_db(transaction=True)
def test_failures_at_the_same_moment_are_all_counted():
    user = User.objects.create_user(username="racer", password=PASSWORD)

    def fail_once(_):
        try:
            return lockout.record_failure(user)
        finally:
            connection.close()  # each thread has its own database connection

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(fail_once, range(5)))

    assert results.count(True) == 1  # exactly one of the 5 failures locked the account
    assert lockout.is_locked(user)
