"""Outbound HTTP client for the worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from subzero.obsidian.sync.config import WorkerSettings


@dataclass(frozen=True)
class Lease:
    """One answer to "is there anything to write?".

    ``poll_in_seconds`` is how long the server asks the worker to wait before
    asking again, because only the server knows whether work is still arriving.
    It is ``None`` against a server too old to send one, and the worker then
    falls back to its own configured interval.
    """

    jobs: list[dict[str, Any]]
    poll_in_seconds: float | None = None


class SyncClient:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_url.rstrip("/"),
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"x-worker-key": settings.api_key.get_secret_value()},
        )

    async def lease(self) -> Lease:
        response = await self._client.post(
            "/worker/jobs/lease",
            json={"worker_id": self._settings.worker_id, "limit": self._settings.batch_size},
        )
        response.raise_for_status()
        body = response.json()
        hint = body.get("poll_in_seconds")
        return Lease(
            jobs=body.get("jobs", []),
            poll_in_seconds=float(hint) if hint is not None else None,
        )

    async def ack(
        self,
        job_id: str,
        *,
        status: str = "done",
        sha256: str | None = None,
        error: str | None = None,
    ) -> None:
        response = await self._client.post(
            "/worker/jobs/ack",
            json={
                "worker_id": self._settings.worker_id,
                "job_id": job_id,
                "status": status,
                "sha256": sha256,
                "error": error,
            },
        )
        response.raise_for_status()

    async def push_edits(self, edits: list[dict[str, Any]]) -> dict[str, Any]:
        response = await self._client.post(
            "/worker/edits",
            json={"worker_id": self._settings.worker_id, "edits": edits},
        )
        response.raise_for_status()
        return response.json()

    async def download(self, url: str) -> bytes:
        # Signed URLs point at object storage, not at our API, so this uses a
        # bare client with no worker credential attached.
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def aclose(self) -> None:
        await self._client.aclose()
