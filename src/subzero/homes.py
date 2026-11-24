"""One logical home, the other homes typed with it, and concept tags."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import Field

from subzero.errors import ValidationError
from subzero.util import slugify

HOME_PATTERN = r"^(other|(projects|lesson|setup)/[a-z0-9][a-z0-9-]{0,79})$"
Home = Annotated[str, Field(pattern=HOME_PATTERN)]


def home_metadata(
    home: str | None,
    project_slug: str | None,
    concepts: list[str] | None = None,
    people: list[str] | None = None,
    extra_homes: list[str] | None = None,
) -> dict:
    """Validate caller-selected semantics; code never guesses a lesson or tool.

    ``extra_homes`` are the other places a sigil pointed at: a session can be
    about a project and a lesson and a setup at once, even though it is filed
    under one of them. They become tags, validated like the home itself.
    """
    chosen = home or (f"projects/{project_slug}" if project_slug else "other")
    if not re.fullmatch(HOME_PATTERN, chosen):
        raise ValidationError(
            "Home must be projects/{slug}, lesson/{category}, setup/{tool}, or other."
        )
    if chosen.startswith("projects/") and chosen != f"projects/{project_slug}":
        raise ValidationError(
            "A project home must match an existing project. Use project_search first."
        )
    tags = [extra for extra in (extra_homes or []) if extra != chosen]
    for extra in tags:
        if not re.fullmatch(HOME_PATTERN, extra):
            raise ValidationError(
                "Extra homes must be projects/{slug}, lesson/{category}, setup/{tool}, or other."
            )
    for concept in concepts or []:
        name = slugify(concept.removeprefix("concept/"))
        if not name or len(name) > 80:
            raise ValidationError(
                "Concept names must contain letters or numbers and fit in 80 characters."
            )
        tags.append(f"concept/{name}")
    for handle in people or []:
        name = handle.removeprefix("person/").removeprefix("@").lower()
        if re.fullmatch(r"[a-z0-9_.-]{1,64}", name):
            tags.append(f"person/{name}")
    return {"home": chosen, "tags": [chosen, *sorted(set(tags))]}
