"""Memory lab end to end: import, Jev triage answers, recover, vault jobs."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from conftest import requires_db
from subzero.classifier.base import Answer, ClassificationResult
from subzero.classifier.questions import QUESTIONS_VERSION
from subzero.memorylab.recover import recover, write_structured
from support.fakes import swap_classifier as _swap_classifier

pytestmark = requires_db


#: Conversation name -> (worth, gate, bucket, relation, preference)
PLAN: dict[str, tuple[float, float, str, str, float]] = {
    "Deploy runbook": (0.95, 2.0, "resource", "new", 0.1),
    "Which project is this": (0.85, 1.0, "project", "unclear", 0.9),
    "Editor habits": (0.7, 1.2, "area", "new", 0.9),
    "Small talk": (0.2, 1.0, "discard", "new", 0.1),
}


class TriageJev:
    name = "jev"

    async def classify_checkpoint(
        self,
        state: dict[str, Any],
        candidates: dict[str, str],
        home_candidates: dict[str, str] | None = None,
        concept_candidates: dict[str, str] | None = None,
    ) -> ClassificationResult:
        worth, gate, bucket, relation, preference = PLAN[state["title"]]
        return ClassificationResult(
            answers={
                "save_durable_memory": Answer("noul", 0.2),
                "destination": Answer("choice", "research", confidence=0.9),
                "importance": Answer("score", 2, confidence=0.9),
                "worth_processing": Answer("noul", worth),
                "primary_intent": Answer("choice", "how_to", confidence=0.8),
                "has_decision": Answer("noul", 0.1),
                "has_preference": Answer("noul", preference),
                "has_procedure": Answer("noul", 0.9),
                "atomicity": Answer("score", 1, confidence=0.8),
                "para_bucket": Answer("choice", bucket, confidence=0.8),
                "relation_to_existing": Answer("choice", relation, confidence=0.8),
                "confidence_gate": Answer("score", gate, confidence=0.8),
            },
            classifier="jev",
            model="jev-test",
            questions_version=QUESTIONS_VERSION,
            input_hash=state["title"],
        )

    classify_conversation = classify_checkpoint


def _zip(path: Path) -> Path:
    conversations = [
        {
            "uuid": f"c{i}",
            "name": name,
            "created_at": "2026-03-01T10:00:00Z",
            "updated_at": "2026-03-01T11:00:00Z",
            "chat_messages": [
                {"uuid": f"c{i}-1", "sender": "human", "text": f"{name}: tell me the steps."},
                {"uuid": f"c{i}-2", "sender": "assistant", "text": "Step one, step two."},
            ],
        }
        for i, name in enumerate(PLAN)
    ]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conversations))
    return path


class FakeHoncho:
    """Records what would reach Honcho."""

    def __init__(self) -> None:
        self.sessions: dict[str, list[Any]] = {}

    def peer(self, name: str) -> Any:
        outer = self

        class Peer:
            def message(self, text: str, *, created_at: Any = None) -> dict[str, Any]:
                return {"peer": name, "content": text, "created_at": created_at}

        outer._peer = Peer()
        return outer._peer

    def session(self, sid: str, **_: Any) -> Any:
        store = self.sessions.setdefault(sid, [])

        class Session:
            def add_messages(self, batch: list[Any]) -> None:
                store.extend(batch)

        return Session()


@pytest.mark.asyncio
async def test_recover_routes_each_session_to_its_layers(services, tmp_path: Path) -> None:
    from subzero.extraction.deep import NO_NOTE
    from subzero.memorylab.layers import extraction_targets, honcho_sync

    _swap_classifier(services, TriageJev())
    await services.importer.run(
        _zip(tmp_path / "export.zip"), provider="claude", store_archive=False
    )

    preview = await recover(
        dry_run=True,
        settings=services.settings,
        db=services.db,
        notes=services.notes,
        sync_queue=services.sync_queue,
        vault_paths=services.vault_paths,
    )
    assert preview.as_dict()["vault_notes_planned"] == 1
    assert await services.db.fetchval("SELECT count(*) FROM memory_triage") == 0

    report = (
        await recover(
            dry_run=False,
            settings=services.settings,
            db=services.db,
            notes=services.notes,
            sync_queue=services.sync_queue,
            vault_paths=services.vault_paths,
        )
    ).as_dict()
    assert report["sessions_seen"] == 4
    assert report["vault_notes_planned"] == 1
    assert report["parked_in_inbox"] == 1
    assert report["inbox_drafts_written"] == 1
    assert report["layers"] == {
        "vault": 1,
        "inbox": 1,
        "structured": 3,
        "honcho": 2,
        "hot": 3,
    }

    rows = {
        r["title"]: r
        for r in await services.db.fetch(
            "SELECT c.title, t.outcome, t.layers, n.vault_path FROM memory_triage t "
            "JOIN conversations c ON c.id = t.conversation_id "
            "LEFT JOIN notes n ON n.id = t.note_id"
        )
    }
    # Stored as real JSON objects, not JSON text (a double-encoding bug once
    # made every score unreadable and every draft unrenderable).
    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM memory_triage WHERE jsonb_typeof(scores) <> 'object'"
        )
        == 0
    )
    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM notes WHERE jsonb_typeof(metadata) <> 'object'"
        )
        == 0
    )
    from subzero.obsidian.render import render_note

    draft = await services.db.fetchrow(
        "SELECT n.*, NULL AS project_slug FROM notes n WHERE vault_path LIKE '00-Inbox/%'"
    )
    rendered = render_note(dict(draft))
    assert "triage_outcome: inbox" in rendered and "jev_worth_processing: 0.85" in rendered

    # The vault note is Claude's to write; only the Inbox draft exists now.
    assert rows["Deploy runbook"]["vault_path"] is None
    assert rows["Which project is this"]["vault_path"].startswith("00-Inbox/")
    assert rows["Small talk"]["layers"] == []

    ranked = await extraction_targets(limit=10, settings=services.settings, db=services.db)
    assert [t.importance for t in ranked] == [0.95, 0.85, 0.7]  # worth is readable
    targets = {t.title: t.destination for t in ranked}
    assert targets == {
        "Deploy runbook": "resource",
        "Which project is this": NO_NOTE,
        "Editor habits": NO_NOTE,
    }
    assert next(iter(targets)) == "Deploy runbook"  # vault sessions first

    dry = (await honcho_sync(dry_run=True, settings=services.settings, db=services.db)).as_dict()
    assert dry["sessions"] == 2 and dry["messages"] == 2
    services.settings.honcho_peer = "dan"
    fake = FakeHoncho()
    sent = (
        await honcho_sync(dry_run=False, client=fake, settings=services.settings, db=services.db)
    ).as_dict()
    assert sent["sessions"] == 2 and sent["errors"] == []
    contents = [m["content"] for batch in fake.sessions.values() for m in batch]
    assert all(m.endswith("tell me the steps.") for m in contents)  # user turns only
    assert (
        await honcho_sync(dry_run=True, settings=services.settings, db=services.db)
    ).sessions == 0  # not sent twice

    jobs = {r["vault_path"] for r in await services.db.fetch("SELECT vault_path FROM sync_jobs")}
    assert "Experiments/Analysis/hot-memory-candidates.md" in jobs
    assert any(p.startswith("Experiments/Memory-Logs/recovery-log") for p in jobs)
    candidates = await services.db.fetchval(
        "SELECT content FROM notes WHERE vault_path = 'Experiments/Analysis/hot-memory-candidates.md'"
    )
    assert "Editor habits" in candidates.split("## USER.md")[1]

    again = await recover(
        dry_run=False,
        settings=services.settings,
        db=services.db,
        notes=services.notes,
        sync_queue=services.sync_queue,
        vault_paths=services.vault_paths,
    )
    assert again.seen == 0 and again.skipped_unchanged == 4

    # A later run that decides only one session still reports the whole history.
    await services.db.execute(
        "UPDATE memory_triage SET classification_event_id = NULL WHERE conversation_id = "
        "(SELECT id FROM conversations WHERE title = 'Small talk')"
    )
    assert (
        await recover(
            dry_run=False,
            settings=services.settings,
            db=services.db,
            notes=services.notes,
            sync_queue=services.sync_queue,
            vault_paths=services.vault_paths,
        )
    ).seen == 1
    report_text = await services.db.fetchval(
        "SELECT content FROM notes WHERE vault_path LIKE 'Experiments/Analysis/recovery-report%'"
    )
    assert "All 4 triaged sessions" in report_text and "decided 1 of them" in report_text
    candidates = await services.db.fetchval(
        "SELECT content FROM notes WHERE vault_path = 'Experiments/Analysis/hot-memory-candidates.md'"
    )
    assert "Editor habits" in candidates

    assert (
        await write_structured(
            settings=services.settings,
            db=services.db,
            notes=services.notes,
            sync_queue=services.sync_queue,
            vault_paths=services.vault_paths,
        )
        >= 0
    )


_STORED_SQL = """
SELECT conversation_id, classification_event_id, questions_version, outcome, bucket,
       reason, gate, candidates, scores, note_id, layers
FROM memory_triage ORDER BY conversation_id
"""


@pytest.mark.asyncio
async def test_decisions_are_written_in_batches_and_stay_idempotent(
    services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5,629 sessions one upsert at a time took a quarter of an hour over the
    pooler. Batching must not change what ends up in the table."""
    import subzero.memorylab.recover as recover_module

    _swap_classifier(services, TriageJev())
    await services.importer.run(
        _zip(tmp_path / "export.zip"), provider="claude", store_archive=False
    )

    monkeypatch.setattr(recover_module, "TRIAGE_WRITE_BATCH", 3)
    real_store = recover_module._store
    batch_sizes: list[int] = []

    async def counting_store(rows: list[tuple[Any, ...]], *, db: Any) -> None:
        batch_sizes.append(len(rows))
        await real_store(rows, db=db)

    monkeypatch.setattr(recover_module, "_store", counting_store)
    report = await recover(
        dry_run=False,
        settings=services.settings,
        db=services.db,
        notes=services.notes,
        sync_queue=services.sync_queue,
        vault_paths=services.vault_paths,
    )

    assert report.seen == 4
    assert batch_sizes == [3, 1]  # two round trips for four sessions, not four
    stored = [dict(r) for r in await services.db.fetch(_STORED_SQL)]
    assert len(stored) == 4
    assert all(r["questions_version"] == QUESTIONS_VERSION for r in stored)
    assert sum(1 for r in stored if r["note_id"] is not None) == 1  # the Inbox draft

    await recover(
        dry_run=False,
        redo=True,
        settings=services.settings,
        db=services.db,
        notes=services.notes,
        sync_queue=services.sync_queue,
        vault_paths=services.vault_paths,
    )
    assert [dict(r) for r in await services.db.fetch(_STORED_SQL)] == stored
