"""End-to-end journeys: real HTTP/MCP against the loopback app."""

import pytest

from support.db import TEST_DB_ENV, test_dsn


@pytest.fixture(autouse=True)
def require_database() -> None:
    if test_dsn() is None:
        pytest.skip(f"set {TEST_DB_ENV} to a throwaway database to run these tests")
