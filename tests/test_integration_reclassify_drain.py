"""Draining the reclassification queue respects privacy and empties the queue.

Two bugs lived here. Artifact classification always told the classifier the
artifact was ``normal``, whatever its stored level, so the privacy check before
any external call never saw a ``highly_sensitive`` artifact for what it was.
And ``subzero reclassify`` skipped a queued live checkpoint without removing
it, so it sat at the head of the queue for ever.
"""

from __future__ import annotations

from typing import Any

from conftest import requires_db
from subzero.artifacts.filing import classify_and_file
from subzero.importers.reclassify import drain_reclassification_queue

pytestmark = requires_db


def _record_artifact_states(services) -> list[dict[str, Any]]:
    """Capture every state the classifier is handed for an artifact."""
    seen: list[dict[str, Any]] = []
    classifier = services.classification._classifier
    original = classifier.classify_artifact

    async def recording(state, *args, **kwargs):
        seen.append(dict(state))
        return await original(state, *args, **kwargs)

    classifier.classify_artifact = recording
    return seen


async def _sensitive_artifact(services):
    return await services.artifacts.save_files(
        title="Tax return 2025",
        files={"return.txt": b"SSN and income"},
        sensitivity="highly_sensitive",
    )


async def test_classifying_an_artifact_passes_its_stored_sensitivity(services):
    seen = _record_artifact_states(services)
    record = await _sensitive_artifact(services)

    await classify_and_file(services, record, stated_project=None)

    assert [s["sensitivity"] for s in seen] == ["highly_sensitive"]


async def test_draining_a_queued_artifact_keeps_its_sensitivity_and_records_it(services):
    seen = _record_artifact_states(services)
    record = await _sensitive_artifact(services)
    await services.classification.queue_reclassification(
        object_type="artifact", object_id=record.id, reason="classifier_degraded"
    )
    await services.db.execute("UPDATE reclassification_queue SET next_attempt_at = now()")

    done, total = await drain_reclassification_queue(services, limit=10)

    assert (done, total) == (1, 1)
    assert [s["sensitivity"] for s in seen] == ["highly_sensitive"]
    assert (
        await services.db.fetchval(
            "SELECT count(*) FROM artifact_classifications WHERE artifact_id = $1", record.id
        )
        == 1
    )
    assert await services.db.fetchval("SELECT count(*) FROM reclassification_queue") == 0


async def test_draining_a_live_checkpoint_classifies_it_and_clears_it(services):
    conv_id, _ = await services.sessions.ensure_conversation(
        provider="claude", provider_conversation_id="live-1", title="Vault sync", imported=False
    )
    await services.db.execute(
        """
        INSERT INTO session_summaries (conversation_id, provider, summary, summary_sha256)
        VALUES ($1, 'claude', $2, 'x')
        """,
        conv_id,
        "Decided the worker keeps its own record of what it wrote.",
    )
    await services.classification.queue_reclassification(
        object_type="checkpoint", object_id=conv_id, reason="classifier_degraded"
    )
    await services.db.execute("UPDATE reclassification_queue SET next_attempt_at = now()")

    done, total = await drain_reclassification_queue(services, limit=10)

    assert (done, total) == (1, 1)
    assert await services.db.fetchval("SELECT count(*) FROM reclassification_queue") == 0
