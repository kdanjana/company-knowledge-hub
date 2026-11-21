"""Adapter: the only place that knows HOW the company's knowledge base is stored.

The auditor asks simple questions ("give me all chunks", "find similar chunks", ...).
To support another vector store (OpenSearch, Pinecone, a Bedrock Knowledge Base),
write another class with the same methods. The rest of the auditor does not change.

The Postgres adapter opens the connection in read-only mode: the auditor never writes
to the company's knowledge base.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import psycopg
from psycopg.rows import dict_row

from .records import KBChunkRecord, KBDocRecord


class KBAdapter(ABC):
    @abstractmethod
    def list_documents(self) -> list[KBDocRecord]: ...

    @abstractmethod
    def get_document(self, doc_id: int) -> KBDocRecord | None: ...

    @abstractmethod
    def get_documents(self, doc_ids) -> list[KBDocRecord]:
        """Only the listed documents (the chat needs the details of the few documents its search found)."""

    @abstractmethod
    def list_chunks(self) -> list[KBChunkRecord]: ...

    @abstractmethod
    def get_chunk(self, chunk_id: int) -> KBChunkRecord | None: ...

    @abstractmethod
    def find_similar(self, chunk_id: int, k: int) -> list[tuple[int, float]]:
        """Return up to k (chunk_id, cosine_similarity) pairs, most similar first, excluding the chunk itself."""

    @abstractmethod
    def search_by_vector(
        self, vector: list[float], k: int, departments: list[str] | None = None
    ) -> list[tuple[KBChunkRecord, float]]:
        """Most similar chunks. departments=None -> all documents; otherwise only documents
        open to everyone or to one of the given departments."""

    @abstractmethod
    def get_neighbors(self, chunk_id: int, window: int = 1) -> list[KBChunkRecord]:
        """Chunks just before/after this one in the same document."""

    def close(self) -> None:
        pass


_CHUNK_COLUMNS = "id, document_id, position, heading_path, text, content_hash"
_DOC_COLUMNS = (
    "d.id, d.path, d.title, d.doc_type, d.version, d.effective_date, d.status, d.owner, d.departments, "
    "d.source, d.state, d.doc_id, d.region, d.audience"
)

# Documents the audit checks: everything that has chunks and is not rejected.
# (processing / failed documents have no chunks yet; rejected ones must not be compared any more)
AUDITED_STATES = ["live", "awaiting_audit", "awaiting_approval"]


def _chunk(row: dict) -> KBChunkRecord:
    return KBChunkRecord(
        id=row["id"],
        doc_id=row["document_id"],
        position=row["position"],
        heading_path=row["heading_path"],
        text=row["text"],
        content_hash=row["content_hash"],
    )


def _doc(row: dict) -> KBDocRecord:
    return KBDocRecord(
        id=row["id"],
        path=row["path"],
        title=row["title"],
        doc_type=row["doc_type"],
        version=row["version"],
        effective_date=row["effective_date"],
        status=row["status"],
        owner=row["owner"],
        chunk_count=row.get("chunk_count", 0) or 0,
        departments=list(row.get("departments") or []),
        source=row.get("source") or "folder",
        state=row.get("state") or "live",
        doc_id=row.get("doc_id") or "",
        region=row.get("region") or "",
        audience=row.get("audience") or "",
    )


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


class PgvectorKBAdapter(KBAdapter):
    """Reads the simulated QuickBite KB tables (kb_documents, kb_chunks) in Postgres + pgvector."""

    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self.conn.execute("SET default_transaction_read_only = on")

    def close(self) -> None:
        self.conn.close()

    def list_documents(self) -> list[KBDocRecord]:
        rows = self.conn.execute(
            f"""
            SELECT {_DOC_COLUMNS}, COUNT(c.id) AS chunk_count
            FROM kb_documents d
            LEFT JOIN kb_chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY d.path
            """
        ).fetchall()
        return [_doc(r) for r in rows]

    def get_document(self, doc_id: int) -> KBDocRecord | None:
        row = self.conn.execute(f"SELECT {_DOC_COLUMNS} FROM kb_documents d WHERE d.id = %s", (doc_id,)).fetchone()
        return _doc(row) if row else None

    def get_documents(self, doc_ids) -> list[KBDocRecord]:
        ids = sorted({int(i) for i in doc_ids})
        if not ids:
            return []
        rows = self.conn.execute(
            f"SELECT {_DOC_COLUMNS} FROM kb_documents d WHERE d.id = ANY(%(ids)s)", {"ids": ids}
        ).fetchall()
        return [_doc(r) for r in rows]

    def list_chunks(self) -> list[KBChunkRecord]:
        """Chunks the audit checks (see AUDITED_STATES)."""
        rows = self.conn.execute(
            """
            SELECT c.id, c.document_id, c.position, c.heading_path, c.text, c.content_hash
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.document_id
            WHERE d.state = ANY(%(states)s)
            ORDER BY c.document_id, c.position
            """,
            {"states": AUDITED_STATES},
        ).fetchall()
        return [_chunk(r) for r in rows]

    def get_chunk(self, chunk_id: int) -> KBChunkRecord | None:
        row = self.conn.execute(f"SELECT {_CHUNK_COLUMNS} FROM kb_chunks WHERE id = %s", (chunk_id,)).fetchone()
        return _chunk(row) if row else None

    def find_similar(self, chunk_id: int, k: int) -> list[tuple[int, float]]:
        # <=> is pgvector's cosine distance; similarity = 1 - distance.
        rows = self.conn.execute(
            """
            SELECT c.id, 1 - (c.embedding <=> src.embedding) AS similarity
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.document_id,
                 (SELECT embedding FROM kb_chunks WHERE id = %(id)s) AS src
            WHERE c.id <> %(id)s
              AND d.state = ANY(%(states)s)
            ORDER BY c.embedding <=> src.embedding
            LIMIT %(k)s
            """,
            {"id": chunk_id, "k": k, "states": AUDITED_STATES},
        ).fetchall()
        return [(r["id"], float(r["similarity"])) for r in rows]

    def search_by_vector(
        self, vector: list[float], k: int, departments: list[str] | None = None
    ) -> list[tuple[KBChunkRecord, float]]:
        # Only live documents that are not superseded (unchecked uploads and replaced versions are never used).
        # Access filter: documents with an empty departments list are open to everyone;
        # `?|` is true if the JSON array contains any of the given department names.
        rows = self.conn.execute(
            """
            SELECT c.id, c.document_id, c.position, c.heading_path, c.text, c.content_hash,
                   1 - (c.embedding <=> %(v)s::vector) AS similarity
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.document_id
            WHERE d.state = 'live'
              AND lower(d.status) <> 'superseded'
              AND (%(see_all)s
                   OR jsonb_array_length(d.departments) = 0
                   OR d.departments ?| %(depts)s::text[])
            ORDER BY c.embedding <=> %(v)s::vector
            LIMIT %(k)s
            """,
            {
                "v": _vector_literal(vector),
                "k": k,
                "see_all": departments is None,
                "depts": list(departments or []) or [""],
            },
        ).fetchall()
        return [(_chunk(r), float(r["similarity"])) for r in rows]

    def get_neighbors(self, chunk_id: int, window: int = 1) -> list[KBChunkRecord]:
        rows = self.conn.execute(
            """
            SELECT n.id, n.document_id, n.position, n.heading_path, n.text, n.content_hash
            FROM kb_chunks n
            JOIN kb_chunks c ON n.document_id = c.document_id
            WHERE c.id = %(id)s
              AND n.id <> c.id
              AND n.position BETWEEN c.position - %(w)s AND c.position + %(w)s
            ORDER BY n.position
            """,
            {"id": chunk_id, "w": window},
        ).fetchall()
        return [_chunk(r) for r in rows]
