from __future__ import annotations

from hashlib import sha1
from pathlib import Path

import httpx
import pytest

from subzero.agent_context import read_agent_context
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
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer test-token"
        sha = sha1(revision, usedforsecurity=False).hexdigest()
        if request.url.path == "/repos/owner/dotfiles/contents/cli":
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
