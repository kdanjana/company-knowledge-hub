from datetime import date

from core.chunking import chunk_markdown, parse_date, parse_document


def test_parse_document_with_front_matter():
    raw = "---\ntitle: Refund Policy\neffective_date: 2025-04-01\n---\n# Refund Policy\nText"
    meta, body = parse_document(raw)
    assert meta["title"] == "Refund Policy"
    assert parse_date(meta["effective_date"]) == date(2025, 4, 1)
    assert body.startswith("# Refund Policy")


def test_parse_document_without_front_matter():
    raw = "# Support FAQ\nSome text"
    meta, body = parse_document(raw)
    assert meta == {}
    assert body == raw


def test_heading_path_keeps_region_with_text():
    body = "# Refund Policy\n\n## India\n\n### Refund window\nWithin 3 days.\n\n## UAE\nWithin 5 days.\n"
    chunks = chunk_markdown(body)
    assert [c.heading_path for c in chunks] == ["Refund Policy > India > Refund window", "Refund Policy > UAE"]
    assert chunks[0].text == "Within 3 days."
    assert [c.position for c in chunks] == [0, 1]


def test_empty_sections_are_skipped():
    body = "# Title\n\n## Response targets\n\n### Chat\nWithin 2 minutes.\n"
    chunks = chunk_markdown(body)
    assert len(chunks) == 1
    assert chunks[0].heading_path == "Title > Response targets > Chat"


def test_long_section_is_split_by_paragraph():
    paragraph = "word " * 60  # ~300 characters
    body = "# Title\n" + "\n\n".join([paragraph] * 5)
    chunks = chunk_markdown(body, max_chars=700)
    assert len(chunks) > 1
    assert all(c.heading_path == "Title" for c in chunks)


def test_content_hash_changes_when_text_changes():
    a = chunk_markdown("# T\nWithin 3 days.")[0]
    b = chunk_markdown("# T\nWithin 7 days.")[0]
    assert a.content_hash != b.content_hash
