"""Device pairing for HTTP ingest and agent tests."""

from __future__ import annotations

from typing import Any

import httpx


async def insert_pairing_code(db: Any, *, code: str, device_name: str = "test-browser") -> str:
    await db.execute(
        "INSERT INTO pairing_codes (code, device_name) VALUES ($1, $2)",
        code,
        device_name,
    )
    return code


async def pair_device(
    client: httpx.AsyncClient,
    db: Any,
    *,
    code: str = "E2EPAIR42",
    device_name: str = "test-browser",
) -> str:
    await insert_pairing_code(db, code=code, device_name=device_name)
    response = await client.post("/api/v1/auth/pair-device", json={"pairing_code": code})
    assert response.status_code == 200, response.text
    return response.json()["device_token"]