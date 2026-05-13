from __future__ import annotations

import subprocess
from typing import Any

from google.auth.credentials import Credentials
from google.oauth2.credentials import Credentials as OAuthCredentials


def get_gcloud_cli_credentials_if_enabled(settings: Any) -> Credentials | None:
    if not bool(getattr(settings, "monitor_use_gcloud_cli_auth", False)):
        return None
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"],
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=15,
    ).strip()
    if not token:
        return None
    # Local development fallback only. Long-running services should use ADC/service account.
    return OAuthCredentials(token=token)
