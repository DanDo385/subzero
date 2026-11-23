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
from subzero.classifier.service import ClassificationService
from subzero.importers.runner import ImportRunner
from subzero.memorylab.recover import recover, write_structured

pytestmark = requires_db


#: Conversation name -> (worth, gate, bucket, relation, preference)
PLAN: dict[str, tuple[float, float, str, str, float]] = {
    "Deploy runbook": (0.95, 2.0, "resource", "new", 0.1),
    "Maybe a project": (0.85, 1.0, "project", "new", 0.9),
    "Small talk": (0.2, 1.0, "discard", "new", 0.1),
}


class TriageJev:
    name = "jev"

    async def classify_checkpoint(
        self, state: dict[str, Any], candidates: dict[str, str]
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


@pytest.mark.asyncio
async def test_recover_writes_notes_drafts_and_logs(services, tmp_path: Path) -> None:
    services.classification = ClassificationService(services.db, TriageJev(), services.settings)
    await ImportRunner(services).run(
        _zip(tmp_path / "export.zip"), provider="claude", store_archive=False
    )

    preview = await recover(services, dry_run=True)
    assert preview.as_dict()["notes_created_directly"] == 1
    assert await services.db.fetchval("SELECT count(*) FROM memory_triage") == 0

    report = (await recover(services, dry_run=False)).as_dict()
    assert report["sessions_seen"] == 3
    assert report["notes_created_directly"] == 1
    assert report["parked_in_inbox"] == 1
    assert report["logged_only"] == 1
    assert report["memory_candidates"] == {"procedure": 2, "preference": 1}

    paths = {
        r["outcome"]: r["vault_path"]
        for r in await services.db.fetch(
            "SELECT t.outcome, n.vault_path FROM memory_triage t "
            "LEFT JOIN notes n ON n.id = t.note_id"
        )
    }
    assert paths["note"].startswith("60-Resources/deploy-runbook")
    assert paths["inbox"].startswith("00-Inbox/maybe-a-project")
    assert paths["log"] is None

    jobs = {r["vault_path"] for r in await services.db.fetch("SELECT vault_path FROM sync_jobs")}
    assert paths["note"] in jobs and paths["inbox"] in jobs
    assert any(p.startswith("Experiments/Analysis/recovery-report") for p in jobs)
    assert any(p.startswith("Experiments/Memory-Logs/recovery-log") for p in jobs)
    assert "Experiments/Analysis/memory-candidates.md" in jobs

    # Idempotent: nothing changed, nothing decided again.
    again = await recover(services, dry_run=False)
    assert again.seen == 0 and again.skipped_unchanged == 3

    assert await write_structured(services) >= 0
    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM sync_jobs WHERE vault_path LIKE 'Experiments/Structured/%'"
        )
        == 1
    )
