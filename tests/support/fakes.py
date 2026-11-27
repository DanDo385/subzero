"""Test doubles for classifiers and embedding providers."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from subzero.classifier.base import Answer, ClassificationResult
from subzero.classifier.questions import QUESTIONS_VERSION
from subzero.classifier.service import ClassificationService


class CountingJev:
    """Stands in for Jev: counts calls and answers with fixed probabilities."""

    name = "jev"

    def __init__(self, *, degraded: bool = False) -> None:
        self.calls = 0
        self.states: list[dict[str, Any]] = []
        self.degraded = degraded

    async def classify_checkpoint(
        self, state: dict[str, Any], candidates: dict[str, str]
    ) -> ClassificationResult:
        self.calls += 1
        self.states.append(state)
        return ClassificationResult(
            answers={
                "save_durable_memory": Answer("noul", 0.2),
                "deep_extraction": Answer("noul", 0.9),
                "destination": Answer(
                    "choice", "research", confidence=0.97, probabilities={"research": 0.97}
                ),
                "importance": Answer("score", 3, confidence=0.9),
            },
            classifier="rules" if self.degraded else "jev",
            model="jev-test",
            questions_version=QUESTIONS_VERSION,
            input_hash=f"h{self.calls}",
            degraded=self.degraded,
        )

    classify_conversation = classify_checkpoint


def swap_classifier(services: Any, fake: CountingJev) -> ClassificationService:
    service = ClassificationService(services.db, fake, services.settings)
    services.classification = service
    return service


def claude_zip(path: Path, conversations: list[dict[str, Any]]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conversations))
    return path


def conversation(uuid: str, text: str) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "name": f"Chat {uuid}",
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-01-01T11:00:00Z",
        "chat_messages": [
            {"uuid": f"{uuid}-1", "sender": "human", "text": text},
            {"uuid": f"{uuid}-2", "sender": "assistant", "text": "Understood."},
        ],
    }
