"""The sync worker loop.

Poll, lease, write, acknowledge. Nothing in this process listens on a socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from typing import Any

import httpx

from obsidian_sync.client import Lease, SyncClient
from obsidian_sync.config import WorkerSettings
from obsidian_sync.vault import Vault
from subzero.logging_setup import configure_logging, get_logger
from subzero.util import sha256_bytes

logger = get_logger(__name__)

#: Never ask the server faster than this, whatever it suggests.
FAST_DELAY_SECONDS = 0.5


class ObsidianSyncWorker:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._client = SyncClient(settings)
        self._vault = Vault(
            settings.resolved_vault,
            settings.vault_roots,
            max_bytes=settings.max_file_bytes,
            dry_run=settings.dry_run,
        )
        self._stopping = asyncio.Event()
        #: vault path -> sha we last wrote, used to spot human edits. Loaded
        #: from disk, because a fresh process with an empty map sees no edit.
        self._written: dict[str, str] = self._load_written()
        self._dirty = False

    def _load_written(self) -> dict[str, str]:
        path = self._settings.state_file
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("ignoring unreadable sync state", extra={"path": str(path)})
            return {}
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}

    def _save_written(self) -> None:
        """Write the record atomically: a crash leaves the old file or the new."""
        if not self._dirty or self._settings.dry_run:
            return
        path = self._settings.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self._written, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
            self._dirty = False
        except OSError:
            logger.warning("could not save sync state", extra={"path": str(path)})

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info(
            "obsidian-sync starting",
            extra={
                "vault": str(self._settings.resolved_vault),
                "api": self._settings.api_url,
                "dry_run": self._settings.dry_run,
            },
        )
        try:
            while not self._stopping.is_set():
                # A tick that reaches the server replaces this with what the
                # server asked for; a tick that failed keeps the local rhythm.
                delay = self._settings.poll_interval_seconds
                try:
                    lease = await self._tick()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 401:
                        logger.error(
                            "worker rejected: check OBSIDIAN_SYNC_API_KEY matches "
                            "SUBZERO_WORKER_API_KEY on the server"
                        )
                    else:
                        logger.warning(
                            "server error while polling",
                            extra={"status": exc.response.status_code},
                        )
                except httpx.HTTPError:
                    # Offline is the normal state for a laptop. Keep quiet and
                    # retry; nothing is lost because the queue is server-side.
                    logger.debug("could not reach the server; will retry")
                except Exception:
                    logger.exception("unexpected worker error")
                else:
                    delay = self._next_delay(lease)

                with_timeout = asyncio.wait_for(self._stopping.wait(), timeout=delay)
                try:
                    await with_timeout
                except TimeoutError:
                    continue
        finally:
            await self._client.aclose()
            logger.info("obsidian-sync stopped")

    def _next_delay(self, lease: Lease) -> float:
        """How long to wait before the next poll.

        Draining a backlog comes straight back. Otherwise the server decides:
        quick while work is still arriving, quiet when the vault has been idle,
        because nothing can push a job to this laptop and every poll is a
        request it has to answer. The ceiling keeps a nonsense answer from
        stalling the worker, and the floor keeps it from hammering.
        """
        if lease.jobs:
            return FAST_DELAY_SECONDS
        asked = lease.poll_in_seconds or self._settings.poll_interval_seconds
        return min(max(asked, FAST_DELAY_SECONDS), self._settings.max_poll_interval_seconds)

    async def _tick(self) -> Lease:
        if self._settings.watch_for_edits and self._written:
            edits = self._vault.scan_for_edits(self._written)
            if edits:
                result = await self._client.push_edits(edits)
                for edit in edits:
                    # Accept the new on-disk hash so the edit is pushed once.
                    self._written[str(edit["vault_path"])] = str(edit["sha256"])
                self._dirty = True
                self._save_written()
                logger.info(
                    "pushed vault edits",
                    extra={"count": len(edits), "applied": len(result.get("applied", []))},
                )

        lease = await self._client.lease()
        try:
            for job in lease.jobs:
                await self._handle(job)
        finally:
            self._save_written()
        return lease

    async def _handle(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        vault_path = str(job["vault_path"])

        try:
            if "download_url" in job:
                data = await self._client.download(job["download_url"])
                expected = job.get("expected_content_sha256")
                if expected and sha256_bytes(data) != expected:
                    await self._client.ack(
                        job_id,
                        status="failed",
                        error="downloaded bytes did not match the expected hash",
                    )
                    return
            else:
                content = job.get("content")
                if content is None:
                    await self._client.ack(job_id, status="failed", error="job carried no content")
                    return
                data = content.encode("utf-8")

            outcome = self._vault.write(
                vault_path, data, expected_sha256=job.get("expected_sha256")
            )
        except Exception as exc:
            logger.exception("failed to write vault file", extra={"vault_path": vault_path})
            await self._client.ack(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            return

        if outcome.conflict:
            logger.warning("vault write conflict", extra={"vault_path": vault_path})
            await self._client.ack(job_id, status="conflict", error=outcome.reason)
            return

        if not outcome.written and outcome.reason != "already up to date":
            await self._client.ack(job_id, status="failed", error=outcome.reason)
            return

        if outcome.sha256:
            self._written[vault_path] = outcome.sha256
            self._dirty = True
        await self._client.ack(job_id, status="done", sha256=outcome.sha256)
        logger.info("wrote vault file", extra={"vault_path": vault_path})


async def run_worker(settings: WorkerSettings) -> None:
    configure_logging("INFO")
    worker = ObsidianSyncWorker(settings)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with __import__("contextlib").suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)

    await worker.run()
