"""Domain errors.

These map onto MCP tool errors. Messages are written for the model calling the
tool, so they say what to do differently rather than dumping internals.
"""

from __future__ import annotations


class SubzeroError(Exception):
    """Base class for all errors this service raises deliberately."""


class NotFoundError(SubzeroError):
    """A referenced object does not exist."""


class ValidationError(SubzeroError):
    """Caller supplied something this service will not accept."""


class ConflictError(SubzeroError):
    """The object changed since the caller read it.

    Returned instead of overwriting: re-read and retry against the new
    version rather than guessing what the other writer did.
    """


class PathSecurityError(ValidationError):
    """A path escaped its allowed root, or contained a forbidden component."""


class StorageError(SubzeroError):
    """Object storage refused or failed an operation."""


class IntegrityError(SubzeroError):
    """Stored bytes did not match the hash we expected."""


class ClassifierUnavailable(SubzeroError):
    """The external classifier could not be reached.

    Never fatal: callers fall back to deterministic rules and mark the object
    for reclassification.
    """


class RateLimited(SubzeroError):
    """Caller exceeded the configured request budget."""


class EmbeddingInputError(SubzeroError):
    """The embedding provider refused the request itself, not the caller.

    An input over the model's token limit makes OpenAI reject the whole batch
    with a 400. That is a different failure from a dead or rate-limited
    provider, and the response is different: shrink or drop the offending
    input, rather than retrying a batch that can never succeed.
    """


class ArchiveSecurityError(ValidationError):
    """An archive tripped a zip-slip, entry-count, or decompression guard."""
