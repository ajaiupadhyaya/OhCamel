"""Where the job system keeps its files: under Settings.data_dir (/data in compose)."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings, get_settings


def jobs_db_path(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).data_dir) / "jobs.sqlite"


def artifacts_root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).data_dir) / "artifacts"
