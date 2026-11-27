"""asyncpg connection pool with the small amount of glue we actually need.

Deliberately thin: no ORM, no query builder. Every statement in this codebase
is hand-written SQL in the service module that owns the table, which keeps the
security-relevant queries readable.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import asyncpg

from subzero.config import Settings
from subzero.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class RequestStats:
    """Statements run and time spent in Postgres for one request.

    Every statement a request runs costs a network round trip against a
    remote database (Supabase is ~200 ms away from a home machine), so this
    is the number Phase 1 optimises. Filled by the instrumented connections
    below whenever a request set one; background loops leave it unset and
    pay nothing for the lookup.
    """

    statements: int = 0
    db_ms: float = 0.0


_stats_var: ContextVar[RequestStats | None] = ContextVar("subzero_db_stats", default=None)

#: Every statement any connection ran since import, for tests. Request
#: attribution uses the contextvar above (a live server handles each request
#: in its own task tree, which a test cannot reach across a socket); the
#: eval harness snapshots this around each serial tool call instead.
_total_statements: int = 0


def total_statements() -> int:
    """Process-wide statement count. Eval use only."""
    return _total_statements


@contextmanager
def request_stats() -> Iterator[RequestStats]:
    """Collect per-request database stats until the block exits.

    The request log middleware owns this: one accumulator per HTTP request,
    read after the response completes. Task-local, so concurrent requests
    never share a count.
    """
    stats = RequestStats()
    token: Token[RequestStats | None] = _stats_var.set(stats)
    try:
        yield stats
    finally:
        _stats_var.reset(token)


class _InstrumentedConnection:
    """Counts and times statements into the request's RequestStats, if set.

    Thin delegation: anything that is not fetch/fetchrow/fetchval/execute/
    executemany passes straight through to the real connection, so
    transaction blocks and type codecs behave exactly as before.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def _timed(self, method: str, *args: Any, **kwargs: Any) -> Any:
        global _total_statements
        stats = _stats_var.get()
        # Synchronous increments only: no await between load and store, so
        # no task can interleave. The eval harness snapshots the global
        # around each serial call; requests use their own accumulator.
        start = time.perf_counter() if stats is not None else 0.0
        try:
            return await getattr(self._conn, method)(*args, **kwargs)
        finally:
            _total_statements += 1
            if stats is not None:
                stats.statements += 1
                stats.db_ms += (time.perf_counter() - start) * 1000

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        return await self._timed("fetch", *args, **kwargs)

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        return await self._timed("fetchrow", *args, **kwargs)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return await self._timed("fetchval", *args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return await self._timed("execute", *args, **kwargs)

    async def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return await self._timed("executemany", *args, **kwargs)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Make JSONB round-trip as dict/list, and register pgvector text I/O."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    # pgvector's wire format is not known to asyncpg. Its text representation
    # ("[1,2,3]") is accepted on input and easy to parse on output, which is
    # plenty for our volumes and avoids a binary-codec dependency.
    #
    # This must tolerate the type being absent: on a brand-new database the
    # pool is opened by `subzero migrate`, before migration 0001 has
    # created the extension. asyncpg signals that as a bare ValueError from
    # type introspection, not only as UndefinedObjectError.
    try:
        await conn.set_type_codec(
            "vector",
            encoder=_encode_vector,
            decoder=_decode_vector,
            format="text",
        )
    except (asyncpg.exceptions.UndefinedObjectError, ValueError):
        logger.info(
            "pgvector type not registered on this connection; "
            "run migrations, then reconnect to enable vector search"
        )


def vector_literal(value: Sequence[float] | None) -> str | None:
    """pgvector text form. Send it as ``$n::text::vector`` to work whether or
    not this connection has the vector codec registered."""
    return _encode_vector(value)


def _encode_vector(value: Sequence[float] | None) -> str | None:
    if value is None:
        return None
    return "[" + ",".join(f"{float(v):.7g}" for v in value) + "]"


def _decode_vector(value: str | None) -> list[float] | None:
    if value is None:
        return None
    return [float(part) for part in value.strip("[]").split(",") if part]


class Database:
    """Owns the pool. One instance per process.

    Two phases: constructing it is free, :meth:`open` connects. That split lets
    the app wire every service to this object while it is still being built, and
    leaves the connecting to the server's lifespan. :meth:`connect` does both
    for scripts and the CLI.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None

    @classmethod
    async def connect(cls, settings: Settings) -> Database:
        db = cls(settings)
        await db.open()
        return db

    @property
    def is_open(self) -> bool:
        return self._pool is not None

    async def open(self) -> None:
        if self._pool is not None:
            return
        settings = self._settings
        self._pool = await asyncpg.create_pool(
            dsn=settings.database_url.get_secret_value(),
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            init=_init_connection,
            command_timeout=settings.db_statement_timeout_ms / 1000,
            # Supabase's pooler does not support prepared-statement caching.
            statement_cache_size=0,
        )
        logger.info("database pool ready")

    async def close(self) -> None:
        if self._pool is not None:
            pool, self._pool = self._pool, None
            await pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database is not open; call open() first.")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """A pooled connection. Statements run through it are counted into
        the request's RequestStats when one is set (see request_stats)."""
        async with self.pool.acquire() as conn:
            yield _InstrumentedConnection(conn)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """A connection inside a transaction.

        Checkpoints use this so a half-applied checkpoint never persists.
        """
        async with self.pool.acquire() as conn, conn.transaction():
            yield _InstrumentedConnection(conn)

    # ---------------------------------------------------------------- reads
    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        async with self.acquire() as conn:
            return await conn.execute(query, *args)
