"""seed_kb must never touch uploaded documents. Fake embedder, no AWS calls. Needs the database."""
import pytest
from django.conf import settings
from django.core.management import call_command

from audit.models import AuditRun
from kb.models import KBDocument

DOC_A = "---\ntitle: Refund Policy\nstatus: active\n---\n# Refund Policy\n\n## India\nRefund within 3 days.\n"
DOC_B = "---\ntitle: Office Wifi\n---\n# Office Wifi\n\nThe password is on the notice board.\n"


class FakeClient:
    def __init__(self, cfg):
        pass

    def embed(self, text, purpose="document"):
        return [0.1] * settings.EMBED_DIM


@pytest.fixture(autouse=True)
def no_bedrock(monkeypatch):
    monkeypatch.setenv("EMBED_MODEL_ID", "fake-model")
    monkeypatch.setattr("kb.management.commands.seed_kb.BedrockClient", FakeClient)


@pytest.fixture
def docs_dir(tmp_path):
    (tmp_path / "refund-policy.md").write_text(DOC_A, encoding="utf-8")
    (tmp_path / "office-wifi.md").write_text(DOC_B, encoding="utf-8")
    return tmp_path


def make_upload() -> KBDocument:
    return KBDocument.objects.create(
        path="uploads/abc-notes.md", title="Notes", content_hash="u",
        source=KBDocument.Source.UPLOAD, state=KBDocument.State.LIVE,
    )


@pytest.mark.django_db
def test_seeded_documents_are_live_and_uploads_survive_removal_and_reset(docs_dir):
    upload = make_upload()

    call_command("seed_kb", docs_dir=str(docs_dir), no_audit=True)
    seeded = KBDocument.objects.filter(source=KBDocument.Source.FOLDER)
    assert set(seeded.values_list("path", flat=True)) == {"refund-policy.md", "office-wifi.md"}
    assert set(seeded.values_list("state", flat=True)) == {KBDocument.State.LIVE}

    (docs_dir / "office-wifi.md").unlink()  # removed from the folder -> removed from the KB
    call_command("seed_kb", docs_dir=str(docs_dir), no_audit=True)
    assert not KBDocument.objects.filter(path="office-wifi.md").exists()
    assert KBDocument.objects.filter(pk=upload.pk).exists()

    call_command("seed_kb", docs_dir=str(docs_dir), reset=True, no_audit=True)
    assert KBDocument.objects.filter(pk=upload.pk).exists()


@pytest.mark.django_db
def test_seed_queues_an_audit_only_when_something_changed(docs_dir):
    call_command("seed_kb", docs_dir=str(docs_dir))
    assert AuditRun.objects.filter(status=AuditRun.Status.QUEUED).count() == 1

    AuditRun.objects.all().delete()
    call_command("seed_kb", docs_dir=str(docs_dir))  # nothing changed
    assert AuditRun.objects.count() == 0
