from __future__ import annotations

from functools import lru_cache

from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.conversation_history import ConversationHistoryRepository
from app.repositories.export_jobs import ExportJobRepository
from app.repositories.pipeline_repository import PipelineRepository
from app.repositories.user_directory import UserDirectoryRepository
from app.services.analytics_service import AnalyticsService
from app.services.user_management import UserManagementService
from app.settings import get_settings


@lru_cache(maxsize=1)
def get_analytics_repository() -> AnalyticsRepository:
    return AnalyticsRepository(get_settings())


@lru_cache(maxsize=1)
def get_pipeline_repository() -> PipelineRepository:
    return PipelineRepository(get_settings())


@lru_cache(maxsize=1)
def get_user_directory_repository() -> UserDirectoryRepository:
    return UserDirectoryRepository(get_settings())


@lru_cache(maxsize=1)
def get_conversation_history_repository() -> ConversationHistoryRepository:
    return ConversationHistoryRepository(get_settings())


@lru_cache(maxsize=1)
def get_export_job_repository() -> ExportJobRepository:
    return ExportJobRepository(get_settings())


@lru_cache(maxsize=1)
def get_user_management_service() -> UserManagementService:
    settings = get_settings()
    return UserManagementService(
        directory=get_user_directory_repository(),
        identity_secret=settings.monitor_identity_hmac_key,
        audit_retention_days=settings.monitor_admin_change_retention_days,
    )


@lru_cache(maxsize=1)
def get_analytics_service() -> AnalyticsService:
    return AnalyticsService(
        analytics=get_analytics_repository(),
        pipeline=get_pipeline_repository(),
        directory=get_user_directory_repository(),
        conversations=get_conversation_history_repository(),
        settings=get_settings(),
    )


def clear_dependency_caches() -> None:
    get_analytics_service.cache_clear()
    get_user_management_service.cache_clear()
    get_conversation_history_repository.cache_clear()
    get_export_job_repository.cache_clear()
    get_user_directory_repository.cache_clear()
    get_pipeline_repository.cache_clear()
    get_analytics_repository.cache_clear()
