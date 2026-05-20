"""Configuration and local filesystem paths for SwissDataMCP."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CKAN_BASE_URL = "https://ckan.opendata.swiss/api/3/action"
DEFAULT_USER_AGENT = "swissdatamcp/0.1 (+https://opendata.swiss)"


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the local MCP server."""

    home: Path
    cache_dir: Path
    downloads_dir: Path
    outputs_dir: Path
    reports_dir: Path
    sessions_dir: Path
    database_path: Path
    ckan_base_url: str = DEFAULT_CKAN_BASE_URL
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout_seconds: float = 30.0
    max_download_mb: int = 75


def load_settings() -> Settings:
    """Load settings from environment variables with local-first defaults."""

    home = Path(os.getenv("SWISSDATAMCP_HOME", Path.cwd() / ".swissdatamcp")).resolve()
    cache_dir = Path(os.getenv("SWISSDATAMCP_CACHE_DIR", home / "cache")).resolve()
    downloads_dir = Path(os.getenv("SWISSDATAMCP_DOWNLOADS_DIR", home / "downloads")).resolve()
    outputs_dir = Path(os.getenv("SWISSDATAMCP_OUTPUTS_DIR", home / "outputs")).resolve()
    reports_dir = Path(os.getenv("SWISSDATAMCP_REPORTS_DIR", home / "reports")).resolve()
    sessions_dir = Path(os.getenv("SWISSDATAMCP_SESSIONS_DIR", home / "sessions")).resolve()
    database_path = Path(os.getenv("SWISSDATAMCP_DB", home / "swissdatamcp.duckdb")).resolve()

    settings = Settings(
        home=home,
        cache_dir=cache_dir,
        downloads_dir=downloads_dir,
        outputs_dir=outputs_dir,
        reports_dir=reports_dir,
        sessions_dir=sessions_dir,
        database_path=database_path,
        ckan_base_url=os.getenv("SWISSDATAMCP_CKAN_BASE_URL", DEFAULT_CKAN_BASE_URL).rstrip("/"),
        user_agent=os.getenv("SWISSDATAMCP_USER_AGENT", DEFAULT_USER_AGENT),
        request_timeout_seconds=float(os.getenv("SWISSDATAMCP_TIMEOUT_SECONDS", "30")),
        max_download_mb=int(os.getenv("SWISSDATAMCP_MAX_DOWNLOAD_MB", "75")),
    )
    ensure_directories(settings)
    return settings


def ensure_directories(settings: Settings) -> None:
    """Create local cache/output directories."""

    for directory in (
        settings.home,
        settings.cache_dir,
        settings.downloads_dir,
        settings.outputs_dir,
        settings.reports_dir,
        settings.sessions_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
