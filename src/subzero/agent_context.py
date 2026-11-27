"""Read the owner's chat context files from dotfiles without a general file API.

Chat sessions (web and mobile apps) have no disk, so their standing context
comes from here. Five documents are known by name; nothing else can be read.
Three of them (the context header, SOUL and the chat AGENTS) are the standing
context every ``context_bootstrap`` starts with; memory and machines open on
demand through ``context_read_guidance``.

The source is the owner's dotfiles: a local checkout when the server runs on a
machine that has one (``SUBZERO_DOTFILES_ROOT`` names a folder that exists
here), otherwise a published Git ref read through the GitHub API (the
serverless deployment). So one settings file can carry both: the Mac finds its
checkout, and Vercel, where that folder does not exist, reads GitHub. GitHub reads are
cached for ``SUBZERO_DOTFILES_CACHE_SECONDS`` so a bootstrap does not pay six
API round trips; local reads always see the file as it is on disk.
"""

from __future__ import annotations

import asyncio
import json
import re
import stat
import time
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx

from subzero.config import Settings
from subzero.models import AgentContext

Document = Literal["context", "soul", "agents", "memory", "machines"]
_PATHS: dict[Document, str] = {
    "context": "subzero/CONTEXT.md",
    "soul": "subzero/SOUL.md",
    "agents": "subzero/AGENTS.md",
    "memory": "subzero/MEMORY.md",
    "machines": "MACHINES.md",
}
#: Sent at the top of every bootstrap, in this order.
STANDING: tuple[Document, ...] = ("context", "soul", "agents")
#: Ceiling on the standing context, across all three documents. It is sent on
#: top of the retrieval budget, so it stays small on purpose (about 2k tokens).
STANDING_MAX_CHARS = 8000
_MAX_BYTES = 32_768
_FETCH_SECONDS = 2.0

_cache: dict[tuple[str, str, str], tuple[float, AgentContext | None]] = {}


def _local_root(settings: Settings) -> Path | None:
    """The configured checkout, when it exists on this machine."""
    root = settings.dotfiles_root
    if root is None:
        return None
    path = root.expanduser()
    return path if path.is_dir() else None


def configured(settings: Settings) -> bool:
    return bool(
        settings.dotfiles_root
        or (settings.dotfiles_github_repo and settings.dotfiles_github_token.get_secret_value())
    )


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
    directory, _, name = relative.rpartition("/")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Subzero"}
    async with asyncio.timeout(_FETCH_SECONDS), httpx.AsyncClient(headers=headers) as client:
        # The direct Contents file endpoint follows in-repo symlinks. Select
        # a regular file from the directory, then read its immutable blob.
        entries = await _github_bytes(
            client,
            f"{api}/contents/{directory}" if directory else f"{api}/contents",
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


async def _read(settings: Settings, document: Document) -> AgentContext | None:
    relative = _PATHS[document]
    local = _local_root(settings)
    source: Literal["local", "github"] = "local" if local else "github"
    try:
        if local:
            data = await asyncio.to_thread(_read_local, local, relative)
        else:
            data = await _read_github(settings, relative)
        if not data or len(data) > _MAX_BYTES:
            return None
        content = data.decode("utf-8").strip()
    except (OSError, RuntimeError, ValueError, httpx.HTTPError):
        return None
    if not content:
        return None
    digest = sha256(data).hexdigest()
    return AgentContext(
        document=document,
        path=relative,
        source=source,
        sha256=digest,
        content_id=digest[:12],
        content=content,
    )


async def read_agent_context(settings: Settings, document: Document) -> AgentContext | None:
    """One document, or None when it is missing, unreadable or not configured."""
    if _local_root(settings) or settings.dotfiles_cache_seconds <= 0:
        return await _read(settings, document)
    key = (settings.dotfiles_github_repo, settings.dotfiles_github_ref, document)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1].model_copy() if hit[1] else None
    found = await _read(settings, document)
    _cache[key] = (now + settings.dotfiles_cache_seconds, found)
    return found.model_copy() if found else None


async def read_standing(settings: Settings) -> list[AgentContext]:
    """The standing context, in order, capped at STANDING_MAX_CHARS in total.

    Missing documents are skipped; a document that does not fit is cut at a
    line boundary and marked, and the ones after it are left out.
    """
    if not configured(settings):
        return []
    found = await asyncio.gather(*(read_agent_context(settings, d) for d in STANDING))
    kept: list[AgentContext] = []
    room = STANDING_MAX_CHARS
    for doc in found:
        if doc is None:
            continue
        if room <= 200:
            break
        if len(doc.content) > room:
            cut = doc.content[:room].rsplit("\n", 1)[0]
            doc.content = f"{cut}\n[... cut to fit; read the rest with context_read_guidance]"
            doc.truncated = True
        room -= len(doc.content)
        kept.append(doc)
    return kept
