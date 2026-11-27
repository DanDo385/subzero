"""Heading-aware chunking: sections split at headings, short ones grouped."""

from __future__ import annotations

from subzero.notes.chunks import chunk_note, embed_text


def test_note_without_headings_is_one_chunk() -> None:
    body = "Just a paragraph.\n\nAnother paragraph."
    (chunk,) = chunk_note("Title", body)
    assert chunk.heading_path == ""
    assert chunk.text == body
    assert embed_text("Title", chunk).startswith("Title\n\n")


def test_headings_become_paths() -> None:
    body = (
        "# Plan\n\n"
        + ("x " * 150)
        + "\n\n## Risks\n\n"
        + ("y " * 150)
        + "\n\n## Budget\n\n"
        + ("z " * 150)
        + "\n"
    )
    chunks = chunk_note("T", body)
    assert [c.heading_path for c in chunks] == ["Plan", "Plan > Risks", "Plan > Budget"]
    assert chunks[1].text.startswith("## Risks")
    assert embed_text("T", chunks[1]).startswith("T > Plan > Risks\n\n")


def test_short_sections_group_with_a_neighbour() -> None:
    body = (
        "# Plan\n\n" + ("x " * 150) + "\n\n## Tiny\n\nA line.\n\n## Big\n\n" + ("y " * 150) + "\n"
    )
    chunks = chunk_note("T", body)
    assert len(chunks) == 2
    assert all(len(c.text) >= 200 or len(chunks) == 1 for c in chunks)
    assert "Tiny" in chunks[1].text


def test_trailing_short_section_folds_backward() -> None:
    body = "# Plan\n\n" + ("x " * 150) + "\n\n## Tail\n\nShort.\n"
    (chunk,) = chunk_note("T", body)
    assert "Tail" in chunk.text


def test_headings_in_fences_do_not_split() -> None:
    body = "# Real\n\n" + ("x " * 150) + "\n\n```\n# Not a heading\n```\n"
    chunks = chunk_note("T", body)
    assert [c.heading_path for c in chunks] == ["Real"]
    assert "# Not a heading" in chunks[0].text


def test_empty_content_has_no_chunks() -> None:
    assert chunk_note("T", "   \n  ") == []


def test_chunk_text_holds_heading_and_body() -> None:
    body = (
        "Intro line here, long enough to survive grouping on its own. " * 6
        + "\n\n# S\n\n"
        + ("b " * 120)
    )
    chunks = chunk_note("T", body)
    assert chunks[0].heading_path == ""
    assert "Intro line" in chunks[0].text
