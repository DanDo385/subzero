"""Markdown rendering for the vault.

Generated sections (a project's next steps, related notes, maps, sources) are
wrapped in one marked block that is regenerated on every write and stripped
from a human edit on the way back in, so a re-render never clobbers prose a
person typed and a person's prose never leaks generated links into the store.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {v}" for v in value)
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            text = str(value).replace("\n", " ")
            lines.append(f'{key}: "{text}"' if ":" in text or "#" in text else f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines)


def _triage_fields(meta: dict[str, Any]) -> dict[str, Any]:
    """Memory-lab triage outcome and Jev scores, flattened for frontmatter."""
    raw_triage, raw_jev = meta.get("triage"), meta.get("jev")
    triage: dict[str, Any] = raw_triage if isinstance(raw_triage, dict) else {}
    jev: dict[str, Any] = raw_jev if isinstance(raw_jev, dict) else {}
    fields: dict[str, Any] = {}
    if triage:
        fields["triage_outcome"] = triage.get("outcome")
        fields["triage_reason"] = triage.get("reason")
        fields["triage_gate"] = triage.get("gate")
        fields["memory_candidate"] = triage.get("candidates") or None
    for key, value in jev.items():
        if isinstance(value, str | int | float) and not isinstance(value, bool):
            fields[f"jev_{key}"] = value
    return fields


def render_next_steps(open_tasks: list[dict[str, Any]]) -> list[str]:
    """The open tasks of a project or area, as a generated Next steps section.

    Rendered inside the note's generated block, so a re-render refreshes it
    and a human edit to the surrounding body is never touched. An empty task
    list renders no section at all. The bullets are plain, not checkboxes:
    ticking one in Obsidian cannot complete the task, and the next render
    would silently untick it.
    """
    if not open_tasks:
        return []
    lines = ["## Next steps", ""]
    for task in open_tasks:
        due = f" (due {task['due_date']})" if task.get("due_date") else ""
        lines.append(f"- {task['text']}{due}")
    lines.append("")
    return lines


#: Everything between these markers is regenerated on every write and is
#: stripped from a human edit before it is stored, so links and sources never
#: leak into the canonical body.
GENERATED_START = "<!-- pc:generated - maintained by subzero; edits here are replaced -->"
GENERATED_END = "<!-- /pc:generated -->"

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n?", re.S)
_GENERATED = re.compile(
    re.escape(GENERATED_START.split(" - ")[0]) + r".*?" + re.escape(GENERATED_END), re.S
)


def wikilink(target_stem: str, title: str) -> str:
    """Vault files are named by slug, so the link names the file and shows
    the title."""
    clean = title.replace("]", "").replace("|", "-").strip()
    return f"[[{target_stem}|{clean}]]" if clean and clean != target_stem else f"[[{target_stem}]]"


def render_note(
    note: dict[str, Any],
    *,
    related: list[tuple[str, str, str]] | None = None,
    mocs: list[tuple[str, str]] | None = None,
    next_steps: list[dict[str, Any]] | None = None,
) -> str:
    """``related`` is (file stem, title, relation); ``mocs`` is (stem, title);
    ``next_steps`` is the open task rows of the note's project or area."""
    meta = note.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    tags = ["subzero/note", *[t for t in meta.get("tags") or [] if isinstance(t, str)]]
    if note.get("source_provider"):
        tags.append(f"from/{note['source_provider']}")
    type_tag = {"project": "type/project", "session_index": "type/session-index"}.get(
        note.get("note_class") or ""
    )
    if type_tag and type_tag not in tags:
        tags.append(type_tag)
    front = _frontmatter(
        {
            "sz_type": "note",
            "sz_id": note["id"],
            "title": note["title"],
            "note_class": note.get("note_class"),
            "home": meta.get("home"),
            "project": note.get("project_slug"),
            "source_provider": note.get("source_provider"),
            "source_conversation": note.get("source_conversation_id"),
            "provenance": note.get("provenance"),
            "version": note.get("version"),
            "updated": note.get("updated_at"),
            "source_date": meta.get("source_date"),
            **_triage_fields(meta),
            "tags": tags,
        }
    )
    parts = [front, "", f"# {note['title']}", "", note["content"].strip(), ""]

    generated: list[str] = []
    generated += render_next_steps(next_steps or [])
    if mocs:
        generated += ["## Maps", ""]
        generated += [f"- {wikilink(stem, title)}" for stem, title in mocs]
        generated.append("")
    if related:
        generated += ["## Related", ""]
        generated += [
            f"- {wikilink(stem, title)} · {relation.replace('_', ' ')}"
            for stem, title, relation in related
        ]
        generated.append("")
    citations = note.get("citations") or []
    if citations:
        generated += ["## Sources", ""]
        for citation in citations:
            if isinstance(citation, dict):
                title = citation.get("title") or citation.get("url") or "source"
                url = citation.get("url")
                generated.append(f"- [{title}]({url})" if url else f"- {title}")
            else:
                generated.append(f"- {citation}")
        generated.append("")
    if note.get("source_provider") and note.get("source_conversation_id"):
        when = f" on {meta['source_date']}" if meta.get("source_date") else ""
        generated += [f"*From a {note['source_provider'].title()} conversation{when}.*", ""]
    if generated:
        parts += ["", GENERATED_START, "", *generated, GENERATED_END, ""]
    return "\n".join(parts)


def extract_note_body(text: str, title: str | None = None) -> str:
    """Recover the human-owned body from a rendered note file.

    Strips the frontmatter, the ``# Title`` heading the renderer adds, and the
    generated block. Files rendered before the generated block existed end
    with a bare ``## Sources`` section, which is stripped too.
    """
    body = _FRONTMATTER.sub("", text.replace("\r\n", "\n"), count=1)
    body = _GENERATED.sub("", body)
    lines = body.strip("\n").split("\n")
    if lines and lines[0].startswith("# ") and (title is None or lines[0][2:].strip() == title):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    legacy = body.rfind("\n## Sources\n")
    if legacy != -1 and "\n## " not in body[legacy + 1 :].split("\n", 1)[-1]:
        body = body[:legacy].rstrip()
    return body


def render_moc(
    moc: dict[str, Any],
    sections: dict[str, list[tuple[str, str]]],
    *,
    section_titles: dict[str, str],
    children: list[tuple[str, str]] | None = None,
    parent: tuple[str, str] | None = None,
) -> str:
    """A Map of Content: sections in the order of ``section_titles``, entries
    sorted by title. Entirely generated - a MOC has no human-owned body."""
    front = _frontmatter(
        {
            "sz_type": "moc",
            "sz_id": moc["id"],
            "title": moc["title"],
            "updated": moc.get("updated_at"),
            "tags": ["subzero/moc", "type/moc"],
        }
    )
    parts = [front, "", f"# {moc['title']}", ""]
    if moc.get("description"):
        parts += [str(moc["description"]).strip(), ""]
    if parent:
        parts += [f"Part of {wikilink(*parent)}.", ""]
    for key, heading in section_titles.items():
        entries = sections.get(key) or []
        if not entries:
            continue
        parts += [f"## {heading}", ""]
        parts += [
            f"- {wikilink(stem, title)}"
            for stem, title in sorted(entries, key=lambda e: e[1].lower())
        ]
        parts.append("")
    if children:
        parts += ["## Sub-maps", ""]
        parts += [
            f"- {wikilink(stem, title)}"
            for stem, title in sorted(children, key=lambda e: e[1].lower())
        ]
        parts.append("")
    return "\n".join(parts)


def render_artifact_note(
    artifact: dict[str, Any], files: list[dict[str, Any]], *, download_hint: str
) -> str:
    front = _frontmatter(
        {
            "sz_type": "artifact",
            "sz_id": artifact["id"],
            "title": artifact["title"],
            "artifact_type": artifact.get("artifact_type"),
            "project": artifact.get("project_slug"),
            "source_provider": artifact.get("source_provider"),
            "source_conversation": artifact.get("source_conversation_id"),
            "storage_status": artifact.get("storage_status"),
            "version": artifact.get("version"),
            "created": artifact.get("created_at"),
            "tags": ["subzero/artifact"],
        }
    )
    lines = [front, "", f"# {artifact['title']}", ""]
    if artifact.get("description"):
        lines += [artifact["description"], ""]

    status = artifact.get("storage_status")
    if status != "captured":
        lines += [
            f"> **Bytes not held.** This artifact is recorded with status `{status}`: "
            "its metadata is known but the file contents were never captured. "
            "Import the provider export to fill it in.",
            "",
        ]

    lines += ["## Files", "", "| Path | Type | Size | SHA-256 |", "| --- | --- | ---: | --- |"]
    for f in files:
        size_kb = f"{f['byte_size'] / 1024:.1f} KB" if f["byte_size"] else "-"
        lines.append(
            f"| `{f['relative_path']}` | {f['mime_type']} | {size_kb} | `{f['sha256'][:12]}…` |"
        )
    lines += ["", "## Retrieve", "", download_hint, ""]
    return "\n".join(lines)


def render_project(project: dict[str, Any], snapshot: dict[str, Any]) -> str:
    front = _frontmatter(
        {
            "sz_type": "project",
            "sz_id": project["id"],
            "title": project["name"],
            "status": project.get("status"),
            "updated": project.get("updated_at"),
            "tags": ["subzero/project"],
        }
    )
    lines = [front, "", f"# {project['name']}", ""]
    if project.get("description"):
        lines += [project["description"], ""]
    if project.get("goals"):
        lines += ["## Goals", "", project["goals"], ""]

    decisions = snapshot.get("decisions") or []
    if decisions:
        lines += ["## Decisions", ""]
        lines += [f"- {d['statement']} _({d['decided_at']:%Y-%m-%d})_" for d in decisions]
        lines.append("")

    tasks = snapshot.get("open_tasks") or []
    if tasks:
        lines += ["## Open tasks", ""]
        lines += [
            f"- [ ] {t['text']}" + (f" (due {t['due_date']})" if t.get("due_date") else "")
            for t in tasks
        ]
        lines.append("")

    events = snapshot.get("recent_events") or []
    if events:
        lines += ["## Recent activity", ""]
        lines += [f"- _{e['created_at']:%Y-%m-%d}_ — {e['summary']}" for e in events]
        lines.append("")
    return "\n".join(lines)
