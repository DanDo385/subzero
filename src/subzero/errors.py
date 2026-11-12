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


class ArchiveSecurityError(ValidationError):
    """An archive tripped a zip-slip, entry-count, or decompression guard."""
