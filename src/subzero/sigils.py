"""Sigils: one typed symbol that says where something belongs.

    %Code/portfolio-site   project   home projects/portfolio-site
    ^cfa2/fixed-income     lesson    home lesson/cfa2, concept fixed-income
    &tailscale             setup     home setup/tailscale
    #comptime              concept   tag concept/comptime
    @handle                person    tag person/handle
    >claude                target    which agent a dispatched job goes to

The first ``%``, ``^`` or ``&`` in the text is the home; ``#`` and ``@`` are
extra tags, as many as are typed. Parsing is deterministic code, never a model:
this is the "rules answer what code can answer" half of classification, and a
sigil always beats Jev. A sigil is only recognised at the start of a word, so
``C#``, ``x^2``, ``50%``, ``a@b.com`` and ``https://x.dev/#top`` are left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from subzero.util import slugify

#: The symbol must open a word (start of text, or after whitespace or an
#: opening bracket/quote) and be followed directly by a letter or digit.
_SIGIL = re.compile(r"(?:^|(?<=[\s(\[{\"'`]))([%^&#@>])([A-Za-z0-9][A-Za-z0-9_./-]*)", re.MULTILINE)
_TRAILING = "./-_"

HOME_KINDS = {"%": "projects", "^": "lesson", "&": "setup"}


@dataclass(frozen=True)
class Sigils:
    """What the sigils in one piece of text asked for."""

    home: str | None = None
    project: str | None = None
    lesson: str | None = None
    setup: str | None = None
    concepts: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    #: The text with each sigil replaced by its plain words, for search.
    plain: str = ""
    found: tuple[str, ...] = field(default=())

    @property
    def any(self) -> bool:
        return bool(self.found)

    def tags(self) -> list[str]:
        out = [self.home] if self.home else []
        out += [f"concept/{c}" for c in self.concepts]
        out += [f"person/{p}" for p in self.people]
        return out


def _clean(word: str) -> str:
    return word.rstrip(_TRAILING)


def parse(text: str | None) -> Sigils:
    """Read every sigil in ``text``. Never raises; unknown shapes are ignored."""
    if not text:
        return Sigils(plain=text or "")
    home: str | None = None
    project = lesson = setup = None
    concepts: list[str] = []
    people: list[str] = []
    targets: list[str] = []
    found: list[str] = []

    def plain_words(match: re.Match[str]) -> str:
        return _clean(match.group(2)).replace("/", " ").replace("-", " ").replace("_", " ")

    for match in _SIGIL.finditer(text):
        symbol, raw = match.group(1), _clean(match.group(2))
        parts = [p for p in raw.split("/") if p]
        if not parts:
            continue
        found.append(f"{symbol}{raw}")
        if symbol == "%":
            slug = slugify(parts[-1])
            if slug and project is None:
                project = slug
                home = home or f"projects/{slug}"
        elif symbol == "^":
            slug = slugify(parts[0])
            if slug and lesson is None:
                lesson = slug
                home = home or f"lesson/{slug}"
            # ^cfa2/fixed-income: the topic is a concept within the lesson.
            for topic in parts[1:]:
                if (name := slugify(topic)) and name not in concepts:
                    concepts.append(name)
        elif symbol == "&":
            slug = slugify(parts[0])
            if slug and setup is None:
                setup = slug
                home = home or f"setup/{slug}"
        elif symbol == "#":
            name = slugify(raw.replace("/", "-"))
            if name and name not in concepts:
                concepts.append(name)
        elif symbol == "@":
            handle = raw.lower()
            if re.fullmatch(r"[a-z0-9_.-]{1,64}", handle) and handle not in people:
                people.append(handle)
        elif symbol == ">":
            name = slugify(parts[0])
            if name and name not in targets:
                targets.append(name)

    return Sigils(
        home=home,
        project=project,
        lesson=lesson,
        setup=setup,
        concepts=tuple(c[:80] for c in concepts),
        people=tuple(people),
        targets=tuple(targets),
        plain=_SIGIL.sub(plain_words, text),
        found=tuple(found),
    )


def parse_all(*texts: str | None) -> Sigils:
    """Sigils across several fields, read in order (title before summary)."""
    return parse("\n".join(t for t in texts if t))
