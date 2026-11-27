"""Heading-aware chunks: split a note at its headings for meaning search.

A long note embedded as one vector matches weakly and forces whoever
matched it to read the whole note. Chunks split it at ATX headings (outside
fenced code blocks), so each section is embedded under a
``Title > Heading > Subheading`` prefix and a hit can name the section it
matched. Sections shorter than ``MIN_CHARS`` are grouped with a neighbour,
so a preview can stand alone.

Chunk rows are derived data: ``notes.upsert`` and ``apply_vault_edit``
rebuild them whenever the body changes, and the background embedding loop
fills their vectors. Whole-note vectors stay for keyword-adjacent paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Sections shorter than this are grouped with a neighbour: too short to
#: stand alone as a preview.
MIN_CHARS = 200

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


@dataclass(frozen=True)
class Chunk:
    """One embeddable section: path from the top, order, and text."""

    heading_path: str  # "" for the top of the note, else "A > B"
    index: int
    text: str  # heading line plus its body


def embed_text(note_title: str, chunk: Chunk) -> str:
    """The text actually embedded: section under its heading path."""
    title = note_title.strip()
    if chunk.heading_path:
        return f"{title} > {chunk.heading_path}\n\n{chunk.text}"
    return f"{title}\n\n{chunk.text}" if title else chunk.text


def chunk_note(note_title: str, content: str) -> list[Chunk]:
    """Split note content at its headings. Never returns an empty list for
    non-empty content; a note with no headings is one chunk."""
    lines = (content or "").split("\n")
    # (level, heading, body lines). Level 0 is the top of the note.
    sections: list[tuple[int, str, list[str]]] = [(0, "", [])]
    stack: list[tuple[int, str]] = []
    in_fence: str | None = None
    for line in lines:
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0] * 3
            in_fence = None if in_fence == marker else marker
            sections[-1][2].append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            sections[-1][2].append(line)
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        path = " > ".join(h for _, h in stack)
        sections.append((level, path, [line]))
    # Drop the empty top section a note that starts with a heading leaves.
    if len(sections) > 1 and not "".join(sections[0][2]).strip():
        sections.pop(0)
    chunks = [
        Chunk(heading_path=path, index=n, text="\n".join(body).strip())
        for n, (_, path, body) in enumerate(sections)
        if "\n".join(body).strip()
    ]
    return _group_short(chunks)


def _group_short(chunks: list[Chunk]) -> list[Chunk]:
    """Fold sections under MIN_CHARS forward into the next sibling (or, for
    a trailing short chunk, backward into the previous one), so every chunk
    can stand alone as a preview."""
    if len(chunks) < 2:
        return chunks
    merged: list[Chunk] = []
    pending: Chunk | None = None
    for chunk in chunks:
        if pending is not None:
            chunk = Chunk(
                heading_path=chunk.heading_path,
                index=chunk.index,
                text=f"{pending.text}\n\n{chunk.text}",
            )
            pending = None
        if len(chunk.text) < MIN_CHARS:
            pending = chunk
        else:
            merged.append(chunk)
    if pending is not None:
        if merged:
            previous = merged.pop()
            merged.append(
                Chunk(
                    heading_path=previous.heading_path,
                    index=previous.index,
                    text=f"{previous.text}\n\n{pending.text}",
                )
            )
        else:
            merged.append(pending)
    return [Chunk(heading_path=c.heading_path, index=n, text=c.text) for n, c in enumerate(merged)]
