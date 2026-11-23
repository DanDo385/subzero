# Subzero audited against *Designing Data-Intensive Applications*

An audit of the system as it actually runs, not as the documentation describes it. Every finding
below is grounded in two kinds of evidence:

1. **Code**, cited as `path:line` against the base commit `341e6d2`, which is the state of the code
   before anything in the last section was changed. Line numbers in the findings are that commit's.
2. **Production measurements**, taken read-only on 2026-11-22 from the canonical Supabase instance
   (`pg_stat_statements`, `pg_stat_user_tables`, `pg_stat_user_indexes`, `EXPLAIN (ANALYZE, BUFFERS)`).
   No value was written and no secret was printed.

Findings are ordered inside each chapter by severity. The summary table lists everything that is
high severity, because those are the ones that lose data, hang a request, or cost real money.

## Shape of the system, as measured

| Fact | Value | Source |
| --- | --- | --- |
| Database | PostgreSQL 17.6, pgvector 0.8.2, pg_trgm 1.6 | `SELECT version()` |
| Isolation in effect | `read committed` (asyncpg and Postgres default, never escalated) | `current_setting('transaction_isolation')` |
| Pooler | PgBouncer in transaction mode (`pgbouncer.get_auth` called 309,660 times, `RESET ALL` 404,980) | `pg_stat_statements` |
| Guard rails | `statement_timeout` 120 s, `lock_timeout` 0 (none), `idle_in_transaction_session_timeout` 0 (none), `work_mem` 2184 kB | `pg_settings` |
| Total size | 337 MB, cache hit ratio 0.9994, zero recorded deadlocks | `pg_stat_database` |
| `messages` | 61,194 rows, 249 MB, 82% of conversations are ChatGPT (4,782 of 5,797) | `pg_stat_user_tables` |
| `messages.embedding` | **0 of 61,194 populated**; 28 marked `embedding_skipped` | direct count |
| `classification_events` | 7,976 rows, **0 degraded**, 7,790 distinct cache keys (2.3% reuse) | direct count |
| `sync_jobs` | 96 rows: 84 `done`, 12 `cancelled`, **nothing open, newest 2026-11-02** | direct count |
| `review_queue` | 156 `open`, 50 `dismissed`; no consumer has drained it | direct count |
| `agent_sessions` / `agent_messages` / `dispatch_jobs` | **0 / 0 / 0** | direct count |
| `audit_log` | 249 rows, `idx_scan` 0 (never read by anything) | `pg_stat_user_tables` |

Two of those rows frame the whole audit. The semantic layer does not exist in production (no
vectors at all), and the agent subsystem has never carried a single row. Both are maintained,
indexed and reasoned about as though they were load-bearing.

## High-severity summary

| # | Finding | Chapter | Evidence |
| --- | --- | --- | --- |
| H1 | A transaction acquires a **second** pooled connection while holding one; 10 concurrent checkpoints exhaust the pool and hang | 7, 8 | `projects/service.py:39-64`, `sessions/service.py:350` |
| H2 | External HTTP (embeddings, 30 s timeout) runs **inside** the checkpoint transaction, holding row locks | 7, 8, 9 | `sessions/service.py:788`, `notes/service.py:167` |
| H3 | `enqueue` **silently drops** a vault write when a job for the same path is currently leased | 11 | `obsidian/queue.py:70-101`, `migrations/0004_ops.sql:36-38` |
| H4 | `write_list` jobs carry no `expected_sha256`, so a list render **blindly overwrites** a human edit | 5, 11 | `obsidian/queue.py:28-32`, `migrations/0002_core.sql:243-257` |
| H5 | The `sync-state` query cannot use the index built for it: `DESC NULLS LAST` vs a `DESC` (NULLS FIRST) index. Measured **1,041 ms → 0.361 ms** | 3 | `ingest.py:347-357`, `migrations/0013:…` |
| H6 | A failed checkpoint or ingest returns 500 with **no retry**, while the client's durable queue holds the only copy | 8, 11 | `ingest.py:254-284`, `extension/lib/api.js` |
| H7 | The auto-classify claim query is **O(all imported conversations)** per tick when the backlog is empty: 817 calls, mean 783 ms, max 3.1 s | 3, 10 | `importers/reclassify.py:184-202` |
| H8 | Derived-data pipelines have **no claim** and no leader election; singleton-ness is a config convention | 8, 9 | `embeddings/refresh.py:35-42`, `reclassify.py:210-238` |
| H9 | A checkpoint that saves a memory *and* is promoted to the vault **crashes after committing**: `saved` is rebound to the memory's result, then indexed as the conversation row. No test passes `queue_for_vault=True` | 7 | `sessions/service.py:416,534,555,692` |
| H10 | A degraded classification is not an "error", so the 6 h backoff never engages and the loop re-asks Jev every tick, appending a row each time | 8, 11 | `reclassify.py:213-228` |

---

## Part I: foundations

### Chapter 1: reliability, scalability, maintainability

**The read path is bound by round trips, not by work.** At ~200 ms to the pooler, `context_bootstrap`
costs 3-6 sequential queries plus one embedding call (`retrieval/service.py:1046-1054`,
`:1118-1159`, `:1213-1226`): a 0.6-1.2 s floor before any ranking. `memory_deep_search` runs its two
independent searches sequentially instead of concurrently (`retrieval/semantic.py:165-175`). A full
`session_checkpoint` is worse: the transaction body issues one or more statements per decision, per
memory, per task and per list item (`sessions/service.py:347-649`), which is dozens of sequential
round trips inside a single open transaction. Nothing in the system batches them.

**Every read is a cold read.** No result is cached anywhere: not the projects table
(`retrieval/service.py:912-914`, no `LIMIT`), not the home handoff (`:1118-1159`), not the provider
list. Each `context_bootstrap` re-fetches all of it. The eval harness reports `db_roundtrips`
(`tests/eval/retrieval_cases.yaml:39-41`), so the number is known and asserted only as a ceiling of 4.

**Maintainability: three sources of truth have drifted.** `docs/architecture.md:818-861` still
describes a Cloud Run deployment (production is Vercel plus a launchd Mac), `:1041-1044` lists the
browser extension as "designed for, not built" (it is built, shipped and is the largest writer of
messages), and `:954` claims 264 passing tests. The `pc_` prefix survives in every enum name and in
`PC_*` environment fallbacks after the rename to `subzero`. Documentation is derived data with no
reconciliation job.

**Dead weight that is maintained as though it were live.** Six HNSW vector indexes exist over
columns that contain zero non-null values (`migrations/0005_vector_indexes.sql`); each is 16 kB and
each is maintained on every insert. `messages_embedding_pending_idx` is 1,728 kB, a partial index
covering all 61,166 unembedded rows, rebuilt by `migrations/0013` and fed by 5,360 scans that have
never produced a vector. `audit_log` has 249 rows and `idx_scan = 0`. The agents subsystem
(`agents/service.py`, 596 lines, plus `agents/api.py`, `cli_agents.py`, `board/`, five tables and
four migrations) has **never stored a row**. `extension/lib/dedup.js` exports three functions that
nothing imports.

Indexes that are never scanned but still written on every insert: `messages_conv_seq_key`
(3,816 kB), `classification_events_input_idx` (992 kB, superseded by the `cache_key` expression
index), `conversations_project_idx` (768 kB, unusable because it leads with a highly nullable
`project_id`), `list_items_search_idx` (120 kB, unusable because the query matches on
`(x.search_tsv || l.search_tsv)`, an unindexed expression).

### Chapter 2: data models

The model choice is sound and worth stating, because it is the strongest part of the system: one
document store (Postgres) for canonical structured state, one blob store for bytes, one derived
human-readable rendering (the vault). Rows over Markdown for lists (`lists`, `list_items`) is the
right call and the `pc:id` marker as the only identity channel for a checkbox is a genuinely good
design (`docs/architecture.md:406-416`).

The weakness is that the graph is **implicit**. `projects` is the hub, but relationships live in
`artifact_relationships`, `entity_relationships`, `note_edges`, `moc_memberships`, plus
`source_conversation_id` columns copied onto eight tables. There is no single place that answers
"what is connected to this project", so every consumer re-derives it with its own join set. The
retrieval layer pays for this directly: `_conflicts` (`retrieval/service.py:752-838`) has to pull
every recent decision, memory and summary in a project and compare topic words in Python, because
no edge table records that two items are about the same thing.

### Chapter 3: storage and indexing

**H5, measured.** `ingest.py:347-357`:

```sql
SELECT provider_conversation_id, provider_updated_at FROM conversations
WHERE provider = $1 AND provider_conversation_id IS NOT NULL
ORDER BY provider_updated_at DESC NULLS LAST
LIMIT $2
```

`migrations/0013` created `conversations_provider_updated_idx (provider, provider_updated_at DESC)`
for exactly this query. A btree `DESC` column implies `NULLS FIRST`, so the index cannot satisfy
`DESC NULLS LAST` and the planner falls back to a sequential scan plus top-N sort:

```
Sort  (actual rows=200)   Sort Method: top-N heapsort
  ->  Seq Scan on conversations  (actual rows=4782)  Rows removed by filter: 1015
      Buffers: shared hit=1871
Execution Time: 1041.544 ms
```

Changing two words to `NULLS FIRST`, which the existing index can serve:

```
->  Index Scan using conversations_provider_updated_idx  (actual rows=200)
    Buffers: shared hit=60
Execution Time: 0.361 ms
```

**2,900x**, and 31x fewer buffers. This is the single most expensive statement in the database:
7,624 calls, 1,186 s of total execution time (19.8 minutes), mean 155.6 ms, **max 8,587.6 ms**. The
extension calls it every two minutes per history provider with `SYNC_RECENT = 200`
(`extension/background.js:18`). The fix is a matching partial index, not a query change: the
`NULLS LAST` ordering is part of the contract with the client.

**H7, measured.** `importers/reclassify.py:184-202` finds unclassified conversations with an
anti-join against `classification_events`, ordered by `c.updated_at DESC`. When a backlog exists the
plan stops early (measured 15.5 ms once 5 rows are found). When the backlog is empty, which is the
normal state, it walks all 5,679 imported conversations and probes the anti-join for each:
817 calls, mean **782.7 ms**, max 3,102.9 ms, 639 s total, returning 106 rows in all. The planner's
own estimate for the anti-join is `rows=5005`. A periodic full scan is being used as a work queue.

**Deep search is bounded but sequential.** `retrieval/semantic.py:39-64` bounds both queries with
`LIMIT $2` at `max(30, limit * 6)`, so an unbounded fetch is not the risk here. Ordering is:
`deep_search` (`:152-157`) awaited the query's embedding, then the full-text query, then the vector
query, so a deep search paid the sum of three latencies when the first two are independent.

**One projection really is wasted, and it is not the one it looks like.** The seven search branches
do *not* return `search_tsv`; the string `"x.search_tsv"` in `retrieval/service.py:217,236,253,269,286,301,317`
is the argument that fills the `{tsv}` placeholder in the rank and scope fragments, and the captured
production query text confirms the select list carries only `rank`, `coverage`, `phrase_hit` and
`exact`. The conflict query is the real offender: `retrieval/service.py:1039-1042` re-selects
`decisions.search_tsv` by id (247 calls, mean 301.9 ms, **max 5,968.6 ms**) so that `_extract_terms`
can take it apart in Python, and it also re-selects the summaries' (`:1046`) for the same reason.

**Per-row tsquery parsing.** The `coverage` column in every union branch is
`(SELECT count(*) FROM unnest($3::text[]) t WHERE x.search_tsv @@ to_tsquery($26, t))`
(`retrieval/service.py:136-138`): a tsquery is parsed again for every term of every candidate row.
The `q` CTE already exists to hoist exactly this; it hoists four queries and leaves the per-term
array inside the correlated subquery. This is the dominant cost of the hybrid search statement:
438 calls, mean 434.4 ms, max 4,352.2 ms.

**Predicates no index can serve.** The project filter appears in all seven branches as
`($6::uuid IS NULL OR x.project_id = $6 OR ($10::bool AND x.project_id IS NULL) OR (… @@ q.pn))`
(`retrieval/service.py:140-146`). An `OR` with a full-text match and a parameter-dependent null test
cannot be served by either the btree or the GIN index. The same shape in `_handoff`
(`:200-202`, `:228-230`) wraps the column in `coalesce(s.project_id, c.project_id) = $1`, which is
not indexable at all: 285 calls, mean 402.1 ms, max 3,423.4 ms.

**Duplicate and redundant indexes.** `messages_conv_idx (conversation_id, sequence)` duplicates the
unique `messages_conv_seq_key` on the same columns (`migrations/0002_core.sql:98`,
`:222`). `bridge_messages_session_seq_idx` duplicates `bridge_messages_session_id_sequence_key`
(`migrations/0010_bridge_sessions.sql:41`, `:43`). `agent_sessions_seen_idx` is redundant with
`agent_sessions_external_id_key`. `artifact_files_path_key` is redundant with the composite unique
index that has `object_key` as its leading column. `memories_project_idx (project_id, updated_at DESC)`
has `idx_scan = 0` while `memories` is seq-scanned 300 times.

**Unbounded tables with no retention.** `messages` (61,194 and growing), `audit_log`, `sync_events`,
`classification_events` (7,976), `memory_revisions`, `note_versions`, and `agent_events`
(explicitly documented as an unbounded log with no retention at `agents/service.py:155-177`).
`oauth_kv` is the only one with an expiry, and its expiry index has `idx_scan = 0`: nothing prunes
expired rows.

### Chapter 4: encoding and evolution

**Enums are encoded three different ways.** Postgres enums `pc_provenance`, `pc_sensitivity`,
`pc_storage_status`, `pc_classification_status` (`migrations/0001_extensions.sql:33-73`); `TEXT`
with a Python `Literal` and no constraint (`notes.note_class`, `sync_jobs.job_type`,
`sync_jobs.status`, `agent_sessions.status`, `dispatch_jobs.status`); and `TEXT` with a `CHECK`
(`lists.status`, `list_items.status`, `bridge_sessions.status`). One vocabulary, three enforcement
levels. The `TEXT`+`Literal` tables accept any string from any writer that bypasses the service
layer, and `models.py` `extra="forbid"` only guards the MCP boundary.

**Postgres enums are append-only in practice.** Adding a label is `ALTER TYPE … ADD VALUE`, which
before PG 12 could not run in a transaction and even now cannot be used in the same transaction that
created it. Removing or renaming a label requires recreating the type and rewriting every column.
The migration runner wraps each file in one transaction (`db/migrate.py:85-91`), so a migration that
adds a label and then uses it will fail. `0012_muse_provider.sql` is the precedent for the safe
path: it inserts a `providers` row rather than extending an enum.

**`pc_sensitivity` ordinal order carries semantics.** `importers/sensitivity.py` raises a level with
`GREATEST(a, b)`, which for a Postgres enum compares internal ordinals. Reordering the labels, or
inserting one in the middle, silently changes what "more sensitive" means with no code change and
no test failure.

**`VECTOR(1536)` is hard-coded in six columns and six indexes** (`migrations/0002_core.sql:94,123,158,228,318,364`,
`0005_vector_indexes.sql`) while `config.EMBEDDING_DIM = 1536` is used only client-side. Nothing
asserts that the configured provider's dimension matches the column. Switching provider is a runtime
failure on every write, discovered in production.

**42 JSONB columns, none versioned.** `artifacts.manifest` is the one with an explicit
`manifest_version`. `classification_events.answers` is keyed by `QUESTIONS_VERSION` in a sibling
column, which is the right idea. `sync_jobs.payload`, `session_summaries.important_context`,
`audit_log.previous_version`/`resulting_version` and every `metadata` blob are unversioned: a shape
change is indistinguishable from an old row.

**Timestamps are consistent** (`TIMESTAMPTZ` throughout `public`; zero `timestamp without time zone`
columns verified), and `pc_touch_updated_at()` triggers maintain `updated_at`. One hazard survives:
`conversations.provider_updated_at` stores **the provider's** clock reading, and
`extension/content.js` compares it against a locally captured `updated_at`. Two unsynchronised clocks
decide whether a conversation is re-fetched.

### Chapter 5: replication

There is no application-level replication: one canonical Postgres, one writer role. Supabase's own
streaming replication is invisible to the application, and `pg_stat_database` reports zero
conflicts. This is the right shape for a single user and the audit does not recommend changing it.

What replication thinking does apply is **the vault as a replica with a conflict-detecting protocol**.
`expected_sha256` is optimistic concurrency control done properly for notes and maps: the server
records what the worker last wrote (`obsidian/queue.py:28-32`, `:146-162`), the worker compares
before writing (`obsidian_sync/vault.py:78-88`), and a mismatch becomes a terminal `conflict` rather
than a silent overwrite (`queue.py:171-192`).

**H4 breaks that invariant for lists.** `_SYNCED_HASH_SQL` has entries for `note` and `moc` only, so
a `write_list` job gets `expected_sha256 = None`, the worker's hash check is skipped, and the render
overwrites whatever a human typed into the list note. `lists` has `vault_path`
(`migrations/0002_core.sql:251`) and no `vault_synced_sha256` column at all. The docstring at
`queue.py:55-62` promises the opposite behaviour. Checkbox state survives because it flows back
through `pc:id` markers; prose does not.

**Two further replica gaps.** `queue.complete()` (`:135-162`) issues the job update and the
`vault_synced_sha256` update as two separate autocommit statements: a crash between them leaves the
job `done` with no recorded hash, so the *next* write of that file has no `expected_sha256` and
overwrites blindly. And `obsidian_sync/worker.py` keeps the set of files it has written in an
in-process map (`:91-92`), which `scan_for_edits` uses to decide what to check for human edits
(`vault.py:111+`). Restart the worker and every human edit made before the restart becomes invisible
to the inbound path: the vault and the database diverge with no record. A `conflict` is likewise
terminal with no automatic recovery, and nothing re-reads the file to clear it.

### Chapter 6: partitioning

Not applicable at 337 MB, and the audit does not recommend it. Two observations belong here anyway.

**The data is already skewed by provider**: ChatGPT is 82% of conversations. Every provider-scoped
query (ingest, sync-state, reclassification) touches that one slice, so a future partitioning by
provider would be natural, and `messages` would partition cleanly on `conversation_id` because every
hot predicate leads with it (`migrations/0002_core.sql:98-99`, `:222`).

**Hot rows exist and are contended.** `device_tokens` has 12 live rows and 8,095 updates: `ingest.py:41`
runs `UPDATE device_tokens SET last_used_at = now()` on *every* authenticated request (8,071 calls,
28.7 s of DB time) and **nothing in the repository ever reads `last_used_at`** (verified across
`src`, `tests`, `extension`, `migrations`). It is a write-only column on the auth path, producing
~675 row versions per token. `projects.touch` (`projects/service.py:236-239`) is the same pattern on
9 rows, called from inside the checkpoint transaction.

### Chapter 7: transactions

**The good news first.** `session_checkpoint` really is one transaction for its main body
(`sessions/service.py:347-649`), classification genuinely runs before it opens (`:342-344`, and the
comment at `:683-684` says so), and idempotency comes from deterministic keys with matching unique
indexes: `conversations (provider, provider_conversation_id)`, `decisions (project_id, statement_sha256)`,
`memories (content_sha256, coalesce(subject,''))`, `list_items (list_id, text_sha256)`,
`session_summaries (conversation_id)`. The message batch is a single `unnest` statement with a
`NOT EXISTS` freshness pre-filter and `ON CONFLICT DO NOTHING` (`sessions/service.py:54-87`), so a
whole-conversation retry is safe. `ensure_conversation` takes a `FOR UPDATE` row lock
(`migrations/0002_core.sql:56-58`), which accidentally serialises concurrent checkpoints of the same
conversation. The Obsidian queue and the dispatch queue both use `FOR UPDATE SKIP LOCKED` correctly
(`obsidian/queue.py:107-133`, `agents/service.py:470`), and `notes/service.py:118` and
`bridge/service.py:70` use `pg_advisory_xact_lock`, which a transaction pooler tolerates.

**H1: nested acquisition from a bounded pool.** `ProjectService.resolve` (`projects/service.py:39-64`)
and `ListService.resolve` / `resolve_item` (`lists/service.py:123-137`, `:326-333`) always use
`self._db`, which acquires its own connection (`db/pool.py:120-134`). They are called *from inside*
the checkpoint transaction (`sessions/service.py:350`, `:364`, `:580`, `:600`, `:611`), which is
already holding one. Two consequences. Correctness: those reads do not see the transaction's own
uncommitted writes, so a project created at `:366` is not visible to the `resolve` at `:364` on a
later iteration. Liveness: with `db_pool_max_size = 10` (`config.py:109`), ten concurrent checkpoints
each hold one connection and wait for a second. asyncpg's `acquire()` has no timeout, and
`lock_timeout` and `idle_in_transaction_session_timeout` are both 0 in production, so nothing breaks
the deadlock. The server hangs until the client gives up.

**H2: a network call inside the transaction.** `_upsert_summary` calls `embeddings.embed_one`
(`sessions/service.py:788`) and `notes.upsert` does the same (`notes/service.py:167`), both while the
caller's transaction is open. The embedding provider has a 30 s timeout. A slow or hung OpenAI call
holds a connection, a transaction, and the `FOR UPDATE` lock on the conversation row for its whole
duration, with no `idle_in_transaction_session_timeout` to reap it. `memory/service.py:119` and `:225`
make the same call outside a transaction, which is fine.

**Non-idempotent read-modify-write, unguarded.** `artifacts/service.py:424-441` computes
`max(version)+1` in a plain `SELECT` and inserts, so two concurrent revisions of one artifact collide
on `artifacts_project_version_key` and surface as a 500, not a retry. `lists/service.py:83-98` and
`:292-321` do the same for `position` (`max+1` with no unique constraint), so two inserts can land on
the same position and `list_move_item` then produces an arbitrary order. `_supersede`
(`memory/service.py:267-281`) computes `next_version` from an earlier read; the
`memory_revisions (memory_id, version)` unique index is the only thing preventing a duplicate, and
its `ON CONFLICT DO NOTHING` means the loser silently loses its revision row while its `UPDATE` still
applies.

**H9: the promotion branch crashes on the common path.** `sessions/service.py:416` binds `saved` to
the conversation row read back under `FOR UPDATE`, and `:692` reads `saved["slug"]` from it while
queuing the vault note. Between those two lines the decisions loop (`:534`) and the facts loop
(`:555`) rebind `saved` to a `MemorySaveResult`, which is a Pydantic model and not subscriptable.
`queue_for_vault` defaults to `True` (`:277`), so any checkpoint that saves at least one memory and
receives a `promote_to_vault = yes` routing raises `TypeError` *after* the transaction has
committed: the data is stored and the caller is told the call failed. Every test that calls
`session_checkpoint` passes `queue_for_vault=False`, which is why nothing caught it. Production has
not hit it yet: the current server log contains no `session_checkpoint` call, and `sync_jobs` has had
no new row since 2026-11-02.

**Post-commit work is not atomic with the commit.** Vault promotion, review queueing, the
reclassification queue and the audit record all run *after* `COMMIT`, each on its own autocommit
connection (`sessions/service.py:651-702`). A crash between the commit and `_queue_summary_note`
leaves a checkpoint that is saved, reported as saved, and never rendered to the vault, with no row
anywhere recording that a render was owed. All four are pure database writes and `SyncQueue.enqueue`
already accepts a `conn` (`obsidian/queue.py:52`), so they can be inside the transaction; only
`AuditService.record` (`audit/service.py:27-49`) lacks the parameter.

**`_write` for notes has no transaction at all.** `notes/write.py:220-264` runs seven to nine
sequential autocommit statements: upsert, wiki link extraction, note-edge replacement, wiki
invalidation, vault-path update, queue render, fingerprint update, audit. Each is its own implicit
transaction with no rollback. A failure part-way leaves a note stored but not queued, or queued with
a stale `expected_sha256`.

**Isolation is correct for what the code does**, with one exception. Read committed plus
`ON CONFLICT` plus the unique indexes is a sound combination, and no code depends on repeatable
reads. The exception is `_upsert_summary`'s open-questions merge (`sessions/service.py:428-453`): it
reads the existing summary, merges in Python, and writes back. The `FOR UPDATE` on the summary row at
`:440` protects it only for checkpoints; `importers/runner.py:415-437` writes summaries outside any
transaction, so an import and a checkpoint can lose one another's questions.

### Chapter 8: unreliable networks, clocks, partial failure

**No retry anywhere on the database path.** `db/pool.py:120-134` has no retry, no caller wraps a
query, and a single dropped pooler connection becomes a single failed user request. Reads are
idempotent and could be retried safely; writes cannot, and correctly lean on idempotency keys
instead. The distinction is not currently made explicit anywhere.

**H6: the failure mode the client cannot survive.** `ingest.py:254-284` computes
`MAX(sequence)+1` per message batch and inserts with `unnest … WITH ORDINALITY`. Two writers to one
conversation (two paired browsers, or the Mac and Vercel) both compute the same base; the loser
violates `messages (conversation_id, sequence)` and the whole request 500s. The extension backs off
and retries, which converges, but a 500 is also what a genuine server fault looks like, so the client
cannot distinguish "retry me" from "I am broken". `sessions/service.py:54-87` has the same race with
the same outcome, and `sessions/service.py:424` (`_upsert_summary`) is not retried either.

**H10: a degraded classifier is not an error.** `classify_forever` records a backoff only for entries
in `report.errors` (`reclassify.py:226-228`). A Jev outage produces a *degraded* result, which the
policy engine handles gracefully and which is not an error, so `failed_at` stays empty and the same
conversations are re-asked every `auto_classify_seconds`, each pass appending another
`classification_events` row and logging `"conversations classified"`. The in-memory `failed_at` dict
(`:213`) is also per-process and lost on restart, so the 6 h backoff (`RETRY_AFTER_S`, `:207`) never
survives a deploy. Production currently shows 0 degraded events out of 7,976, so this has not fired
yet; it is a latent failure that turns a Jev outage into unbounded table growth and unbounded cost.

**The embedding loop retries a permanent failure forever, and reports success while doing it.**
`refresh.py:114-136` catches everything, logs, and sleeps a fixed `interval`. This is not
hypothetical. The production server log on 2026-11-22 holds **5,916 tracebacks**, every one of them
`subzero.errors.SubzeroError: Embedding request failed: HTTP 429` raised at
`embeddings/service.py:70`, each immediately followed by
`{"level": "INFO", "message": "embeddings refreshed", "embedded": 0, "skipped": 0, "failed": 64}`.
At one tick every two minutes that is eight days of a loop fetching 64 full message bodies, being
refused by the provider, and logging the outcome at `INFO` under a message that reads as success.
There is no backoff, no consecutive-failure counter, and `/healthz` is a static `"ok"` so nothing
outside the process can see it. `_PENDING_SQL` (`:35-42`) also transfers whole message bodies and
truncates them in Python afterwards, and orders `created_at DESC`, so a large backlog is worked
newest-first.

**Both loops are entered unconditionally** (`server.py:611-615`) and the "only the Mac runs them"
invariant is a configuration convention: `auto_classify_seconds` defaults to 0, Vercel's environment
leaves the embedding provider off, and nothing checks either. Turn both on by mistake and two
processes embed and classify the same rows, paying the provider twice and writing two
`classification_events` for one conversation, with nothing to show that it happened. The tasks
themselves are handled correctly, contrary to first appearance: each context manager keeps its task,
cancels it on shutdown and awaits it (`refresh.py:139-151`, `reclassify.py:241-253`), and both loops
catch every exception per tick, so neither dies silently.

**Clocks.** `provider_updated_at` is the provider's clock and is compared with the extension's
(`extension/content.js`). `sync_jobs.leased_until` is `now() + 300 s` on the server, and the worker
has no renewal, so a write that takes longer than the lease can be re-leased to a second worker
while the first is still writing; the `expected_sha256` check makes that a conflict rather than
corruption, which is the right outcome, but the worker cannot tell that it lost its lease.
`storage/supabase.py:141` caches signed URLs by expiry with no clock-skew guard, per instance.

**Timeouts are present on every request-path HTTP client**, with one CLI-only exception
(`extraction/deep.py:330`, Anthropic, no timeout). The real exposure is the sum: Jev 12 s times three
attempts, embeddings 30 s, storage 60 s, DB statement 15 s, against a Vercel function with no
`maxDuration` set (`vercel.json` contains only `{"framework": "python"}`).

### Chapter 9: consistency and consensus

**There is no consensus and no leader election.** H8: the singleton property of the two background
loops rests on `SUBZERO_AUTO_CLASSIFY_SECONDS` and `SUBZERO_EMBEDDING_REFRESH_SECONDS` being zero on
Vercel (`config.py:241`, `:254`, and `deploy/production.env.op`). Set one by mistake and both
instances embed and classify the same rows: duplicated external API cost, duplicated
`classification_events`, last-writer-wins on `messages.embedding`. Neither loop claims its work, so
the duplication is not merely possible but invisible.

The codebase already contains the two primitives that would fix this, and uses them elsewhere:
`FOR UPDATE SKIP LOCKED` for the two real queues, and `pg_advisory_xact_lock` for notes and the
bridge. A lease row (`name` primary key, holder, `leased_until`) renewed per tick is the boring,
correct answer, and it also makes "both are off" detectable.

**The migration runner has no lock.** `apply_migrations` reads `schema_migrations` into a dict and
then applies what is missing (`db/migrate.py:63-94`). Two concurrent `subzero migrate` runs (the
Docker entrypoint and a laptop, or two Vercel builds) both see the same pending set and both apply
it; the loser aborts on `schema_migrations_pkey` because the recording `INSERT` has no
`ON CONFLICT`. The failure is loud and atomic rather than corrupting, which is good, but it is a
deploy-time coin flip. `pg_advisory_xact_lock` is already used 40 lines away in the codebase.
Duplicate numeric prefixes (`0009_email_drafts` and `0009_pairing_codes`, `0010_agents` and
`0010_bridge_sessions`) are harmless today because the version is the full filename stem sorted
lexicographically, and both pairs are recorded as applied in production; but nothing refuses a third,
and the ordering between two files with the same prefix is alphabetical, not intentional.

**Linearizability is not claimed and not needed.** What is needed is that a committed checkpoint is
durably reflected in its derived data, and chapter 11 covers where that breaks.

### Chapter 10: batch processing

Imports are the batch layer and they are well built: content-hash deduplication across the whole
corpus, `imported_metadata` provenance to keep mechanical summaries honest, mechanical rather than
model-written summaries, and a documented "re-running is a no-op" property
(`docs/architecture.md:749-761`). `importers/runner.py:415-437` is the weak point: `ensure_conversation`
and `add_messages` run as separate autocommit operations across concurrent per-conversation tasks
(`:353-356`), so an import interrupted part-way leaves conversations with partial message sets and no
transactional record of progress.

**The batch layer leaks into the serving path.** The auto-classify loop is a periodic full scan
(H7) rather than an incrementally maintained work list, and `reclassification_queue` already exists
(`migrations/0004_ops.sql:90-101`, unique on `(object_type, object_id)`, with an exponential backoff
in `queue_reclassification`) but is not what drives the loop. The system has the right table and
queries the wrong one.

**Deep extraction is a batch job with a stream job's assumptions.** `extraction/deep.py` is CLI-only,
unclaimed, and writes its completion flag *last* (`:600-604`), so a crash or a concurrent run
re-appends model-generated note content; the append-hash deduplication in `notes/service.py:157-164`
cannot match non-deterministic model output.

### Chapter 11: stream processing and derived data

The derived-data inventory is accurate and the design intent is right: `classification_events`
(pass 1, cached by input hash), summaries and memories (pass 2), `messages.embedding`, the wiki
graph, and the vault. Every one of them is a function of canonical state, and every one can in
principle be recomputed. In practice:

**H3: the vault queue drops writes.** `sync_jobs_open_path_key` is unique on `vault_path`
`WHERE status IN ('queued','approved','leased')` (`migrations/0004_ops.sql:36-38`). `enqueue` cancels
only `('queued','approved')` before inserting (`obsidian/queue.py:70-76`) and then does
`INSERT … ON CONFLICT DO NOTHING RETURNING id` (`:77-101`). If a job for that path is **leased**
(the worker is mid-write, a 300 s window), the insert conflicts, `enqueue` returns `None`, and the
only thing that happens is that no log line is emitted. Callers treat `None` as "nothing to do":
`wiki/service.py` still stamps `wiki_rendered_at` / `rendered_at = now()`, so the render is recorded
as done and the content is never written. It is lost until something else changes the graph.
Production has not hit it yet (96 jobs, none stuck, none open since 2026-11-02), because the queue
has been nearly idle; a busy vault hits a 300 s lease window routinely.

**Nothing reconciles.** There is no job that compares canonical state against derived state and
repairs it. The embedding backlog is the clearest case: 0 of 61,194 messages embedded, a pipeline
that has failed 5,360 consecutive times, and no way to ask the system "what is stale?".
`review_queue` has 156 open items and no drainer. `reclassification_queue` exists and is populated by
degraded classifications, but the loop does not read it (chapter 10).

**Cache invalidation is by input hash only.** `classifier/service.py:221-236` keys the cache on
question set, classifier name, model id, state and candidates, which is correct, and
`QUESTIONS_VERSION` participates, so changing the questions invalidates everything. But the cached
answer is then reused with a *new* policy: `ask_cached` returns the cached result and `policy.route`
runs on it (`:286-287`), so a threshold change silently re-routes old classifications with no record
that the routing changed. `classification_events` rows are immutable and never re-derived.

**Notes, memories and summaries embed inline with no backfill.** Only messages have a refresh loop.
`notes/service.py:167`, `memory/service.py:119` and `sessions/service.py:788` call `embed_one` at
write time and swallow failures, so a provider outage permanently leaves those rows without vectors
and nothing ever returns to them.

### Chapter 12: "unbundling the database"

Subzero is already an unbundled system: Postgres for indexed state, object storage for bytes, a
queue table for the outbound stream to the vault, a local worker as the consumer, Jev and Claude as
external processors, and the extension as a producer with its own durable log. That is the right
architecture and the audit recommends keeping it.

What is missing is the part that makes unbundling safe: **the queues are not the source of truth for
their own progress.** The extension's durable queue (`chrome.storage` index plus per-conversation
bodies, three-way concurrent drain, exponential backoff, dead letters) is a genuine client-side log,
and its `delivered` watermark keyed by `updated_at` is a reasonable design, but the server cannot
tell the client "I committed your batch" separately from "I failed", so an ambiguous timeout is
resolved by a retry that only works because the server is idempotent on `provider_message_id`. That
idempotency is the load-bearing wall and it holds (`sessions/service.py:54-87`). The sequence-number
race (H6) is the crack in it.

The periodic sync window is the other gap: `SYNC_RECENT = 200` plus a newest-first early stop means a
conversation that changes outside the 200 newest, while the extension is not looking, is never
re-fetched. `LIMIT $2` is also `None` when the client omits `recent`, which is an unbounded fetch
(`ingest.py:356`).

---

## What the audit recommends, in order

1. **Make the checkpoint transaction self-contained** (H1, H2, chapter 7): thread the transaction
   connection through `ProjectService.resolve`, `ListService.resolve` and `resolve_item`; move the
   `embed_one` calls outside the transaction; move vault enqueue, review queueing and the audit
   record inside it. This removes the pool-starvation hang, the lock-holding network call, and the
   lost-render window in one change, and it makes the documented "one call, one transaction" true.
2. **Stop dropping and overwriting vault writes** (H3, H4, chapter 5, 11): narrow
   `sync_jobs_open_path_key` to `('queued','approved')`; add `lists.vault_synced_sha256` and a
   `list` entry in `_SYNCED_HASH_SQL`; make `complete()` one transaction; make callers treat a
   `None` from `enqueue` as a failure rather than a no-op.
3. **Fix the two measured query disasters** (H5, H7): a partial index with the ordering the query
   actually uses, and drive auto-classify from `reclassification_queue` with `SKIP LOCKED` instead of
   an anti-join over every imported conversation.
4. **Cheaper reads** (chapter 3): stop re-parsing a tsquery per term per candidate row in `coverage`,
   run a deep search's independent embedding and full-text queries together, and give the conflict
   query the lexemes instead of the tsvector.
5. **Make the loops single by construction, and make failure durable** (H8, H10, chapter 8, 9): a
   lease row both loops must hold, classification backoff stored in the database rather than in a
   per-process dict, and exponential backoff plus a `WARNING` in the embedding loop.
6. **Give the migration runner a lock** (chapter 9): one transaction behind `pg_advisory_xact_lock`,
   with `ON CONFLICT DO NOTHING` on the recording insert.
7. **Reconcile derived data** (chapter 11): a `subzero reconcile` command that reports and repairs
   stale embeddings, unrendered notes, orphaned classification events and open review items. This is
   the missing piece that makes the rest of the architecture safe to leave unbundled.
8. **Delete or park what is not live** (chapter 1): the empty agent subsystem, `extension/lib/dedup.js`,
   the redundant and never-scanned indexes, and the write-only `device_tokens.last_used_at` update.
9. **Rewrite the stale chapters of `docs/architecture.md`** (chapter 1): deployment, the extension,
   the test count, and the `pc_` naming.

---

## What this branch changes

All of it is implemented on `ddia-audit`, and `ruff check src tests`, `mypy src` and the integration
suite pass against a real PostgreSQL 17 with pgvector. Migration `0014` has **not** been applied to
the production database; that is an operator step.

| Finding | Change | Files |
| --- | --- | --- |
| H1 | `ProjectService.resolve`, `ListService.resolve` and `resolve_item` take the caller's connection and every call inside the checkpoint transaction passes it. No transaction acquires a second pooled connection, so the starvation deadlock is gone and those reads see their own writes. | `projects/service.py`, `lists/service.py`, `sessions/service.py` |
| H2 | A summary's vector is computed before the transaction opens and passed in. A note's vector is no longer computed at write time at all: notes join the background refresh loop, where messages already were, and a note whose content changes has its stale vector cleared instead of kept. | `sessions/service.py`, `notes/service.py`, `embeddings/refresh.py`, `migrations/0014` |
| H3 | `sync_jobs_open_path_key` no longer covers `leased`, so a render requested while the worker holds a lease queues behind it instead of vanishing. `enqueue` logs the remaining insert race instead of returning `None` silently, and `wiki.render` counts a deferred render rather than stamping `rendered_at` for a job it never queued. | `migrations/0014`, `obsidian/queue.py`, `wiki/service.py` |
| H4 | `lists.vault_synced_sha256` exists, `_SYNCED_HASH_SQL` covers `list`, and `complete()` records the job and the hash in one transaction. A list render now says what it expects to replace, so a human edit becomes a conflict instead of being overwritten. | `migrations/0014`, `obsidian/queue.py` |
| H5 | A partial index with the ordering the query actually uses: measured on production, 1,041 ms and 1,871 buffers become 0.361 ms and 60 buffers. `sync-state` also clamps `recent`, so a client that omits it cannot ask for the whole table. | `migrations/0014`, `ingest.py` |
| H7, H8, H10 | The auto-classify loop claims from `reclassification_queue` with `FOR UPDATE SKIP LOCKED` and a 10-minute reservation, and ingest enqueues a capture inside the same transaction that stores it. Migration 0014 seeds the queue with what the scan would have found, so nothing waiting is dropped by the change. Backoff is the durable `next_attempt_at` that a degraded result already updates, so the per-process retry map is gone. Both loops must hold a row in `background_leases` before doing any work. | `importers/reclassify.py`, `ingest.py`, `classifier/service.py`, `db/lease.py`, `migrations/0014` |
| H9 | The memory result no longer shadows the conversation row, and vault promotion, review queueing and the reclassification queue moved inside the checkpoint transaction. The audit record stays outside it on purpose: failing to log a mutation must not roll the mutation back. Two integration tests now drive `queue_for_vault=True`, which nothing did before. | `sessions/service.py`, `tests/test_integration_checkpoint_promotion.py` |
| ch 3 | `coverage` reads pre-parsed tsqueries from the `q` CTE instead of re-parsing every term for every candidate row of all seven branches; a deep search runs its embedding call and its full-text query together. | `retrieval/service.py`, `retrieval/semantic.py` |
| ch 8 | The embedding loop backs off exponentially, to 30 intervals, after a tick that produced only failures, logs a `WARNING` on the third such tick, and truncates bodies in SQL rather than after the round trip. | `embeddings/refresh.py` |
| ch 9 | `apply_migrations` is one transaction behind `pg_advisory_xact_lock` and records with `ON CONFLICT DO NOTHING`, so two machines migrating at once serialise instead of one crashing on the primary key. | `db/migrate.py` |
| ch 1 | `device_tokens.last_used_at` is written at most once a minute instead of on every authenticated request; `extension/lib/dedup.js` is deleted; two indexes that duplicated a unique index are dropped; `embed-backfill` counts notes as well as messages. | `ingest.py`, `extension/`, `migrations/0014`, `cli.py` |

**Deliberately not done here.** Item 7, a `subzero reconcile` command, is a new subsystem and needs
its own design. The remaining chapter 3 costs (the `OR`-shaped project filter, the unindexable
`coalesce(s.project_id, c.project_id)`, the `_conflicts` reload at up to 5.97 s, the per-item project
lookups in `_home_handoff`) are query rewrites that move ranking, so they belong with the retrieval
eval harness rather than in the same change as a schema migration. The version and position races in
`artifacts/service.py` and `lists/service.py` need either `ON CONFLICT` retry loops or serializable
isolation, and choosing between them is a decision about what those tools promise. The worker's
in-process `_written` map needs a server-side manifest endpoint, which is a protocol change for the
Obsidian helper. And `memories` still embeds inline, because its deduplication compares vectors and
so needs one synchronously; moving that means changing how deduplication works.

---

## Second pass: what is still open on `main`

A second audit, written against the same base commit `341e6d2`, was re-checked against `main` after
the changes above had landed. Four of its findings were already fixed by them (the second pooled
connection, the summary embedding inside the transaction, the leased-path drop, and the unlocked
migration runner and loops) and are not repeated here. What remains is below, in the order it is
being fixed. Each fix starts with a failing test that reproduces the bug.

The second audit also cited a 15 s statement timeout. The measurement at the top of this document
(120 s, no `lock_timeout`, no `idle_in_transaction_session_timeout`) is the one to trust.

| # | Finding | Chapter | Evidence on `main` |
| --- | --- | --- | --- |
| S1 | Every artifact classification, the tools' and `subzero reclassify`'s alike, told the classifier the artifact was `sensitivity: "normal"` whatever its stored level, because `ArtifactRecord` did not carry it. A `highly_sensitive` artifact could leave the process. `subzero reclassify` also never recorded the result on the artifact, and skipped a queued live checkpoint without removing it, so it sat at the head of the queue for ever. **Fixed:** the record carries its sensitivity, filing lives in `artifacts/filing.py`, and `drain_reclassification_queue` claims like the background loop and redoes live checkpoints from what was stored. | 1, 11, privacy | `mcp/tools_artifacts.py:52`, `cli.py:601-645` |
| S2 | `memories.save` calls the embedding provider inside the checkpoint transaction, holding row locks across a network call, and a new memory with no subject match is embedded twice: once to look for a near duplicate and again to store. **Fixed:** `save` takes a precomputed vector and embeds at most once; checkpoint vectorises every memory in one batch per sensitivity level before the transaction opens. Memory, summary and note queries also now bind vectors as `$n::text::vector`, as the project rule says: they failed on any connection opened before the vector type existed. | 7, 8 | `sessions/service.py:532,552`, `memory/service.py:119,225` |
| S3 | The Obsidian helper keeps "what I last wrote" only in memory. After a restart, and on every `obsidian-sync run --once` (the Linux timer path), the map is empty, so no human edit is ever sent upstream. The server's `expected_sha256` stops it being overwritten, so the edit is stranded rather than lost. **Fixed:** the helper keeps the map in a state file under `$XDG_STATE_HOME`, written atomically once per tick. | 5, 11 | `obsidian_sync/worker.py:35,86` |
| S4 | `save_files` "verifies" storage by comparing the hash of the local buffer with itself, because `put()` hashes what it sent, not what was stored. `_next_version` reads `max(version)` with no lock, so two saves of one slug collide. A failed upload part-way through a bundle leaves `captured` file rows under a `referenced` artifact. `artifact_complete_upload` also recorded the caller's claimed hash after checking only that an object existed. **Fixed:** bytes are read back and hashed before anything is called captured, in both paths; a version is allocated under a per-slug advisory lock (simpler than a retry loop, and it keeps the object keys stable); a bundle that fails part-way is marked `failed`. | 7, 12 | `artifacts/service.py:138-231`, `storage/supabase.py:102-123` |
| S5 | In the extension, a drain worker stops at the first conversation that fails, stranding everything due behind it. Re-queueing an unsent conversation keeps the newer attachment list and drops the older one. ZIP imports never set `provider_updated_at`, so periodic sync cannot tell they are up to date and walks past them. **Fixed:** a worker moves past a conversation that failed and stops only when the server is unreachable (no response, 502, 503, 504), so an outage still does not spend every item's attempts; a re-capture keeps the older capture's attachments; an import writes the conversation, its messages and `provider_updated_at` in one transaction. Conversations imported earlier get the time on their next import. | 8, 11 | `extension/lib/queue.js:265`, `importers/runner.py:419` |
| S6 | `add_messages` numbers new messages from `max(sequence) + 1` without locking the conversation, so two concurrent writers for one conversation pick the same numbers and one fails on `messages_conv_seq_key`. **Fixed:** `add_messages` takes `FOR NO KEY UPDATE` on the conversation before numbering, in the caller's transaction or its own, so writers take turns without blocking foreign-key checks. | 7 | `sessions/service.py:54-87`, `ingest.py:290`, `importers/runner.py:433` |
| S7 | `oauth_kv.purge_expired()` has no caller (727 rows in production). A crashed agent leaves its `dispatch_jobs` row `claimed` for ever; the board only masks the session as idle. | 8 | `auth/kv.py:134`, `agents/service.py:478` |
| S8 | `docs/architecture.md` still lists the browser extension and the embedding backfill under "Not built, deliberately". | 1 | `docs/architecture.md:1057-1066` |

Three earlier "deliberately not done" notes are revisited by this list. S3 keeps the helper's state in
a file beside the vault instead of adding a server endpoint: the helper is the only writer of that
vault, so its own record of what it wrote is the right source, and no protocol change is needed. S2
keeps deduplication by vector and computes the vector once, before the transaction, passing it in.
S4 settles the version race with a per-slug advisory lock held while the row is inserted: a retry loop would have to recompute the object keys, which are derived from the version.

**Left as they are.** The duplicate migration prefixes (`0009_*`, `0010_*`, `0012_*`) are harmless:
the runner records each file by name and content hash, and renaming an applied file would make it
look new. A test now refuses a new duplicate instead. Messages with no provider id are not
deduplicated by content alone, because two genuine "ok" turns would merge. The seven to nine round
trips in `ingest_conversation` are latency, not correctness, and wait for the reconcile work.
