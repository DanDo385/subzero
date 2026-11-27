"""Read the two owner-configured guidance files without a general file API."""

from __future__ import annotations

import asyncio
import json
import re
import stat
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx

from subzero.config import Settings
from subzero.models import AgentContext

Document = Literal["agents", "machines"]
_PATHS: dict[Document, str] = {"agents": "subzero/AGENTS.md", "machines": "cli/MACHINES.md"}
_MAX_BYTES = 32_768
_FETCH_SECONDS = 2.0


def _read_local(configured_root: Path, relative: str) -> bytes | None:
    root = configured_root.expanduser().resolve()
    path = (root / relative).resolve(strict=True)
    if path != root / relative or not stat.S_ISREG(path.stat().st_mode):
        return None
    with path.open("rb") as file:
        return file.read(_MAX_BYTES + 1)


async def _read_github(settings: Settings, relative: str) -> bytes | None:
    token = settings.dotfiles_github_token.get_secret_value()
    if not settings.dotfiles_github_repo or not token:
        return None
    api = f"https://api.github.com/repos/{settings.dotfiles_github_repo}"
    directory, name = relative.rsplit("/", 1)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Subzero"}
    async with asyncio.timeout(_FETCH_SECONDS), httpx.AsyncClient(headers=headers) as client:
        # The direct Contents file endpoint follows in-repo symlinks. Select
        # a regular file from the directory, then read its immutable blob.
        entries = await _github_bytes(
            client,
            f"{api}/contents/{directory}",
            accept="application/vnd.github+json",
            params={"ref": settings.dotfiles_github_ref},
        )
        listing = json.loads(entries) if entries else None
        if not isinstance(listing, list):
            return None
        for entry in listing:
            if (
                isinstance(entry, dict)
                and entry.get("name") == name
                and entry.get("type") == "file"
            ):
                sha = entry.get("sha", "")
                if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha):
                    return await _github_bytes(
                        client, f"{api}/git/blobs/{sha}", accept="application/vnd.github.raw+json"
                    )
    return None


async def _github_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    accept: str,
    params: dict[str, str] | None = None,
) -> bytes | None:
    data = bytearray()
    async with client.stream("GET", url, params=params, headers={"Accept": accept}) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > _MAX_BYTES:
                return None
            data.extend(chunk)
    return bytes(data)


async def read_agent_context(settings: Settings, document: Document) -> AgentContext | None:
    relative = _PATHS[document]
    source: Literal["local", "github"] = "local" if settings.dotfiles_root else "github"
    try:
        if settings.dotfiles_root:
            data = await asyncio.to_thread(_read_local, settings.dotfiles_root, relative)
        else:
            data = await _read_github(settings, relative)
        if not data or len(data) > _MAX_BYTES:
            return None
        content = data.decode("utf-8").strip()
    except (OSError, RuntimeError, ValueError, httpx.HTTPError):
        return None
    if not content:
        return None
    return AgentContext(
        path=relative, source=source, sha256=sha256(data).hexdigest(), content=content
    )
