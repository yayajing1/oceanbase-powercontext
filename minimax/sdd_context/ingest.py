"""Importing a business project into the seekdb / OceanBase document store."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .db import DocumentStore, new_batch_id
from .documents import collect_documents

__all__ = ["ingest_project"]


def ingest_project(config: Config, store: DocumentStore | None = None) -> dict:
    """Collect the documents of the project and store them in the database.

    The import is idempotent: documents are addressed by a stable
    ``doc_id`` and rows of a re-imported file that no longer exist are
    removed, so a changed specification is really replaced instead of
    being shadowed by an older row.
    """
    project_root = Path(config.project_root).resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"the project root does not exist: {project_root}")

    store = store or DocumentStore(config)
    schema = store.ensure_schema()
    documents = collect_documents(project_root)
    batch_id = new_batch_id()
    result = store.upsert_documents(documents, batch_id, str(project_root))
    kinds: dict[str, int] = {}
    for document in documents:
        kinds[document.kind] = kinds.get(document.kind, 0) + 1
    return {
        "batch_id": batch_id,
        "project_root": str(project_root),
        "documents": len(documents),
        "kinds": kinds,
        "schema": schema,
        **result,
    }
