from __future__ import annotations

from functools import lru_cache

from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings


@lru_cache(maxsize=1)
def get_bigquery_metrics_service() -> BigQueryMetricsService:
    settings: Settings = get_settings()
    return BigQueryMetricsService(settings)


@lru_cache(maxsize=1)
def get_firestore_history_service() -> FirestoreHistoryService:
    settings: Settings = get_settings()
    return FirestoreHistoryService(settings)
