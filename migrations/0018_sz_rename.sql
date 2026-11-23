-- Finish the rename from the project's old initials to its name.
--
-- These five objects are internal: nothing a user types and nothing stored as
-- text, so renaming them is a metadata change. Existing rows keep their values,
-- and a trigger follows its function by identity rather than by name, so every
-- updated_at trigger keeps firing.
--
-- The migrations that created these are frozen and still say pc_, because the
-- runner refuses an edited migration by hash. A fresh database therefore
-- creates the old names and renames them here.
--
-- Deploy order matters. Code from this commit casts to sz_* and code from
-- before it casts to pc_*, so an old build against a renamed type fails on
-- every write that names one. Apply this and restart both servers together,
-- rather than migrating a database an old build is still serving.
ALTER TYPE pc_provenance RENAME TO sz_provenance;
ALTER TYPE pc_sensitivity RENAME TO sz_sensitivity;
ALTER TYPE pc_storage_status RENAME TO sz_storage_status;
ALTER TYPE pc_classification_status RENAME TO sz_classification_status;
-- Schema-qualified: PostgreSQL's parser rejects the bare name with an empty
-- argument list in the RENAME form.
ALTER FUNCTION public.pc_touch_updated_at() RENAME TO sz_touch_updated_at;

-- A migration added after this one must call sz_touch_updated_at(), not the old
-- name: the frozen files above still say pc_ because they ran before the
-- rename, and that is the only place the old name is still correct.
