"""Import path: classify cache and idempotent zip import."""

from __future__ import annotations

from pathlib import Path

from subzero.importers.runner import ImportRunner
from support.fakes import CountingJev, claude_zip, conversation, swap_classifier


async def test_identical_classify_uses_cache(services) -> None:
    fake = CountingJev()
    service = swap_classifier(services, fake)
    state = {
        "title": "Shared memory",
        "provider": "claude",
        "summary": "Postgres is canonical.",
        "sensitivity": "normal",
    }
    await service.classify_checkpoint(dict(state))
    await service.classify_checkpoint(dict(state))
    assert fake.calls == 1


async def test_claude_zip_import_twice_is_idempotent(services, tmp_path: Path) -> None:
    swap_classifier(services, CountingJev())
    zpath = claude_zip(tmp_path / "exp.zip", [conversation("imp-1", "hello import")])
    runner = ImportRunner(services)
    first = await runner.run(zpath, provider="claude")
    second = await runner.run(zpath, provider="claude")
    assert first.conversations_imported >= 1
    assert second.conversations_imported == 0
