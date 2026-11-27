"""Test fixtures.

Two tiers:

* **Unit** tests exercise pure logic (path validation, key generation, archive
  guards, confidence policy, dedup hashing). They need nothing external.
* **Integration** tests exercise real SQL against a real Postgres with
  pgvector. They are skipped, with a clear reason, unless SUBZERO_TEST_DATABASE_URL
  points at a throwaway database.

Nothing here ever touches a production database: the integration fixture
refuses a URL that does not look like a test database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from subzero.config import Settings
from support.db import TEST_DB_ENV, requires_db, test_dsn

__all__ = ["TEST_DB_ENV", "requires_db", "test_dsn"]


@pytest.fixture(autouse=True)
def _isolated_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the vault helper's state file out of the real home directory."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="dev",
        database_url=test_dsn() or "postgres://unused",
        # Every test drops and rebuilds the whole schema, and the production
        # 15 s statement timeout is not what a test measures: under load the
        # rebuild alone can exceed it, which surfaces as a random TimeoutError
        # in whichever test happens to be running.
        db_statement_timeout_ms=120_000,
        auth_mode="static",
        static_tokens={"test-token": "tester@example.com"},
        allowed_subjects=["tester@example.com"],
        classifier="rules",
        storage_backend="local",
        local_storage_root=str(tmp_path / "storage"),
        obsidian_enabled=True,
        typesafe_api_key="unused",
        oauth_signing_key="test-signing-key",
        worker_api_key="test-worker-key",
    )


@pytest.fixture
async def db(settings: Settings):
    """A migrated database, wrapped so each test starts from a clean schema."""
    from subzero.db.migrate import apply_migrations
    from subzero.db.pool import Database

    dsn = test_dsn()
    if dsn is None:
        pytest.skip(f"set {TEST_DB_ENV} to run this test")

    database = await Database.connect(settings)
    async with database.acquire() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        await apply_migrations(conn)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def services(settings: Settings, db):
    from subzero.services import build_services

    built = build_services(settings, db)
    yield built


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for name in ("00-Inbox", "20-Projects", "30-Research", "60-Resources"):
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
async def live_server(settings):
    """Run the real app on a loopback port.

    FastMCP's client speaks httpx2 and does not honour an injected ASGI
    transport, so an in-process shim does not work. A real socket also tests
    the streaming transport rather than a stand-in for it.
    """
    import asyncio
    import socket

    import uvicorn

    from subzero.server import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    settings.base_url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(
        create_app(settings),
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("uvicorn did not start")

    try:
        yield settings
    finally:
        server.should_exit = True
        await task


# Shared HTTP/MCP clients for e2e journeys (see tests/e2e/).
pytest_plugins = ["support.app", "support.rate_limit", "support.worker", "support.eval"]
