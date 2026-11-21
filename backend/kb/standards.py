"""The document naming and metadata standard for uploads. One place, used by the backend checks
and sent to the upload form (GET /api/kb/standards/), so both always use the same lists.

Name of an uploaded document:  uploads/<doc_id>__v<version>.md   e.g. uploads/refund-policy__v3.md
- doc_id:  stays the same for every version of a document (lowercase letters, digits, hyphens)
- version: set by the system (1 for a new document, latest + 1 for a new version)
The user can upload a file with any name; the original name is kept only for reference.

The existing knowledge base (seed_kb) keeps its own names and metadata: cleaning it up is the
auditor's job, and the answer key (ground_truth.yaml) refers to those names.
"""
from __future__ import annotations

import re

from accounts.roles import DEPARTMENTS

DOC_TYPES = ["policy", "procedure", "faq", "guide"]
REGIONS = ["All", "India", "UAE"]
AUDIENCES = ["employees", "customers", "delivery partners"]
OWNERS = DEPARTMENTS  # the team responsible for the document = one of the company's departments

DOC_ID_MAX = 60
TITLE_MAX = 120
DOC_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NON_ID_CHARS = re.compile(r"[^a-z0-9]+")


def slugify_doc_id(text: str) -> str:
    """'Refund Policy (UAE)' -> 'refund-policy-uae'."""
    slug = _NON_ID_CHARS.sub("-", (text or "").lower()).strip("-")
    return slug[:DOC_ID_MAX].strip("-")


def is_valid_doc_id(doc_id: str) -> bool:
    return bool(DOC_ID_RE.match(doc_id or "")) and len(doc_id) <= DOC_ID_MAX


def standard_path(doc_id: str, version: int) -> str:
    return f"uploads/{doc_id}__v{version}.md"


def as_dict() -> dict:
    return {
        "doc_types": DOC_TYPES,
        "regions": REGIONS,
        "audiences": AUDIENCES,
        "owners": OWNERS,
        "statuses": ["active", "draft"],
        "doc_id_pattern": DOC_ID_RE.pattern,
        "doc_id_max": DOC_ID_MAX,
        "title_max": TITLE_MAX,
        "name_format": "uploads/<doc_id>__v<version>.md",
    }
