"""Slim projections for tool results.

Host models pay for every byte of a tool result, and some hosts also echo the
structured payload as text. Default responses therefore keep only the fields a
caller needs to answer or to take the next action.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def dump(value: Any) -> Any:
    """JSON-ready value with nulls and empty containers removed."""
    if isinstance(value, BaseModel):
        return dump(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, list):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            projected = dump(item)
            if projected is None or projected == "" or projected == [] or projected == {}:
                continue
            out[key] = projected
        return out
    return value
