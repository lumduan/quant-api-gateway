"""Services public interface — re-exports from the engines layer.

Backward-compatible: existing callers that import from ``src.services``
directly continue to work. New consumers should prefer ``src.engines``
imports for engine-scoped access.
"""
