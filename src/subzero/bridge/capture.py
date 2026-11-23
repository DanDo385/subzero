"""What a published bridge event may contain, decided before it is stored.

Every event passes through :func:`capture_event`, which

1. removes hidden-reasoning blocks (``<thinking>`` and friends) - a harness
   should never send them, and if one slips through it is not kept;
2. redacts secrets: well-known token shapes, ``NAME=value`` assignments whose
   name says it is a secret, credentials inside URLs, and the exact values of
   this server's own configured secrets;
3. truncates long text to display limits;

and records every one of those as an *omission*. An event with any omission
is ``partial``; an event with none is ``complete``. That is the explicit
capture-completeness contract: a reader can always tell whether it is looking
at everything the terminal showed or at a reduced copy.

Event ids are deterministic: ``uuid5(session_id, event_key)``. The key is the
publisher's own (``event_key``) or, without one, a hash of the captured
content, so a resend after a reconnect lands on the same id.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from subzero.models import BridgeEventCreate

#: Namespace for event ids. Fixed forever: changing it changes every id.
EVENT_ID_NAMESPACE = UUID("6f0e3c3a-2b8e-5d0b-9c51-8a3f7f2a9b10")

#: Display limits (characters). Longer text is truncated, not refused.
CONTENT_LIMIT = 32_000
EXCERPT_LIMIT = 16_000
DIFF_LIMIT = 200_000
PATCH_FILES_LIMIT = 500
_EXCERPT_FIELDS = frozenset({"input_summary", "output_excerpt"})

_REASONING = re.compile(
    r"<(thinking|think|reasoning|scratchpad)(?:\s[^>]*)?>.*?(?:</\1\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)

_SECRET_NAME = r"[A-Za-z0-9_.-]*(?:secret|token|passw(?:or)?d|pwd|api[_-]?key|apikey|private[_-]?key|access[_-]?key|credential)[A-Za-z0-9_.-]*"

#: (pattern, label). Conservative on purpose: over-redaction costs a little
#: readability, under-redaction costs a secret.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
        "private-key",
    ),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "api-key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "github-token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github-token"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "aws-key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "slack-token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "google-key"),
    (re.compile(r"\b(?:pcd|ops)_[A-Za-z0-9_-]{20,}"), "token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "jwt"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), "token"),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), "url-password"),
    (
        re.compile(
            rf"(?i)(\b{_SECRET_NAME}\b[\"']?\s*[:=]\s*)(\"[^\"\n]{{3,}}\"|'[^'\n]{{3,}}'|[^\s\"',;]{{3,}})"
        ),
        "assignment",
    ),
)


@dataclass(frozen=True)
class CapturedEvent:
    """An event as it will be stored: reduced, labelled and keyed."""

    kind: str
    role: str
    model: str | None
    content: str
    payload: dict[str, Any]
    omissions: list[str]
    event_key: str
    key_supplied: bool

    @property
    def completeness(self) -> str:
        return "partial" if self.omissions else "complete"

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def fingerprint(self) -> str:
        return event_fingerprint(self.kind, self.role, self.content, self.payload)

    def event_id(self, session_id: UUID) -> UUID:
        return event_id(session_id, self.event_key)


def event_id(session_id: UUID, event_key: str) -> UUID:
    return uuid5(EVENT_ID_NAMESPACE, f"{session_id}\n{event_key}")


def event_fingerprint(kind: str, role: str, content: str, payload: dict[str, Any]) -> str:
    """Identity of an event's substance, used to refuse a reused key."""
    canonical = json.dumps(
        {"kind": kind, "role": role, "content": content, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Reducer:
    def __init__(self, known_secrets: Iterable[str]) -> None:
        # Longest first, so a secret that contains another is replaced whole.
        self._known = sorted({s for s in known_secrets if len(s) >= 8}, key=len, reverse=True)
        self.redactions = 0
        self.reasoning = False
        self.truncated: list[str] = []

    def text(self, value: str, *, field: str, limit: int | None) -> str:
        value = value.replace("\x00", "")
        value, removed = _REASONING.subn("[hidden reasoning omitted]", value)
        if removed:
            self.reasoning = True
        for secret in self._known:
            if secret in value:
                self.redactions += value.count(secret)
                value = value.replace(secret, "[redacted:server-secret]")
        for pattern, label in _PATTERNS:
            value = pattern.sub(lambda m, label=label: self._replace(m, label), value)
        if limit is not None and len(value) > limit:
            dropped = len(value) - limit
            value = f"{value[:limit]}\n[truncated {dropped} characters]"
            self.truncated.append(field)
        return value

    def _replace(self, match: re.Match[str], label: str) -> str:
        self.redactions += 1
        if label in ("assignment", "url-password"):
            return f"{match.group(1)}[redacted:{label}]" + ("@" if label == "url-password" else "")
        if label == "token" and match.group(0).lower().startswith(("bearer", "basic")):
            return f"{match.group(1)} [redacted:token]"
        return f"[redacted:{label}]"

    def payload(self, value: Any, *, field: str) -> Any:
        if isinstance(value, str):
            leaf = field.rsplit(".", 1)[-1]
            if leaf == "diff":
                limit: int | None = DIFF_LIMIT
            elif leaf in _EXCERPT_FIELDS:
                limit = EXCERPT_LIMIT
            else:
                limit = None  # short fields are already capped by the model
            return self.text(value, field=field, limit=limit)
        if isinstance(value, dict):
            return {k: self.payload(v, field=f"{field}.{k}") for k, v in value.items()}
        if isinstance(value, list):
            return [self.payload(v, field=field) for v in value]
        return value


def capture_event(event: BridgeEventCreate, *, known_secrets: Iterable[str] = ()) -> CapturedEvent:
    """Reduce one published event to what may be stored and shown."""
    reducer = _Reducer(known_secrets)
    content = reducer.text(event.text, field="text", limit=CONTENT_LIMIT)

    details = event.details()
    payload: dict[str, Any] = {}
    if details is not None:
        raw = details.model_dump(mode="json", exclude_none=True)
        if event.kind == "patch" and len(raw.get("files", [])) > PATCH_FILES_LIMIT:
            raw["files"] = raw["files"][:PATCH_FILES_LIMIT]
            reducer.truncated.append(f"{event.kind}.files")
        payload = reducer.payload(raw, field=event.kind)

    omissions: list[str] = []
    if reducer.reasoning:
        omissions.append("hidden_reasoning")
    if reducer.redactions:
        omissions.append(f"redacted:{reducer.redactions}")
    omissions.extend(f"truncated:{name}" for name in dict.fromkeys(reducer.truncated))
    # A publisher's own notes are kept apart from the server's markers so they
    # can never be mistaken for, or hide, a server-side reduction.
    omissions.extend(f"publisher:{reducer.text(note, field='omissions', limit=80)}" for note in event.omissions)

    if event.event_key is not None:
        key = event.event_key.strip()
        supplied = True
    elif event.kind == "message" and not event.omissions:
        # The receive bridge keyed messages by content hash; keep that so a
        # harness resending an old message still gets "duplicate".
        key = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
        supplied = False
    else:
        key = f"sha256:{event_fingerprint(event.kind, event.role, content, payload)}"
        supplied = False

    return CapturedEvent(
        kind=event.kind,
        role=event.role,
        model=event.model,
        content=content,
        payload=payload,
        omissions=omissions,
        event_key=key,
        key_supplied=supplied,
    )
