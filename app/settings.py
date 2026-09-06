from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    monitor_project_id: str = Field(default="lcs-developer-483404")
    monitor_source_service: str = Field(default="lcs-rag-app")
    # News/Society usage is an additive source. Both values stay empty until
    # its schema and Logging source have been prepared; incomplete values are
    # diagnosed only by that branch and never prevent the Chat app starting.
    monitor_news_usage_source_service: str = Field(default="")
    monitor_news_usage_start_at: str = Field(default="")

    monitor_bq_dataset: str = Field(default="oura_navi_monitor")
    monitor_bq_location: str = Field(default="US")

    monitor_firestore_database: str = Field(default="lcs-user-data")
    monitor_firestore_chat_collection: str = Field(default="chat_users")
    monitor_firestore_user_collection: str = Field(default="monitor_users")
    monitor_firestore_label_collection: str = Field(default="monitor_labels")
    monitor_firestore_admin_change_collection: str = Field(default="monitor_admin_changes")
    monitor_firestore_export_collection: str = Field(default="monitor_export_jobs")
    monitor_firestore_unique_claim_collection: str = Field(default="monitor_unique_claims")
    monitor_admin_allowlist: str = Field(default="")
    monitor_allow_unverified_local: bool = Field(default=False)
    monitor_cors_allowed_origins: str = Field(default="")

    monitor_default_days: int = Field(default=7)
    monitor_firestore_read_timeout_seconds: int = Field(default=120)
    monitor_firestore_read_page_size: int = Field(default=1000)
    monitor_query_maximum_bytes: int = Field(default=1_073_741_824)
    monitor_admin_change_retention_days: int = Field(default=180)
    monitor_analytics_start_at: str = Field(default="")
    monitor_timezone: str = Field(default="Asia/Tokyo")
    monitor_log_level: str = Field(default="INFO")

    @property
    def admin_allowlist(self) -> List[str]:
        out: List[str] = []
        for raw in str(self.monitor_admin_allowlist or "").split(","):
            value = raw.strip().lower()
            if value:
                out.append(value)
        return sorted(set(out))

    @property
    def cors_allowed_origins(self) -> List[str]:
        out: List[str] = []
        for raw in str(self.monitor_cors_allowed_origins or "").split(","):
            value = raw.strip()
            if value:
                out.append(value)
        return sorted(set(out))

    @property
    def news_usage_configuration_status(self) -> str:
        """Additive usage state; incomplete values never break app startup."""

        service = str(self.monitor_news_usage_source_service or "").strip()
        start = str(self.monitor_news_usage_start_at or "").strip()
        if not service and not start:
            return "disabled"
        if not service or not start:
            return "invalid"
        if re.fullmatch(r"[a-z][a-z0-9-]{0,62}", service) is None:
            return "invalid"
        try:
            parsed_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return "invalid"
        if parsed_start.tzinfo is None:
            return "invalid"
        return "enabled"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
