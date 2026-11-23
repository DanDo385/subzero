"""Integration: a checkpoint that promotes to the vault, end to end.

Every other checkpoint test passes ``queue_for_vault=False``, which left the
promotion branch of the system's main write path uncovered. That is where a
checkpoint turns into a note, a vault path and a queued job, and it reads back
state that earlier stages of the same transaction wrote.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import requires_db
from subzero.auth.principal import Principal
from subzero.classifier.policy import Verdict
from subzero.models import CheckpointFact

pytestmark = requires_db

ME = Principal(subject="tester@example.com", provider="claude")


def _promotion(**overrides):
    """Make the classifier promote the session, whatever the rules decided.

    The routing is the classifier's output and the point of these tests is what
    the checkpoint does with a "yes", not how a "yes" was arrived at.
    """

    def apply(monkeypatch, services):
        original = services.classification.classify_checkpoint
        wanted = {"promote_to_vault": Verdict.YES, "destination": "inbox", **overrides}

        async def classify(state, **kwargs):
            result, routing, event_id = await original(state, **kwargs)
            return result, replace(routing, **wanted), event_id

        monkeypatch.setattr(services.classification, "classify_checkpoint", classify)

    return apply


async def test_a_promoted_checkpoint_queues_its_note(services, monkeypatch) -> None:
    """A fact, a summary and a promotion, in one call.

    Promotion runs after the memories are written and reads the conversation's
    project slug back. Saving a memory used to rebind the name it read that
    from, so this raised after the commit: the checkpoint was stored, the tool
    reported a failure, and no vault job existed.
    """
    _promotion()(monkeypatch, services)

    result = await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="promo-1",
        title="Batch the message writes",
        summary="Decided to batch inserts and clamp the sync window.",
        facts=[CheckpointFact(content="Dan prefers a boring design to a clever one")],
    )

    assert result.queued_vault_jobs == 1
    assert result.created["memories"] == 1

    job = await services.db.fetchrow(
        "SELECT job_type, object_type, object_id, vault_path, content_sha256 "
        "FROM sync_jobs WHERE status IN ('queued', 'approved')"
    )
    assert job is not None
    assert job["job_type"] == "write_note"
    assert job["object_type"] == "note"
    assert job["vault_path"].endswith(".md")
    # The job says what it expects to write, so the worker can refuse to
    # overwrite a human edit it has not seen.
    assert job["content_sha256"]

    note = await services.db.fetchrow(
        "SELECT vault_path, content FROM notes WHERE id = $1", job["object_id"]
    )
    assert note is not None
    assert note["vault_path"] == job["vault_path"]
    assert "Decided to batch inserts" in note["content"]

    summary_note_id = await services.db.fetchval(
        """
        SELECT s.metadata->>'vault_note_id'
        FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id
        WHERE c.provider_conversation_id = 'promo-1'
        """
    )
    assert summary_note_id == str(job["object_id"])


async def test_a_failure_after_the_note_rolls_the_checkpoint_back(services, monkeypatch) -> None:
    """The note and its job are inside the checkpoint's transaction.

    They used to be written after COMMIT, on their own connections, so a
    failure in the stages that followed left a note and a queued vault job
    behind for a checkpoint the caller was told had failed.
    """
    _promotion(needs_review=True, review_reasons=["classifier_requested_review"])(
        monkeypatch, services
    )

    async def refuse(*args, **kwargs):
        raise RuntimeError("review queueing failed")

    monkeypatch.setattr(services.sessions, "_queue_reviews", refuse)

    with pytest.raises(RuntimeError, match="review queueing failed"):
        await services.sessions.checkpoint(
            provider="claude",
            principal=ME,
            provider_conversation_id="promo-2",
            title="Second promotion",
            summary="Checking the note does not outlive a failed checkpoint.",
        )

    assert await services.db.fetchval("SELECT count(*) FROM conversations") == 0
    assert await services.db.fetchval("SELECT count(*) FROM notes") == 0
    assert await services.db.fetchval("SELECT count(*) FROM sync_jobs") == 0
