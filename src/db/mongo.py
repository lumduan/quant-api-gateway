from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from src.config import get_settings

_client: AsyncIOMotorClient[Any] | None = None


def get_client() -> AsyncIOMotorClient[Any]:
    """Return the singleton motor client for MongoDB."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(settings.mongo_uri)
    return _client


def close_client() -> None:
    """Close the motor client and null out the global reference."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
