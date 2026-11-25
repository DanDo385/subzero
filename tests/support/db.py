"""Database test gate shared by integration and e2e suites."""

from __future__ import annotations

import os

import pytest

TEST_DB_ENV = "SUBZERO_TEST_DATABASE_URL"


def test_dsn() -> str | None:
    dsn = os.environ.get(TEST_DB_ENV)
    if not dsn:
        return None
    tail = dsn.split("?", 1)[0].rsplit("/", 1)[-1].lower()
    if "test" not in tail:
        pytest.exit(
            f"{TEST_DB_ENV} must point at a database whose name contains 'test'. "
            f"Refusing to run migrations against {tail!r}.",
            returncode=2,
        )
    return dsn


requires_db = pytest.mark.skipif(
    test_dsn() is None,
    reason=f"set {TEST_DB_ENV} to a throwaway database to run integration tests",
)