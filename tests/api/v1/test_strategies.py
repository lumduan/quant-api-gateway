"""Tests for ``GET /api/v1/strategies``."""

from httpx import AsyncClient


async def test_list_strategies_returns_active_only(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """The fixture registry has 2 active + 1 inactive entry."""
    response = await async_client.get("/api/v1/strategies")
    assert response.status_code == 200
    body = response.json()
    ids = {entry["id"] for entry in body}
    assert ids == {"csm-set-01", "tfex-01"}
    # Inactive legacy entry must not appear
    assert "legacy-00" not in ids
    # Schema fields are present
    for entry in body:
        assert {"id", "name", "service_url", "capital_weight", "active"} <= entry.keys()


async def test_list_strategies_requires_no_auth(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """Strategy listing is unauthenticated — only ingestion needs the API key."""
    response = await async_client.get("/api/v1/strategies")
    assert response.status_code == 200
