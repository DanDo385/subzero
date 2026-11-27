"""Notes: the human-readable knowledge layer.

Notes live in Postgres and are *rendered* into the Obsidian vault by the sync
worker. Postgres holds the version history; the vault holds the copy a person
edits. When both change, the worker reports a conflict rather than picking a
winner.
"""

from __future__ import annotations

import difflib
import json
from typing import Any, get_args
from uuid import UUID

import asyncpg

from subzero.db.pool import Database, vector_literal
from subzero.embeddings.service import EmbeddingService
from subzero.errors import ConflictError, NotFoundError, ValidationError
from subzero.homes import home_metadata
from subzero.models import NoteClass, NoteRecord
from subzero.notes.chunks import Chunk, chunk_note
from subzero.obsidian.render import extract_note_body
from subzero.util import sha256_text, slugify

_NOTE_CLASSES = frozenset(get_args(NoteClass))


def _unified_diff(before: str, after: str, *, context: int = 3, limit: int = 60) -> str:
    """A short unified diff for a patch preview. Capped so a large rewrite
    cannot flood the caller's context."""
    lines = list(
        difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=context)
    )
    if len(lines) > limit:
        lines = [*lines[:limit], f"... ({len(lines) - limit} more lines)"]
    return "\n".join(lines)


def _record(row: asyncpg.Record, *, include_content: bool = False) -> NoteRecord:
    return NoteRecord(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        # A stored value outside the vocabulary must not fail a whole search
        # result; it reads as inbox, where an unsorted note belongs anyway.
        note_class=row["note_class"] if row["note_class"] in _NOTE_CLASSES else "inbox",
        project=row.get("project_slug"),
        vault_path=row["vault_path"],
        version=row["version"],
        updated_at=row["updated_at"],
        content=row["content"] if include_content else None,
        home=(row.get("metadata") or {}).get("home"),
        tags=(row.get("metadata") or {}).get("tags", []),
    )


class NoteService:
    def __init__(self, db: Database, embeddings: EmbeddingService) -> None:
        self._db = db
        self._embeddings = embeddings

    async def resolve(self, reference: str) -> UUID | None:
        try:
            note_uuid: UUID | None = UUID(reference)
        except (ValueError, AttributeError):
            note_uuid = None
        row = await self._db.fetchrow(
            """
            SELECT id FROM notes
            WHERE ($1::uuid IS NOT NULL AND id = $1::uuid)
               OR slug = $2 OR lower(title) = lower($3)
            ORDER BY ($1::uuid IS NOT NULL AND id = $1::uuid) DESC
            LIMIT 1
            """,
            note_uuid,
            slugify(reference),
            reference,
        )
        return row["id"] if row else None

    async def upsert(
        self,
        *,
        title: str,
        content: str,
        note_class: str = "inbox",
        project_id: UUID | None = None,
        vault_path: str | None = None,
        sensitivity: str = "normal",
        citations: list[dict[str, Any]] | None = None,
        source_provider: str | None = None,
        source_conversation_id: UUID | None = None,
        provenance: str = "assistant_generated",
        author: str | None = None,
        append: bool = False,
        home: str | None = None,
        concepts: list[str] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool, int]:
        """Create or update a note. Returns (id, created, version).

        ``append=True`` adds to the existing body instead of replacing it,
        which is what research-to-obsidian wants when a topic is revisited.
        """
        if conn is None:
            async with self._db.transaction() as tx:
                return await self.upsert(
                    title=title,
                    content=content,
                    note_class=note_class,
                    project_id=project_id,
                    vault_path=vault_path,
                    sensitivity=sensitivity,
                    citations=citations,
                    source_provider=source_provider,
                    source_conversation_id=source_conversation_id,
                    provenance=provenance,
                    author=author,
                    append=append,
                    home=home,
                    concepts=concepts,
                    conn=tx,
                )
        executor = conn
        slug = slugify(title, fallback="note")
        # One writer per slug until commit: concurrent extractions that land on
        # the same title would otherwise both see no row and both insert.
        await executor.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"notes:{slug}")
        existing = await executor.fetchrow(
            "SELECT id, content, version, metadata, project_id, sensitivity FROM notes WHERE slug = $1",
            slug,
        )

        project_slug = await executor.fetchval(
            "SELECT slug FROM projects WHERE id = $1",
            project_id or (existing["project_id"] if existing else None),
        )
        previous = (existing["metadata"] or {}) if existing else {}
        if existing:
            levels = {"normal": 0, "private": 1, "highly_sensitive": 2}
            sensitivity = max((sensitivity, existing["sensitivity"]), key=levels.__getitem__)
        metadata = home_metadata(
            home or previous.get("home"),
            project_slug,
            concepts
            if concepts is not None
            else [
                t.removeprefix("concept/")
                for t in previous.get("tags", [])
                if t.startswith("concept/")
            ],
        )

        metadata["tags"] = list(
            dict.fromkeys(
                [
                    t
                    for t in previous.get("tags", [])
                    if t != previous.get("home") and not t.startswith("concept/")
                ]
                + metadata["tags"]
            )
        )

        if append:
            incoming_hash = sha256_text(content.strip())
            appended = previous.get("append_hashes", [])
            if existing and incoming_hash in appended:
                content = existing["content"]
            elif existing and existing["content"] not in content:
                content = f"{existing['content'].rstrip()}\n\n---\n\n{content.strip()}"
            metadata["append_hashes"] = sorted({*appended, incoming_hash})

        content = content.strip()
        content_hash = sha256_text(content)
        # No embedding here on purpose. A note's vector is derived data, and
        # computing it inline meant a network call of up to 30 s inside the
        # caller's transaction, holding row locks and a pooled connection while
        # a provider was slow. The refresh loop fills notes.embedding instead,
        # and a note the privacy policy forbids is marked embedding_skipped so
        # the loop never rescans it.

        if existing is None:
            note_id = await executor.fetchval(
                """
                INSERT INTO notes (
                    slug, title, content, content_sha256, note_class, project_id,
                    vault_path, sensitivity, citations, source_provider,
                    source_conversation_id, provenance, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::sz_sensitivity,$9,$10,$11,
                          $12::sz_provenance,$13)
                RETURNING id
                """,
                slug,
                title.strip(),
                content,
                content_hash,
                note_class,
                project_id,
                vault_path,
                sensitivity,
                citations or [],
                source_provider,
                source_conversation_id,
                provenance,
                metadata,
            )
            await self._add_version(
                executor,
                note_id,
                1,
                title,
                content,
                content_hash,
                author or source_provider or "system",
            )
            await self._rebuild_chunks(executor, note_id, title, content)
            return note_id, True, 1

        await executor.execute(
            "UPDATE notes SET metadata = metadata || $2::jsonb, sensitivity = $3::sz_sensitivity WHERE id = $1",
            existing["id"],
            metadata,
            sensitivity,
        )
        if existing["content"] == content:
            return existing["id"], False, existing["version"]

        version = existing["version"] + 1
        await executor.execute(
            """
            UPDATE notes
            SET title = $2, content = $3, content_sha256 = $4,
                note_class = coalesce($5, note_class),
                project_id = coalesce($6, project_id),
                vault_path = coalesce($7, vault_path),
                citations = CASE WHEN $8::jsonb = '[]'::jsonb THEN citations ELSE $8 END,
                version = $9,
                -- The body changed, so the stored vector describes text that is
                -- no longer there. Clearing it beats keeping a stale one: the
                -- refresh loop recomputes it from the new content.
                embedding = NULL
            WHERE id = $1
            """,
            existing["id"],
            title.strip(),
            content,
            content_hash,
            note_class,
            project_id,
            vault_path,
            citations or [],
            version,
        )
        await self._add_version(
            executor,
            existing["id"],
            version,
            title,
            content,
            content_hash,
            author or source_provider or "system",
        )
        await self._rebuild_chunks(executor, existing["id"], title, content)
        return existing["id"], False, version

    @staticmethod
    async def _rebuild_chunks(executor: Any, note_id: UUID, title: str, content: str) -> None:
        """Chunk rows follow the body: rebuilt on every content change.

        Delete + insert keeps no stale rows; chunk vectors start NULL for
        the background refresh loop. Chunking is pure Python, so this adds
        no network call inside the caller's transaction.
        """
        await executor.execute("DELETE FROM note_chunks WHERE note_id = $1", note_id)
        chunks = chunk_note(title, content)
        if chunks:
            await executor.executemany(
                """
                INSERT INTO note_chunks (note_id, heading_path, chunk_index, content, content_sha256)
                VALUES ($1,$2,$3,$4,$5)
                """,
                [
                    (note_id, chunk.heading_path, chunk.index, chunk.text, sha256_text(chunk.text))
                    for chunk in chunks
                ],
            )

    @staticmethod
    async def _add_version(
        executor: Any,
        note_id: UUID,
        version: int,
        title: str,
        content: str,
        content_hash: str,
        author: str,
    ) -> None:
        await executor.execute(
            """
            INSERT INTO note_versions (note_id, version, title, content, content_sha256, author)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (note_id, version) DO NOTHING
            """,
            note_id,
            version,
            title,
            content,
            content_hash,
            author,
        )

    async def get(self, reference: str, *, include_content: bool = True) -> NoteRecord:
        note_id = await self.resolve(reference)
        if note_id is None:
            raise NotFoundError(f"No note matches {reference!r}.")
        row = await self._db.fetchrow(
            """
            SELECT n.*, p.slug AS project_slug
            FROM notes n LEFT JOIN projects p ON p.id = n.project_id WHERE n.id = $1
            """,
            note_id,
        )
        assert row is not None
        return _record(row, include_content=include_content)

    async def merge_metadata(self, note_id: UUID, patch: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE notes SET metadata = metadata || $2::jsonb WHERE id = $1",
            note_id,
            patch,
        )

    async def stale_markers(self, *, days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
        """Notes whose dated claims may have gone stale.

        Finds ``(as of YYYY-MM-DD)`` markers older than ``days`` and returns
        the oldest marker per note. Compared as text: ISO dates order
        correctly, and a malformed stamp can never crash the cast because
        there is none. Use the result to file review items, never to edit:
        only a human (or a model with the note open) decides whether a fact
        is stale or timeless.
        """
        rows = await self._db.fetch(
            """
            SELECT n.id, n.title, min(m[1]) AS oldest
            FROM notes n,
                 regexp_matches(n.content, '\\(as of ([0-9]{4}-[0-9]{2}-[0-9]{2})', 'g') AS m
            GROUP BY n.id, n.title
            HAVING min(m[1]) < to_char(now() - make_interval(days => $1), 'YYYY-MM-DD')
            ORDER BY min(m[1])
            LIMIT $2
            """,
            days,
            limit,
        )
        return [dict(r) for r in rows]

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: UUID | None = None,
        note_class: str | None = None,
    ) -> list[NoteRecord]:
        vector = await self._embeddings.embed_one(query) if self._embeddings.enabled else None
        rows = await self._db.fetch(
            """
            WITH text_hits AS (
                SELECT id, ts_rank_cd(search_tsv, websearch_to_tsquery('english', $1)) AS rank
                FROM notes WHERE search_tsv @@ websearch_to_tsquery('english', $1)
            ),
            vector_hits AS (
                SELECT id, 1 - (embedding <=> $4::text::vector) AS rank FROM notes
                WHERE $4::text IS NOT NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> $4::text::vector LIMIT 30
            ),
            merged AS (
                SELECT id, max(rank) AS rank
                FROM (SELECT * FROM text_hits UNION ALL SELECT * FROM vector_hits) u
                GROUP BY id
            )
            SELECT n.*, p.slug AS project_slug, merged.rank
            FROM merged JOIN notes n ON n.id = merged.id
            LEFT JOIN projects p ON p.id = n.project_id
            WHERE ($2::uuid IS NULL OR n.project_id = $2)
              AND ($3::text IS NULL OR n.note_class = $3)
            ORDER BY merged.rank DESC, n.updated_at DESC
            LIMIT $5
            """,
            query,
            project_id,
            note_class,
            vector_literal(vector),
            limit,
        )
        return [_record(r) for r in rows]

    async def similar_titles(self, title: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Existing notes a new one might duplicate - fed to the classifier.

        Matches on shared significant words rather than trigram similarity, so
        this works without pg_trgm. The result only has to be good enough to
        show the classifier "here is what already exists with a similar name".
        """
        words = [w for w in title.lower().split() if len(w) > 3][:6]
        if not words:
            return []
        rows = await self._db.fetch(
            """
            SELECT slug, title, note_class,
                   (SELECT count(*) FROM unnest($1::text[]) w
                     WHERE lower(n.title) LIKE '%' || w || '%') AS overlap
            FROM notes n
            WHERE EXISTS (
                SELECT 1 FROM unnest($1::text[]) w
                WHERE lower(n.title) LIKE '%' || w || '%'
            )
            ORDER BY overlap DESC, n.updated_at DESC
            LIMIT $2
            """,
            words,
            limit,
        )
        return [dict(r) for r in rows]

    async def mark_synced(self, note_id: UUID, *, sha256: str) -> None:
        await self._db.execute(
            "UPDATE notes SET vault_synced_sha256 = $2, vault_synced_at = now() WHERE id = $1",
            note_id,
            sha256,
        )

    async def apply_vault_edit(
        self,
        *,
        vault_path: str,
        content: str,
        author: str = "human",
        file_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Accept a human's edit from the vault as a new canonical version.

        ``content`` is the whole file. Only the human-owned body is stored:
        the frontmatter, the title heading and the generated block (links,
        maps, sources) are the renderer's and are stripped, or every edit
        would nest another copy of them into the note.
        """
        row = await self._db.fetchrow(
            "SELECT id, title, version, content_sha256 FROM notes WHERE vault_path = $1",
            vault_path,
        )
        if row is None:
            raise NotFoundError(f"No note is tracked at {vault_path!r}.")

        body = extract_note_body(content, row["title"])
        content_hash = sha256_text(body)
        if content_hash == row["content_sha256"]:
            if file_sha256:
                await self.mark_synced(row["id"], sha256=file_sha256)
            return {"note_id": str(row["id"]), "changed": False, "version": row["version"]}

        version = row["version"] + 1
        await self._db.execute(
            """
            UPDATE notes
            SET content = $2, content_sha256 = $3, version = $4,
                vault_synced_sha256 = coalesce($5, vault_synced_sha256), vault_synced_at = now(),
                provenance = 'user_statement'
            WHERE id = $1
            """,
            row["id"],
            body,
            content_hash,
            version,
            file_sha256,
        )
        await self._rebuild_chunks(self._db, row["id"], row["title"], body)
        await self._add_version(
            self._db, row["id"], version, row["title"], body, content_hash, author
        )
        return {"note_id": str(row["id"]), "changed": True, "version": version}

    async def patch_note(
        self,
        reference: str,
        *,
        section: str | None = None,
        key: str | None = None,
        operation: str,
        text: str = "",
        expected_version: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Edit one section of a note (or one metadata key) without rewriting it.

        ``section`` is a chunk heading path ("Plan > Risks") from the same
        chunking the search index uses; ``key`` is a metadata key. Exactly one
        of the two is required. ``operation`` is replace, prepend, append or
        delete (delete ignores ``text``). ``expected_version`` is the version
        the caller read: a mismatch refuses instead of overwriting, so a
        human edit in between comes back as an error naming both versions.
        ``dry_run`` validates everything and returns the diff without writing.

        Runs in one transaction: the guard, the write and the chunk rebuild
        cannot interleave with another writer.
        """
        if bool(section) == bool(key):
            raise ValidationError("Pass exactly one of section (a heading path) or key.")
        if operation not in ("replace", "prepend", "append", "delete"):
            raise ValidationError(
                f"Unknown operation {operation!r}: replace, prepend, append or delete."
            )
        note_id = await self.resolve(reference)
        if note_id is None:
            raise NotFoundError(f"No note matches {reference!r}.")

        async with self._db.transaction() as tx:
            row = await tx.fetchrow(
                "SELECT id, title, content, version, metadata, vault_path FROM notes WHERE id = $1",
                note_id,
            )
            assert row is not None  # resolve() just proved it exists
            if expected_version is not None and row["version"] != expected_version:
                raise ConflictError(
                    f"Note is at version {row['version']}, not {expected_version}: "
                    "it changed since you read it. Re-read it and retry against the new version."
                )
            metadata = dict(row["metadata"] or {})
            if key is not None:
                new_metadata, changed = self._patch_key(metadata, key, operation, text)
                new_content: str | None = None
            else:
                assert section is not None
                new_content = self._patch_section(
                    row["title"], row["content"], section, operation, text
                )
                new_metadata = metadata
                changed = new_content != row["content"]
            if not changed:
                return {
                    "note_id": note_id,
                    "title": row["title"],
                    "version": row["version"],
                    "vault_path": row["vault_path"],
                    "applied": False,
                    "diff": "",
                }
            diff = _unified_diff(row["content"], new_content or row["content"])
            if dry_run:
                return {
                    "note_id": note_id,
                    "title": row["title"],
                    "version": row["version"],
                    "vault_path": row["vault_path"],
                    "applied": False,
                    "diff": diff,
                }
            version = row["version"]
            if new_content is not None:
                version += 1
                await tx.execute(
                    """
                    UPDATE notes
                    SET content = $2, content_sha256 = $3, version = $4,
                        metadata = $5::jsonb, embedding = NULL
                    WHERE id = $1
                    """,
                    note_id,
                    new_content,
                    sha256_text(new_content),
                    version,
                    new_metadata,
                )
                await self._rebuild_chunks(tx, note_id, row["title"], new_content)
                await self._add_version(
                    tx,
                    note_id,
                    version,
                    row["title"],
                    new_content,
                    sha256_text(new_content),
                    "note_patch",
                )
            else:
                await tx.execute(
                    "UPDATE notes SET metadata = $2::jsonb WHERE id = $1",
                    note_id,
                    new_metadata,
                )
            return {
                "note_id": note_id,
                "title": row["title"],
                "version": version,
                "vault_path": row["vault_path"],
                "applied": True,
                "diff": diff,
            }

    @staticmethod
    def _patch_key(
        metadata: dict[str, Any], key: str, operation: str, text: str
    ) -> tuple[dict[str, Any], bool]:
        if operation in ("prepend", "append"):
            raise ValidationError(
                f"Operation {operation!r} needs a section; a metadata key only supports replace and delete."
            )
        if operation == "delete":
            if key not in metadata:
                raise ValidationError(f"No metadata key {key!r} to delete.")
            updated = {k: v for k, v in metadata.items() if k != key}
            return updated, True
        updated = dict(metadata)
        try:
            value: Any = json.loads(text) if text.strip() else None
        except ValueError:
            value = text
        if updated.get(key) == value:
            return updated, False
        updated[key] = value
        return updated, True

    @staticmethod
    def _patch_section(title: str, content: str, section: str, operation: str, text: str) -> str:
        chunks = chunk_note(title, content)
        matches = [c for c in chunks if c.heading_path == section]
        if not matches:
            available = ", ".join(repr(c.heading_path or "(top)") for c in chunks)
            raise NotFoundError(f"No section {section!r} in this note. Sections: {available}.")
        if len(matches) > 1:
            raise ValidationError(
                f"Section {section!r} occurs {len(matches)} times; be more specific."
            )
        target = matches[0]
        if target.heading_path == "":
            raise ValidationError(
                "The top of the note has no heading path; rewrite it with notes.upsert instead."
            )
        heading, _, body = target.text.partition("\n")
        body = body.strip()
        if operation == "delete":
            rebuilt = [c for c in chunks if c is not target]
        else:
            if not text.strip():
                raise ValidationError(f"Operation {operation!r} needs text to write.")
            if operation == "replace":
                new_body = text.strip()
            elif operation == "prepend":
                new_body = f"{text.strip()}\n\n{body}" if body else text.strip()
            else:  # append
                new_body = f"{body}\n\n{text.strip()}" if body else text.strip()
            new_text = f"{heading}\n\n{new_body}" if new_body else heading
            rebuilt = [
                c if c is not target else Chunk(c.heading_path, c.index, new_text) for c in chunks
            ]
        out = "\n\n".join(c.text for c in rebuilt).strip()
        if not out:
            raise ValidationError("The patch would empty the note; refusing.")
        return out
