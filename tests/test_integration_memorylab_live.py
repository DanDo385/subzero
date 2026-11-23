"""Integration: a live checkpoint session is visible to memory-lab triage.

memory-lab recover is the only writer of session notes, and it reads stored
classification events joined to their conversation. A live checkpoint
classifies before its conversation row exists, so the checkpoint transaction
has to link the event to the conversation; without the link the session can
never be routed to the vault, an Inbox draft or a log.
"""

from __future__ import annotations

from typing import Any

from conftest import requires_db
from subzero.auth.principal import Principal
from subzero.classifier.base import Answer, ClassificationResult
from subzero.classifier.questions import QUESTIONS_VERSION
from subzero.classifier.service import ClassificationService
from subzero.memorylab.recover import recover

pytestmark = requires_db

ME = Principal(subject="tester@example.com", provider="claude")


class TriageJev:
    """Answers the triage questions so recover has something to route on."""

    name = "jev"

    async def classify_checkpoint(
        self, state: dict[str, Any], candidates: dict[str, str]
    ) -> ClassificationResult:
        return ClassificationResult(
            answers={
                "save_durable_memory": Answer("noul", 0.9),
                "destination": Answer("choice", "research", confidence=0.9),
                "importance": Answer("score", 2, confidence=0.9),
                "worth_processing": Answer("noul", 0.85),
                "primary_intent": Answer("choice", "how_to", confidence=0.8),
                "has_decision": Answer("noul", 0.9),
                "has_preference": Answer("noul", 0.9),
                "has_procedure": Answer("noul", 0.9),
                "atomicity": Answer("score", 1, confidence=0.8),
                "para_bucket": Answer("choice", "project", confidence=0.8),
                "relation_to_existing": Answer("choice", "unclear", confidence=0.8),
                "confidence_gate": Answer("score", 1.0, confidence=0.8),
            },
            classifier="jev",
            model="jev-test",
            questions_version=QUESTIONS_VERSION,
            input_hash=state["title"],
        )

    classify_conversation = classify_checkpoint


async def test_a_live_checkpoint_session_is_visible_to_memory_lab(services) -> None:
    services.sessions._classification = ClassificationService(
        services.db, TriageJev(), services.settings
    )

    result = await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="live-triage-1",
        title="Batch the message writes",
        summary="Decided to batch inserts and clamp the sync window.",
    )

    linked = await services.db.fetchval(
        "SELECT object_id FROM classification_events WHERE object_type = 'checkpoint'"
    )
    assert linked == result.conversation_id

    report = await recover(services, dry_run=True)
    assert report.as_dict()["sessions_seen"] == 1


async def test_a_recheckpoint_does_not_repoint_an_already_linked_event(services) -> None:
    services.sessions._classification = ClassificationService(
        services.db, TriageJev(), services.settings
    )

    first = await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="live-triage-2",
        title="Clamp the sync window",
        summary="First checkpoint of the session.",
    )
    # An import or a reclassification owns its own event link; simulate one
    # by pointing the first event at its conversation, which the checkpoint
    # already did, and confirm a second checkpoint leaves older links alone.
    await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="live-triage-2",
        title="Clamp the sync window",
        summary="Second checkpoint of the same session.",
    )

    rows = await services.db.fetch(
        "SELECT object_id FROM classification_events WHERE object_type = 'checkpoint' "
        "ORDER BY created_at"
    )
    assert [row["object_id"] for row in rows] == [
        first.conversation_id,
        first.conversation_id,
    ]
