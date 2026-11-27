"""High rate limits for integration tests that hammer MCP tools."""

from __future__ import annotations

import pytest

from subzero.config import Settings


@pytest.fixture
def unthrottled(settings: Settings) -> Settings:
    settings.rate_limit_per_minute = 1_000_000
    settings.rate_limit_burst = 1_000_000
    return settings
