"""Isolate tests from the user's local data workspace."""

import atexit
import os
from tempfile import TemporaryDirectory

import pytest

from swissdatamcp.config import Settings, ensure_directories
from swissdatamcp.store import DataStore

_workspace = TemporaryDirectory(prefix="swissdatamcp-tests-")
os.environ["SWISSDATAMCP_HOME"] = _workspace.name
atexit.register(_workspace.cleanup)


@pytest.fixture
def settings(tmp_path):
    config = Settings(
        home=tmp_path,
        cache_dir=tmp_path / "cache",
        downloads_dir=tmp_path / "downloads",
        outputs_dir=tmp_path / "outputs",
        reports_dir=tmp_path / "reports",
        sessions_dir=tmp_path / "sessions",
        database_path=tmp_path / "test.duckdb",
    )
    ensure_directories(config)
    return config


@pytest.fixture
def data_store(settings):
    return DataStore(settings)
