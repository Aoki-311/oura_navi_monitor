from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    monitor_project_id: str = Field(default="lcs-developer-483404")
    monitor_region: str = Field(default="us-central1")
    monitor_source_service: str = Field(default="lcs-rag-app")
    monitor_runtime_service_account: str = Field(default="")

    monitor_bq_dataset: str = Field(default="oura_navi_monitor")
    monitor_bq_location: str = Field(default="US")

    monitor_firestore_database: str = Field(default="lcs-user-data")
    monitor_firestore_chat_collection: str = Field(default="chat_users")

    monitor_admin_allowlist: str = Field(default="")
    monitor_iap_strict: bool = Field(default=True)
    monitor_allow_unverified_local: bool = Field(default=False)
    monitor_cors_allowed_origins: str = Field(default="")

    monitor_default_days: int = Field(default=7)
    monitor_retention_days: int = Field(default=180)
    monitor_max_users_scan: int = Field(default=800)
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
