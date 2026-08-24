from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery

from app.settings import Settings


class PipelineRepository:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        self._table = f"`{settings.monitor_project_id}.{settings.monitor_bq_dataset}.pipeline_state`"

    def data_through(self) -> datetime | None:
        config = bigquery.QueryJobConfig(
            maximum_bytes_billed=max(1, int(self._settings.monitor_query_maximum_bytes)),
            use_query_cache=True,
        )
        rows = list(
            self._client.query(
                f"SELECT MAX(data_through) AS data_through FROM {self._table} WHERE status = 'succeeded'",
                job_config=config,
                location=self._settings.monitor_bq_location,
            ).result()
        )
        if not rows:
            return None
        value = rows[0].get("data_through")
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None
