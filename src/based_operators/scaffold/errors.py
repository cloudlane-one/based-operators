"""Errors raised while scaffolding a deployment.

Every failure that is the caller's to fix — an entry point that cannot be
imported, a resource that cannot be generated, an output directory that is
already taken — is reported as a :class:`ScaffoldError`, so a command-line front
end can turn it into a single error line instead of a traceback.
"""

from __future__ import annotations

__all__ = [
    "ResourceLoadError",
    "ScaffoldError",
]


class ScaffoldError(Exception):
    """Raised when a scaffold cannot be derived or written.

    This covers invalid inputs (a name or an image reference that Kubernetes or
    Docker would reject), an output directory that is already in use, and a
    resource that cannot be turned into a CRD.
    """


class ResourceLoadError(ScaffoldError):
    """Raised when the operator entry point cannot be loaded.

    This covers an entry point that cannot be imported, and one that imports
    cleanly but declares no resource at all.
    """
