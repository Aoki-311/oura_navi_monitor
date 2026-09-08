#!/usr/bin/env python3
"""Metadata-only validation for the approved per-command Google credential."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path


_CREDENTIAL_ENVIRONMENT_VARIABLES = (
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def approved_credential_path(explicit_path: str | Path) -> Path:
    configured = str(explicit_path or "").strip()
    if not configured:
        raise ValueError("--credential-file is required")
    path = Path(configured)
    if not path.is_absolute() or Path(os.path.normpath(configured)) != path:
        raise ValueError("approved credential must be one normalized absolute path")
    try:
        locator_metadata = path.lstat()
        # An explicitly selected, user-owned link is a locator for the same
        # credential. Validate its target without changing the selected path
        # or reading/copying any credential contents.
        metadata = path.stat()
    except OSError as exc:
        raise ValueError("approved credential metadata is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("approved credential must resolve to a regular file")
    if locator_metadata.st_uid != os.getuid() or metadata.st_uid != os.getuid():
        raise ValueError("approved credential must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("approved credential mode must be exactly 0600")
    for variable in _CREDENTIAL_ENVIRONMENT_VARIABLES:
        ambient = str(os.environ.get(variable) or "").strip()
        if ambient and ambient != str(path):
            raise ValueError(f"{variable} does not match --credential-file")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--print-path", action="store_true")
    args = parser.parse_args()
    try:
        path = approved_credential_path(args.credential_file)
    except ValueError as exc:
        raise SystemExit("credential_preflight_invalid: " + str(exc)) from exc
    if args.print_path:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
