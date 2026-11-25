"""Classification orchestration and its event log.

Wraps the classifier + policy engine so that every routing decision, from Jev
or from the deterministic fallback, is persisted to ``classification_events``
before anything acts on it. That table is what makes it possible to ask later
whether the classifications were any good.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

from subzero.classifier.base import (
    ClassificationResult,
    Classifier,
    answers_from_json,
)
from subzero.classifier.policy import PolicyEngine, Routing
from subzero.classifier.questions import QUESTIONS_VERSION
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.logging_setup import get_logger
from subzero.util import sha256_text

logger = get_logger(__name__)


class ClassificationService:
    def __init__(self, db: Database, classifier: Classifier, settings: Settings) -> None:
        self._db = db
        self._classifier = classifier
        self._settings = settings

    def policy(self, *, historical_import: bool = False) -> PolicyEngine:
        return PolicyEngine(self._settings, historical_import=historical_import)

    async def project_candidates(self, *, limit: int | None = None) -> dict[str, str]:
        """Active projects, most recently touched first, as Choice criteria.

        Bounded on purpose: handing Jev an unbounded list of every project
        ever created makes the Choice worse. When the cap bites, the oldest
        inactive projects drop off and the object lands in review rather than
        being assigned to a project that was never offered.
        """
        limit = limit or self._settings.jev_max_project_candidates
        rows = await self._db.fetch(
            """
            SELECT slug, name, coalesce(description, goals, '') AS blurb
            FROM projects
            WHERE status IN ('active', 'paused')
            ORDER BY last_active_at DESC
            LIMIT $1
            """,
            limit,
        )
        return {
            row["slug"]: (f"{row['name']}. {row['blurb']}".strip()[:300] or row["name"])
            for row in rows
        }

    # ------------------------------------------------------------ recording

    async def record(
        self,
        result: ClassificationResult,
        routing: Routing,
        *,
        object_type: str,
        object_id: UUID | None,
        extra: dict[str, Any] | None = None,
    ) -> UUID | None:
        return await self.record_raw(
            result,
            object_type=object_type,
            object_id=object_id,
            action_taken=routing.action_taken,
            confidence=routing.confidence,
            metadata={"routing": routing.as_json(), **(extra or {})},
        )

    async def record_raw(
        self,
        result: ClassificationResult,
        *,
        object_type: str,
        object_id: UUID | None,
        action_taken: str,
        confidence: float | None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        try:
            return await self._db.fetchval(
                """
                INSERT INTO classification_events (
                    object_type, object_id, classifier, model, request_id,
                    questions_version, input_hash, answers, confidence,
                    action_taken, degraded, input_tokens, duration_ms, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                RETURNING id
                """,
                object_type,
                object_id,
                result.classifier,
                result.model,
                result.request_id,
                result.questions_version,
                result.input_hash,
                result.answers_json(),
                confidence,
                action_taken,
                result.degraded,
                result.input_tokens,
                result.duration_ms,
                {"error": result.error, **(metadata or {})},
            )
        except Exception:
            logger.exception("failed to persist classification event")
            return None

    async def queue_reclassification(
        self,
        *,
        object_type: str,
        object_id: UUID,
        reason: str,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        """Put an object on the durable classification work queue.

        Pass the connection an open transaction is using when the object is
        being created in it: then "saved" and "queued for classification"
        commit together or not at all. Re-queueing an object that is already
        there raises its attempt count and pushes ``next_attempt_at`` out
        exponentially, to an hour at most.
        """
        executor = conn or self._db
        await executor.execute(
            """
            INSERT INTO reclassification_queue (object_type, object_id, reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (object_type, object_id) DO UPDATE
                SET reason = EXCLUDED.reason,
                    attempts = reclassification_queue.attempts + 1,
                    next_attempt_at = now() + make_interval(
                        mins => least(60, power(2, reclassification_queue.attempts + 1)::int)
                    ),
                    updated_at = now()
            """,
            object_type,
            object_id,
            reason,
        )

    async def queue_review(
        self,
        *,
        object_type: str,
        object_id: UUID,
        reason: str,
        question: str,
        options: list[dict[str, Any]] | None = None,
        suggested: dict[str, Any] | None = None,
        confidence: float | None = None,
        classification_event_id: UUID | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> UUID | None:
        executor = conn or self._db
        return await executor.fetchval(
            """
            INSERT INTO review_queue (
                object_type, object_id, reason, question, options, suggested,
                confidence, classification_event_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (object_type, object_id, reason) WHERE status = 'open'
                DO UPDATE SET question = EXCLUDED.question,
                              options = EXCLUDED.options,
                              suggested = EXCLUDED.suggested,
                              confidence = EXCLUDED.confidence,
                              updated_at = now()
            RETURNING id
            """,
            object_type,
            object_id,
            reason,
            question,
            options or [],
            suggested,
            confidence,
            classification_event_id,
        )

    # --------------------------------------------------------- classify + act

    async def _cached(self, cache_key: str) -> ClassificationResult | None:
        row = await self._db.fetchrow(
            """
            SELECT answers, classifier, model, request_id, questions_version, input_hash
            FROM classification_events
            WHERE metadata->>'cache_key' = $1 AND NOT degraded
            ORDER BY created_at DESC
            LIMIT 1
            """,
            cache_key,
        )
        if row is None:
            return None
        return ClassificationResult(
            answers=answers_from_json(row["answers"]),
            classifier=row["classifier"],
            model=row["model"],
            request_id=row["request_id"],
            questions_version=row["questions_version"],
            input_hash=row["input_hash"],
            input_tokens=0,
            duration_ms=0,
        )

    async def home_candidates(self, *, limit: int | None = None) -> dict[str, str]:
        """Lessons and setups already in use, most filed first.

        Offered to Jev so a session whose author typed no sigils can still be
        filed against vocabulary the vault already has. Only homes that exist
        are offered: Jev chooses from a list, it does not invent one.
        """
        rows = await self._db.fetch(
            """
            SELECT home, count(*) AS n
            FROM (
                SELECT metadata->>'home' AS home FROM notes WHERE metadata ? 'home'
                UNION ALL
                SELECT metadata->>'home' FROM conversations WHERE metadata ? 'home'
            ) homes
            WHERE home LIKE 'lesson/%' OR home LIKE 'setup/%'
            GROUP BY home
            ORDER BY n DESC, home
            LIMIT $1
            """,
            limit or self._settings.jev_max_home_candidates,
        )
        return {row["home"]: f"{row['home']}, {row['n']} filed here" for row in rows}

    async def concept_candidates(self, *, limit: int | None = None) -> dict[str, str]:
        """Concepts already tagged, most used first. Same rule as homes."""
        rows = await self._db.fetch(
            """
            WITH tags AS (
                SELECT jsonb_array_elements_text(metadata->'tags') AS tag
                FROM notes WHERE metadata ? 'tags'
                UNION ALL
                SELECT jsonb_array_elements_text(metadata->'tags')
                FROM conversations WHERE metadata ? 'tags'
            )
            SELECT substr(tag, 9) AS concept, count(*) AS n
            FROM tags
            WHERE tag LIKE 'concept/%' AND length(tag) > 9
            GROUP BY concept
            ORDER BY n DESC, concept
            LIMIT $1
            """,
            limit or self._settings.jev_max_concept_candidates,
        )
        return {row["concept"]: f"concept/{row['concept']}, {row['n']} tagged" for row in rows}

    def cache_key(
        self,
        kind: str,
        state: dict[str, Any],
        candidates: dict[str, str],
        key_extras: dict[str, Any] | None = None,
    ) -> str:
        """Identity of one question: same state, options, questions and model.

        ``key_extras`` carries the other candidate lists (homes, concepts). An
        answer cached against one offered list must not be reused when the
        list changed, or the new questions would go unasked.
        """
        return sha256_text(
            json.dumps(
                {
                    "kind": kind,
                    "questions": QUESTIONS_VERSION,
                    "classifier": self._classifier.name,
                    "model": self._settings.jev_model,
                    "state": {k: v for k, v in state.items() if k != "input_hash"},
                    "candidates": candidates,
                    "extras": key_extras or {},
                },
                sort_keys=True,
                default=str,
            )
        )

    async def ask_cached(
        self,
        kind: str,
        ask: Callable[[dict[str, Any], dict[str, str]], Awaitable[ClassificationResult]],
        state: dict[str, Any],
        candidates: dict[str, str],
        *,
        use_cache: bool = True,
        key_extras: dict[str, Any] | None = None,
    ) -> tuple[ClassificationResult, dict[str, Any]]:
        """Ask, or answer from the cache. Returns the result and the metadata
        to record with it (the cache key and whether it was a hit)."""
        # Only Jev answers are worth caching: rules are free and deterministic,
        # and a degraded or privacy-blocked result must be asked again.
        cacheable = self._settings.classification_cache and self._classifier.name == "jev"
        key = self.cache_key(kind, state, candidates, key_extras) if cacheable else None
        result = await self._cached(key) if key and use_cache else None
        cache_hit = result is not None
        if result is None:
            result = await ask(state, candidates)
        extra: dict[str, Any] = {}
        if key and result.classifier == "jev" and not result.degraded:
            extra = {"cache_key": key, "cache_hit": cache_hit}
        return result, extra

    async def classify_note_link(
        self, state: dict[str, Any], *, object_id: UUID | None = None
    ) -> tuple[ClassificationResult, dict[str, Any]]:
        classifier = self._classifier
        return await self.ask_cached(
            "note_link", lambda s, _c: classifier.classify_note_link(s), state, {}
        )

    async def classify_moc(
        self, state: dict[str, Any], candidates: dict[str, str]
    ) -> tuple[ClassificationResult, dict[str, Any]]:
        return await self.ask_cached("moc", self._classifier.classify_moc, state, candidates)

    async def _classify(
        self,
        kind: str,
        ask: Callable[[dict[str, Any], dict[str, str]], Awaitable[ClassificationResult]],
        state: dict[str, Any],
        *,
        object_id: UUID | None,
        historical_import: bool,
        use_cache: bool = True,
        key_extras: dict[str, Any] | None = None,
    ) -> tuple[ClassificationResult, Routing, UUID | None]:
        candidates = await self.project_candidates()
        result, extra = await self.ask_cached(
            kind, ask, state, candidates, use_cache=use_cache, key_extras=key_extras
        )
        routing = self.policy(historical_import=historical_import).route(result)
        event_id = await self.record(
            result, routing, object_type=kind, object_id=object_id, extra=extra
        )
        if result.degraded and object_id is not None:
            await self.queue_reclassification(
                object_type=kind, object_id=object_id, reason="classifier_degraded"
            )
        return result, routing, event_id

    async def classify_checkpoint(
        self,
        state: dict[str, Any],
        *,
        object_id: UUID | None = None,
        historical_import: bool = False,
        use_cache: bool = True,
    ) -> tuple[ClassificationResult, Routing, UUID | None]:
        homes = await self.home_candidates()
        concepts = await self.concept_candidates()
        classifier = self._classifier

        async def ask(state_: dict[str, Any], candidates: dict[str, str]) -> ClassificationResult:
            return await classifier.classify_checkpoint(state_, candidates, homes, concepts)

        return await self._classify(
            "checkpoint",
            ask,
            state,
            object_id=object_id,
            historical_import=historical_import,
            use_cache=use_cache,
            key_extras={"homes": homes, "concepts": concepts},
        )

    async def classify_artifact(
        self,
        state: dict[str, Any],
        *,
        object_id: UUID | None = None,
        historical_import: bool = False,
        use_cache: bool = True,
    ) -> tuple[ClassificationResult, Routing, UUID | None]:
        return await self._classify(
            "artifact",
            self._classifier.classify_artifact,
            state,
            object_id=object_id,
            historical_import=historical_import,
            use_cache=use_cache,
        )

    async def classify_note(
        self, state: dict[str, Any], *, object_id: UUID | None = None
    ) -> tuple[ClassificationResult, Routing, UUID | None]:
        # Notes are human-edited and change often; not worth caching.
        candidates = await self.project_candidates()
        result = await self._classifier.classify_note(state, candidates)
        routing = self.policy().route(result)
        event_id = await self.record(result, routing, object_type="note", object_id=object_id)
        return result, routing, event_id
