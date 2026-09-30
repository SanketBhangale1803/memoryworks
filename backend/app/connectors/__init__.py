"""MemoryWorks connector SDK, signed package registry, and execution runtime."""

from .base import (
    Connector,
    ConnectorAccount,
    ConnectorManifest,
    ConnectorTool,
    SyncBatch,
    SyncRecord,
)


def get_connector_registry():
    # Keep the base contract importable by the auth vault without importing
    # registry -> vault recursively during application startup.
    from .registry import get_connector_registry as load

    return load()


__all__ = [
    "Connector",
    "ConnectorAccount",
    "ConnectorManifest",
    "ConnectorTool",
    "SyncBatch",
    "SyncRecord",
    "get_connector_registry",
]
