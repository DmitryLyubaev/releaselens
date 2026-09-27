"""Shared fixtures for the infra test harness."""

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root, resolved from this file's location."""
    return Path(__file__).resolve().parents[2]
