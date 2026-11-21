"""Upload API and auditor approval. Needs the database (Docker Postgres); no AWS calls."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from accounts.roles import AUDITOR, EMPLOYEE
from kb.models import KBDocument
from kb.services import UploadFields, save_upload
from tests.test_permissions import make_user

URL = "/api/kb/documents/"

DOC = b"""# Refund update

## Refund window
Customers can request a refund within 10 days.
"""

DOC_WITH_FRONT_MATTER = b"""---
doc_id: refund-update
title: Refund Update
doc_type: policy
version: 7
effective_date: 2026-01-10
status: active
owner: Customer Support
region: India
---
# Refund update

## Refund window
Customers can request a refund within 10 days.
"""

FORM = {
    "title": "Refund update",
    "doc_id": "refund-update",
    "doc_type": "policy",
    "effective_date": "2026-01-10",
    "status": "active",
    "owner": "Customer Support",
}


@pytest.fixture(autouse=True)
def upload_dir(settings, tmp_path):
    settings.UPLOAD_DIR = tmp_path
    settings.UPLOAD_MAX_MB = 1
    return tmp_path


@pytest.fixture
def api():
    return APIClient()


def post(api, content=DOC, name="refund-update.md", **fields):
    data = {**FORM, **fields, "file": SimpleUploadedFile(name, content, content_type="text/markdown")}
    return api.post(URL, data, format="multipart")


def live_doc(path="refund-policy-2025.md", departments=None, version="2") -> KBDocument:
    return KBDocument.objects.create(
        path=path, title="Refund Policy", content_hash=path, status="active", version=version,
        departments=departments or [],
    )


@pytest.mark.django_db
def test_upload_saves_file_and_waits_for_processing(api, upload_dir):
    user = make_user("emp", EMPLOYEE, "Customer Support")
    api.force_authenticate(user)
    response = post(api)
    assert response.status_code == 201, response.json()

    doc = KBDocument.objects.get(pk=response.json()["id"])
    assert doc.source == KBDocument.Source.UPLOAD
    assert doc.state == KBDocument.State.PROCESSING
    assert doc.uploaded_by == user and doc.uploaded_at is not None
    assert doc.departments == ["Customer Support"]  # default: my department only
    assert (upload_dir / doc.file_path).read_bytes() == DOC
    # Standard name, whatever the uploaded file was called; version set by the system.
    assert doc.path == "uploads/refund-update__v1.md"
    assert (doc.doc_id, doc.version, doc.original_name) == ("refund-update", "1", "refund-update.md")


@pytest.mark.django_db
def test_upload_for_everyone(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "HR"))
    response = post(api, visibility="everyone")
    assert response.status_code == 201
    assert response.json()["departments"] == []


@pytest.mark.django_db
def test_front_matter_fills_empty_form_fields(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    data = {"file": SimpleUploadedFile("refund.md", DOC_WITH_FRONT_MATTER)}
    response = api.post(URL, data, format="multipart")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert (body["doc_id"], body["title"], body["effective_date"], body["owner"], body["region"]) == (
        "refund-update", "Refund Update", "2026-01-10", "Customer Support", "India",
    )
    assert body["version"] == "1"  # "version: 7" in the file is ignored: the system sets it


@pytest.mark.django_db
def test_doc_id_is_made_from_the_title_when_empty(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    response = post(api, doc_id="", title="Refund Policy (UAE)")
    assert response.status_code == 201, response.json()
    assert response.json()["path"] == "uploads/refund-policy-uae__v1.md"


@pytest.mark.django_db
def test_new_version_gets_the_same_id_and_the_next_number(api):
    old = live_doc()  # seeded "Refund Policy", version 2, no doc_id
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    response = post(api, doc_id="", title="", replaces=old.id)
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["path"] == "uploads/refund-policy__v3.md"
    assert (body["doc_id"], body["version"], body["title"], body["replaces"]) == ("refund-policy", "3", "Refund Policy", old.id)


@pytest.mark.django_db
def test_a_rejected_version_number_is_never_reused(api):
    old = live_doc()
    KBDocument.objects.create(
        path="uploads/refund-policy__v3.md", doc_id="refund-policy", version="3", title="Refund Policy",
        content_hash="rejected", state=KBDocument.State.REJECTED,
    )
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    response = post(api, replaces=old.id)
    assert response.status_code == 201, response.json()
    assert response.json()["path"] == "uploads/refund-policy__v4.md"


@pytest.mark.django_db
def test_doc_id_already_used_points_to_new_version(api):
    live_doc()  # "Refund Policy" -> refund-policy
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    response = post(api, doc_id="refund-policy")
    assert response.status_code == 400
    assert "already used" in response.json()["detail"] and "New version of" in response.json()["detail"]


@pytest.mark.django_db
def test_name_preview_matches_the_saved_name(api):
    old = live_doc()
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    assert api.get(f"{URL}next-name/?doc_id=refund-update").json()["name"] == "uploads/refund-update__v1.md"
    assert api.get(f"{URL}next-name/?replaces={old.id}").json()["name"] == "uploads/refund-policy__v3.md"
    assert "not valid" in api.get(f"{URL}next-name/", {"doc_id": "Refund Policy!"}).json()["error"]


@pytest.mark.django_db
def test_standards_are_served_to_the_form(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    data = api.get("/api/kb/standards/").json()
    assert data["doc_types"] == ["policy", "procedure", "faq", "guide"]
    assert "Customer Support" in data["owners"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content, name, fields, message",
    [
        (DOC, "refund.txt", {}, "Only Markdown"),
        (b"\xff\xfe not utf-8 \xff", "refund.md", {}, "UTF-8"),
        (b"---\ntitle: x\n---\n   \n", "refund.md", {}, "no text"),
        (DOC, "refund.md", {"owner": ""}, "Owner is required"),
        (DOC, "refund.md", {"owner": "CS team"}, "Owner must be one of"),
        (DOC, "refund.md", {"doc_type": "memo"}, "Type must be one of"),
        (DOC, "refund.md", {"region": "Mars"}, "Region must be one of"),
        (DOC, "refund.md", {"doc_id": "Refund Policy!"}, "not valid"),
        (DOC, "refund.md", {"title": "x" * 121}, "at most 120"),
        (DOC, "refund.md", {"effective_date": "10/01/2026"}, "Effective date"),
        (DOC, "refund.md", {"status": "superseded"}, "Status must be"),
    ],
)
def test_bad_uploads_are_refused_and_leave_no_file(api, upload_dir, content, name, fields, message):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    response = post(api, content=content, name=name, **fields)
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert KBDocument.objects.count() == 0
    assert list(upload_dir.iterdir()) == []


@pytest.mark.django_db
def test_too_large(api, settings):
    settings.UPLOAD_MAX_MB = 0.0001  # about 100 bytes
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    response = post(api, content=DOC + b"x" * 200)
    assert response.status_code == 400 and "larger than" in response.json()["detail"]


@pytest.mark.django_db
def test_same_content_twice_is_refused(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    assert post(api).status_code == 201
    response = post(api, name="copy.md")
    assert response.status_code == 400 and "already exists" in response.json()["detail"]


@pytest.mark.django_db
def test_department_visibility_needs_a_department(api):
    api.force_authenticate(make_user("emp", EMPLOYEE))  # no department
    response = post(api)
    assert response.status_code == 400 and "no department" in response.json()["detail"]


@pytest.mark.django_db
def test_file_is_removed_when_the_database_save_fails(monkeypatch, upload_dir):
    user = make_user("emp", EMPLOYEE, "Customer Support")

    def broken_create(**kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(KBDocument.objects, "create", broken_create)
    with pytest.raises(RuntimeError):
        save_upload(user, "refund.md", DOC, UploadFields(**FORM))
    assert list(upload_dir.iterdir()) == []


@pytest.mark.django_db
def test_cannot_replace_a_document_you_cannot_see(api):
    hidden = live_doc("ops-handbook.md", departments=["Operations"])
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    response = post(api, replaces=hidden.id)
    assert response.status_code == 400 and "cannot see" in response.json()["detail"]


@pytest.mark.django_db
def test_employees_only_see_live_documents_and_their_own_uploads(api):
    live_doc("open.md")
    live_doc("ops.md", departments=["Operations"])
    KBDocument.objects.create(path="pending.md", title="x", content_hash="p", state=KBDocument.State.AWAITING_AUDIT)
    user = make_user("emp", EMPLOYEE, "Customer Support")
    api.force_authenticate(user)
    assert post(api).status_code == 201  # own upload, still processing

    paths = {d["path"] for d in api.get(URL).json()}
    assert "open.md" in paths
    assert "ops.md" not in paths and "pending.md" not in paths
    assert any(p.startswith("uploads/") for p in paths)


@pytest.mark.django_db
def test_only_auditors_can_approve_and_approval_supersedes_the_old_version(api):
    old = live_doc()
    new = KBDocument.objects.create(
        path="uploads/new.md", title="Refund Policy", content_hash="n", replaces=old,
        state=KBDocument.State.AWAITING_APPROVAL, source=KBDocument.Source.UPLOAD,
    )

    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    assert api.post(f"{URL}{new.id}/approve/").status_code == 403

    api.force_authenticate(make_user("aud", AUDITOR, "Legal"))
    response = api.post(f"{URL}{new.id}/approve/")
    assert response.status_code == 200
    new.refresh_from_db()
    old.refresh_from_db()
    assert new.state == KBDocument.State.LIVE
    assert old.status == "superseded" and old.state == KBDocument.State.LIVE

    assert api.post(f"{URL}{new.id}/approve/").status_code == 400  # only once


@pytest.mark.django_db
def test_reject_keeps_the_old_version(api):
    old = live_doc()
    new = KBDocument.objects.create(
        path="uploads/new.md", title="Refund Policy", content_hash="n", replaces=old,
        state=KBDocument.State.AWAITING_APPROVAL,
    )
    api.force_authenticate(make_user("aud", AUDITOR, "Legal"))
    assert api.post(f"{URL}{new.id}/reject/").status_code == 200
    new.refresh_from_db()
    old.refresh_from_db()
    assert new.state == KBDocument.State.REJECTED
    assert old.status == "active"


@pytest.mark.django_db
def test_original_text_of_an_upload(api):
    api.force_authenticate(make_user("emp", EMPLOYEE, "Customer Support"))
    doc_id = post(api).json()["id"]
    response = api.get(f"{URL}{doc_id}/original/")
    assert response.status_code == 200
    assert response.json()["text"] == DOC.decode()
