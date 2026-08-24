from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.settings import Settings


class ExportJobRepository:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        database = str(settings.monitor_firestore_database or "(default)").strip() or "(default)"
        self._client = client or firestore.Client(project=settings.monitor_project_id, database=database)
        self._collection = settings.monitor_firestore_export_collection

    def put(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        self._client.collection(self._collection).document(job_id).set(dict(job))
        return dict(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        document = self._client.collection(self._collection).document(str(job_id)).get()
        return dict(document.to_dict() or {}) if document.exists else None

    def cleanup_expired(self, *, limit: int = 200) -> int:
        query = (
            self._client.collection(self._collection)
            .where(filter=FieldFilter("expires_at", "<", datetime.now(timezone.utc)))
            .limit(max(1, min(int(limit), 500)))
        )
        documents = list(query.stream())
        for document in documents:
            document.reference.delete()
        return len(documents)

    @staticmethod
    def is_expired(job: dict[str, Any]) -> bool:
        value = job.get("expires_at")
        if not isinstance(value, datetime):
            return True
        timestamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return timestamp < datetime.now(timezone.utc)
