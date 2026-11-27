from __future__ import annotations

from hashlib import sha1
from pathlib import Path

import httpx
import pytest

from subzero import agent_context
from subzero.agent_context import STANDING_MAX_CHARS, read_agent_context, read_standing
from subzero.config import Settings


async def test_local_rejects_links_and_oversize(tmp_path: Path) -> None:
    root = tmp_path / "dotfiles"
    (root / "subzero").mkdir(parents=True)
    reference = root / "subzero" / "PRE-PROMPT.md"
    reference.write_text("reference-only")
    agents = root / "subzero" / "AGENTS.md"
    agents.symlink_to(reference)
    settings = Settings(dotfiles_root=root)
    assert await read_agent_context(settings, "agents") is None
    agents.unlink()
    outside = tmp_path / "somewhere-else"
    outside.write_text("out of scope")
    agents.symlink_to(outside)
    assert await read_agent_context(settings, "agents") is None
    agents.unlink()
    agents.write_bytes(b"x" * 32_769)
    assert await read_agent_context(settings, "agents") is None


async def test_github_reads_only_requested_document_and_picks_up_new_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = b"# First revision"
    entry_type = "file"
    settings = Settings(
        dotfiles_github_repo="owner/dotfiles",
        dotfiles_github_ref="docs/context",
        dotfiles_github_token="test-token",
        dotfiles_cache_seconds=0,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer test-token"
        sha = sha1(revision, usedforsecurity=False).hexdigest()
        # MACHINES.md sits at the repository root: the listing is the root's.
        if request.url.path == "/repos/owner/dotfiles/contents":
            assert request.url.params["ref"] == "docs/context"
            return httpx.Response(
                200, json=[{"name": "MACHINES.md", "type": entry_type, "sha": sha}]
            )
        assert entry_type == "file"
        assert request.url.path == f"/repos/owner/dotfiles/git/blobs/{sha}"
        return httpx.Response(200, content=revision)

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "subzero.agent_context.httpx.AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    first = await read_agent_context(settings, "machines")
    assert first is not None and first.source == "github" and first.content == "# First revision"

    revision = b"# Second revision"
    second = await read_agent_context(settings, "machines")
    assert second is not None and second.content == "# Second revision"
    assert second.sha256 != first.sha256

    entry_type = "symlink"
    assert await read_agent_context(settings, "machines") is None


async def test_github_reads_are_cached_for_the_configured_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    body = b"# Soul"
    sha = sha1(body, usedforsecurity=False).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/contents/subzero"):
            return httpx.Response(200, json=[{"name": "SOUL.md", "type": "file", "sha": sha}])
        return httpx.Response(200, content=body)

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "subzero.agent_context.httpx.AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(agent_context, "_cache", {})
    settings = Settings(
        dotfiles_github_repo="owner/dotfiles",
        dotfiles_github_token="test-token",
        dotfiles_cache_seconds=60,
    )
    first = await read_agent_context(settings, "soul")
    second = await read_agent_context(settings, "soul")
    assert first is not None and second is not None and first.content == second.content == "# Soul"
    assert len(calls) == 2  # one listing and one blob, for both reads
    assert first.content_id == first.sha256[:12]


async def test_standing_context_is_ordered_skips_gaps_and_is_capped(tmp_path: Path) -> None:
    root = tmp_path / "dotfiles"
    (root / "subzero").mkdir(parents=True)
    (root / "subzero" / "CONTEXT.md").write_text("# Context header")
    # No SOUL.md: a missing document is skipped, the others still arrive.
    agents = "# Chat rules\n" + ("a line of rules\n" * 1000)
    (root / "subzero" / "AGENTS.md").write_text(agents)
    (root / "MACHINES.md").write_text("# Machines")
    settings = Settings(dotfiles_root=root)

    standing = await read_standing(settings)

    assert [d.document for d in standing] == ["context", "agents"]
    assert standing[1].truncated and "cut to fit" in standing[1].content
    assert sum(len(d.content) for d in standing) <= STANDING_MAX_CHARS + 80
    assert all("# Machines" not in d.content for d in standing)
    assert await read_standing(Settings()) == []


async def test_standing_includes_memory_and_cuts_machines_last(tmp_path: Path) -> None:
    root = tmp_path / "dotfiles"
    (root / "subzero").mkdir(parents=True)
    (root / "subzero" / "CONTEXT.md").write_text("# Context header")
    (root / "subzero" / "SOUL.md").write_text("# Soul")
    (root / "subzero" / "AGENTS.md").write_text("# Chat rules")
    (root / "subzero" / "MEMORY.md").write_text("# Memory (pointer)\n\nFacts live in the database.")
    (root / "MACHINES.md").write_text("# Machines\n" + ("machine card\n" * 2000))
    settings = Settings(dotfiles_root=root)

    standing = await read_standing(settings)

    assert [d.document for d in standing] == ["context", "soul", "agents", "memory", "machines"]
    assert standing[3].truncated is False
    assert "Facts live in the database." in standing[3].content
    assert standing[4].truncated and "cut to fit" in standing[4].content
    assert standing[4].content_id == standing[4].sha256[:12]
    assert sum(len(d.content) for d in standing) <= STANDING_MAX_CHARS + 80


async def test_missing_local_checkout_falls_back_to_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One settings file serves the Mac and Vercel: where the configured
    checkout does not exist, the published ref is read instead."""
    body = b"# From GitHub"
    sha = sha1(body, usedforsecurity=False).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/subzero"):
            return httpx.Response(200, json=[{"name": "SOUL.md", "type": "file", "sha": sha}])
        return httpx.Response(200, content=body)

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "subzero.agent_context.httpx.AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings = Settings(
        dotfiles_root=tmp_path / "not-on-this-machine",
        dotfiles_github_repo="owner/dotfiles",
        dotfiles_github_token="test-token",
        dotfiles_cache_seconds=0,
    )
    soul = await read_agent_context(settings, "soul")
    assert soul is not None and soul.source == "github" and soul.content == "# From GitHub"

    (tmp_path / "not-on-this-machine" / "subzero").mkdir(parents=True)
    (tmp_path / "not-on-this-machine" / "subzero" / "SOUL.md").write_text("# From disk")
    local = await read_agent_context(settings, "soul")
    assert local is not None and local.source == "local" and local.content == "# From disk"
