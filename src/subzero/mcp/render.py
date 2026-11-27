"""Readable text for tool results whose object is also sent as structured content.

Several chat apps (Claude and Gemini among them) show the model only the text
block of a tool result. A one-line count there meant those models never saw
the package at all, so the text carries the same content as the structured
result, written for a model to read rather than parse.
"""

from __future__ import annotations

from typing import Any


def _item_line(item: dict[str, Any]) -> list[str]:
    title = item.get("title") or item.get("type", "")
    kind = (item.get("provenance") or {}).get("kind", "")
    head = f"- [{item.get('layer', '')} {item.get('type', '')}, {kind}] {title}".rstrip()
    body = " ".join(str(item.get("content", "")).split())
    lines = [f"{head}: {body}" if body else head]
    for question in item.get("open_questions") or []:
        lines.append(f"  open question: {question}")
    if item.get("conflicts_with"):
        lines.append(f"  may conflict with: {', '.join(item['conflicts_with'])}")
    return lines


def bootstrap_text(payload: dict[str, Any]) -> str:
    """The bootstrap package as text: standing context first, then the handoff."""
    parts: list[str] = []
    standing = payload.get("standing") or []
    if standing:
        loaded = ", ".join(
            f"{d['path']} (id {d['content_id']}, {d['source']}"
            + (", cut" if d.get("truncated") else "")
            + ")"
            for d in standing
        )
        parts.append(
            f"Standing context from dotfiles; follow it for this whole chat. Loaded: {loaded}."
        )
        parts.extend(d["content"] for d in standing)

    items = payload.get("items") or []
    noun = "item" if len(items) == 1 else "items"
    summary = (
        f"## Subzero results: {len(items)} {noun}, "
        f"{payload.get('estimated_tokens', 0)} of {payload.get('token_budget', 0)} tokens"
    )
    if payload.get("truncated"):
        summary += ", truncated (raise token_budget for more)"
    lines = [summary]
    project = payload.get("project") or {}
    if project.get("slug"):
        lines.append(
            f"Project: {project['slug']} ({project.get('reason', project.get('source', ''))})"
        )
    for item in items:
        lines.extend(_item_line(item))
    for conflict in payload.get("conflicts") or []:
        older, newer = conflict.get("older"), conflict.get("newer")
        pair = (
            f"{older!r} vs newer {newer!r}"
            if older and newer
            else ", ".join(conflict.get("ids", []))
        )
        lines.append(f"Possible conflict: {pair}. {conflict.get('advice', '')}".rstrip())
    for note in payload.get("notes") or []:
        lines.append(f"Note: {note}")
    parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts)
