"""Hand-written SQL for ranked search, handoff, and conflict detection."""

from __future__ import annotations

#: Columns every search branch returns, in this order.
#:
#: Weights are explicit (not Postgres's default {0.1, 0.2, 0.4, 1.0}): D is
#: 0.005 so a hub note repeating the query's words thirty times still loses
#: to one exact title cover (a title cover saturates at 1.0 while body
#: covers accumulate ~88xD, so D must sit well under 1/88). Measured.
_RANK_COLS = """
    coalesce(ts_rank_cd('{{0.005, 0.2, 0.4, 1.0}}'::real[], {tsv}, q.r), 0)::float8 AS rank,
    (SELECT count(*) FROM unnest(q.terms) t
      WHERE {tsv} @@ t)::int AS coverage,
    coalesce({tsv} @@ q.ph, false) AS phrase_hit,
    ({id} = ANY($5::uuid[])) AS exact
"""

#: The project filter: tagged to the project, or untagged but naming it.
_IN_PROJECT = """
    ($6::uuid IS NULL
     OR {pid} = $6
     OR ($10::bool AND {pid} IS NULL)
     OR ({pid} IS NULL AND q.pn IS NOT NULL AND {tsv} @@ q.pn))
"""


def _branch(select: str, tsv: str, id_col: str, pid: str) -> str:
    rank = _RANK_COLS.format(tsv=tsv, id=id_col)
    scope = _IN_PROJECT.format(pid=pid, tsv=tsv)
    return select.format(rank=rank, scope=scope, tsv=tsv, id=id_col)


# Built once from constant fragments; caller input only ever arrives as $n.
_SEARCH_SQL = "".join(
    (
        """
WITH q AS (
    SELECT to_tsquery('english', NULLIF($1, '')) AS m,
           to_tsquery('english', NULLIF($2, '')) AS r,
           to_tsquery('english', NULLIF($4, '')) AS ph,
           to_tsquery('english', NULLIF($11, '')) AS pn,
           -- Parsed once per statement instead of once per term per candidate
           -- row: coverage below counts how many of the query's own words a
           -- row matches, and re-parsing them for every row of all seven
           -- branches was the bulk of this statement's cost.
           (SELECT array_agg(to_tsquery('english', t))
              FROM unnest($3::text[]) t) AS terms
)
SELECT u.*, p.slug AS project_slug
FROM (
""",
        _branch(
            """
    (SELECT 'memory'::text AS kind, x.id, NULL::text AS title, x.content AS body,
            x.provenance::text AS provenance, x.project_id, x.updated_at AS effective_at,
            NULL::text AS status, NULL::uuid AS superseded_by,
            x.source_provider AS provider, x.source_conversation_id AS conversation_id,
            x.confidence::float8 AS confidence, NULL::jsonb AS open_questions,
            {rank}
     FROM memories x, q
     WHERE x.status = 'active' AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'decision', x.id, NULL, x.statement || coalesce(E'\\n\\nWhy: ' || x.rationale, ''),
            x.provenance::text, x.project_id, x.decided_at, x.status,
            (SELECT o.id FROM decisions o WHERE o.supersedes_id = x.id
              ORDER BY o.decided_at DESC LIMIT 1),
            x.source_provider, x.source_conversation_id, NULL::float8, NULL::jsonb,
            {rank}
     FROM decisions x, q
     WHERE x.status IN ('active', 'superseded')
       AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'summary', x.id, x.title, x.summary, x.provenance::text,
            coalesce(x.project_id, c.project_id), x.updated_at, NULL, NULL,
            x.provider, x.conversation_id, NULL, x.open_questions,
            {rank}
     FROM session_summaries x JOIN conversations c ON c.id = x.conversation_id, q
     WHERE (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND ($7::text IS NULL OR x.provider = $7)
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "coalesce(x.project_id, c.project_id)",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'note', x.id, x.title, x.content, x.provenance::text, x.project_id, x.updated_at,
            NULL, NULL, x.source_provider, x.source_conversation_id, NULL, NULL,
            {rank}
     FROM notes x, q
     WHERE x.note_class NOT IN ('session_index', 'archive')
       AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'task', x.id, NULL, x.text || coalesce(E'\\n' || x.notes, ''),
            x.provenance::text, x.project_id, x.updated_at, NULL, NULL,
            x.source_provider, x.source_conversation_id, NULL, NULL,
            {rank}
     FROM tasks x, q
     WHERE x.status IN ('open', 'blocked')
       AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'artifact', x.id, x.title, coalesce(x.description, x.title), 'system',
            x.project_id, x.updated_at, NULL, NULL, NULL, NULL, NULL, NULL,
            {rank}
     FROM artifacts x, q
     WHERE (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT $8)
""",
            "x.search_tsv",
            "x.id",
            "x.project_id",
        ),
        "UNION ALL",
        _branch(
            """
    (SELECT 'message', x.id, c.title, x.content, x.provenance::text, c.project_id,
            x.created_at, NULL, NULL, x.provider, x.conversation_id, NULL, NULL,
            {rank}
     FROM messages x JOIN conversations c ON c.id = x.conversation_id, q
     WHERE $9::bool AND (x.search_tsv @@ q.m OR x.id = ANY($5::uuid[]))
       AND ($7::text IS NULL OR x.provider = $7)
       AND {scope}
     ORDER BY exact DESC, rank DESC LIMIT least($8, 8))
""",
            "x.search_tsv",
            "x.id",
            "c.project_id",
        ),
        """
) u
LEFT JOIN projects p ON p.id = u.project_id
""",
    )
)

#: One statement for a project's handoff: newest summaries (tagged, or untagged
#: but naming the project), open tasks, active decisions.
_HANDOFF_SQL = """
WITH pn AS (SELECT to_tsquery('english', NULLIF($2, '')) AS q)
SELECT * FROM (
    (SELECT 'summary'::text AS kind, s.id, s.title, s.summary AS body,
            s.provenance::text AS provenance, s.updated_at AS effective_at,
            s.provider, s.conversation_id, s.open_questions,
            NULL::text AS status, NULL::uuid AS superseded_by,
            (coalesce(s.project_id, c.project_id) IS NULL) AS untagged, 0 AS priority
     FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id, pn
     WHERE coalesce(s.project_id, c.project_id) = $1
        OR (coalesce(s.project_id, c.project_id) IS NULL AND pn.q IS NOT NULL
            AND s.search_tsv @@ pn.q)
     ORDER BY s.updated_at DESC LIMIT $3)
    UNION ALL
    (SELECT 'task', t.id, NULL, t.text || coalesce(E'\\n' || t.notes, ''),
            t.provenance::text, t.updated_at, t.source_provider, t.source_conversation_id,
            NULL, NULL, NULL, false, t.priority
     FROM tasks t
     WHERE t.project_id = $1 AND t.status IN ('open', 'blocked')
     ORDER BY t.priority DESC, t.due_date NULLS LAST, t.created_at LIMIT $4)
    UNION ALL
    (SELECT 'decision', d.id, NULL, d.statement || coalesce(E'\\n\\nWhy: ' || d.rationale, ''),
            d.provenance::text, d.decided_at, d.source_provider, d.source_conversation_id,
            NULL, d.status, NULL, false, 0
     FROM decisions d
     WHERE d.project_id = $1 AND d.status = 'active'
     ORDER BY d.decided_at DESC LIMIT $5)
    UNION ALL
    -- Questions a session of this project marked resolved; hidden from every
    -- summary's open questions below.
    (SELECT 'resolved', s.id, NULL, r.q, NULL, NULL, NULL, NULL, NULL, NULL, NULL, false, 0
     FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id,
          jsonb_array_elements_text(coalesce(s.metadata->'resolved_questions', '[]'::jsonb))
            AS r(q)
     WHERE coalesce(s.project_id, c.project_id) = $1)
) h
"""

#: A lesson or setup home's handoff: its newest session summaries.
_HOME_HANDOFF_SQL = """
SELECT s.id, s.title, s.summary AS body, s.provenance::text AS provenance,
       s.updated_at AS effective_at, s.provider, s.conversation_id, s.open_questions,
       NULL::text AS status, NULL::uuid AS superseded_by, NULL::text AS project_slug
FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id
WHERE c.metadata->>'home' = $1
ORDER BY s.updated_at DESC
LIMIT $2
"""

#: Newer rows in the same project that share several topic words with a
#: returned decision. Project-name words do not count as shared topic, and
#: neither do words found in at least 30% of the decisions and sessions of a
#: project with 20 or more (the user's own name, the product's jargon): those say nothing
#: about whether two items are about the same thing.
_CONFLICT_SQL = """
WITH x AS (
    SELECT id, project_id, decided_at, search_tsv FROM decisions
    WHERE id = ANY($1::uuid[]) AND status = 'active'
), docs AS (
    -- A sample is enough to tell which words are everywhere: the newest 150
    -- decisions and 150 sessions keep this cheap on a large project.
    (SELECT o.project_id, o.search_tsv FROM decisions o
     WHERE o.status = 'active' AND o.project_id IN (SELECT project_id FROM x)
     ORDER BY o.decided_at DESC LIMIT 150)
    UNION ALL
    (SELECT s.project_id, s.search_tsv FROM session_summaries s
     WHERE s.project_id IN (SELECT project_id FROM x)
     ORDER BY s.updated_at DESC LIMIT 150)
), sizes AS (
    SELECT project_id, count(*) AS n FROM docs GROUP BY project_id
), common AS (
    SELECT docs.project_id, l
    FROM docs JOIN sizes USING (project_id), unnest(tsvector_to_array(docs.search_tsv)) l
    WHERE sizes.n >= 20
    GROUP BY docs.project_id, sizes.n, l
    HAVING count(*) >= 0.3 * sizes.n
), d AS (
    SELECT x.id, x.project_id, x.decided_at,
           ARRAY(SELECT l FROM unnest(tsvector_to_array(x.search_tsv)) l
                 WHERE l ~ '^[a-z0-9]+$'
                   AND l <> ALL(tsvector_to_array(to_tsvector('english',
                         coalesce(p.name, '') || ' ' || replace(coalesce(p.slug, ''), '-', ' '))))
                   AND l NOT IN (SELECT common.l FROM common
                                 WHERE common.project_id = x.project_id))
             AS lex
    FROM x LEFT JOIN projects p ON p.id = x.project_id
)
SELECT d.id AS older_id, d.lex AS older_lex, cand.*
FROM d CROSS JOIN LATERAL (
    SELECT * FROM (
        SELECT 'decision'::text AS kind, o.id, o.statement AS body,
               o.provenance::text AS provenance, o.decided_at AS effective_at,
               tsvector_to_array(o.search_tsv) AS lex
        FROM decisions o
        WHERE o.status = 'active' AND o.id <> d.id AND o.decided_at > d.decided_at
          AND o.project_id IS NOT DISTINCT FROM d.project_id
          AND o.search_tsv @@ to_tsquery('simple', NULLIF(array_to_string(d.lex, ' | '), ''))
        UNION ALL
        SELECT 'summary', s.id, coalesce(s.title || ': ', '') || s.summary, s.provenance::text,
               s.updated_at, tsvector_to_array(s.search_tsv)
        FROM session_summaries s JOIN conversations c ON c.id = s.conversation_id
        WHERE s.updated_at > d.decided_at
          AND coalesce(s.project_id, c.project_id) IS NOT DISTINCT FROM d.project_id
          AND s.search_tsv @@ to_tsquery('simple', NULLIF(array_to_string(d.lex, ' | '), ''))
        UNION ALL
        SELECT 'memory', m.id, m.content, m.provenance::text, m.updated_at,
               tsvector_to_array(m.search_tsv)
        FROM memories m
        WHERE m.status = 'active' AND m.updated_at > d.decided_at
          AND m.provenance <> 'imported_metadata'
          AND m.project_id IS NOT DISTINCT FROM d.project_id
          AND m.search_tsv @@ to_tsquery('simple', NULLIF(array_to_string(d.lex, ' | '), ''))
    ) c
    ORDER BY cardinality(ARRAY(SELECT unnest(d.lex) INTERSECT SELECT unnest(c.lex))) DESC,
             (c.provenance = 'user_statement') DESC, (c.kind = 'decision') DESC,
             c.effective_at DESC
    LIMIT 2
) cand
"""

# Public aliases for RetrievalService
SEARCH_SQL = _SEARCH_SQL
HANDOFF_SQL = _HANDOFF_SQL
HOME_HANDOFF_SQL = _HOME_HANDOFF_SQL
CONFLICT_SQL = _CONFLICT_SQL
