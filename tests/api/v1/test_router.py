"""Integration tests for the v1 router and root-level meta endpoints."""

from httpx import AsyncClient


async def test_health_returns_ok(async_client: AsyncClient) -> None:
    """``GET /health`` returns 200 with the expected JSON payload."""
    response = await async_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_content_type_is_json(async_client: AsyncClient) -> None:
    """``GET /health`` advertises ``application/json``."""
    response = await async_client.get("/health")

    assert response.headers["content-type"].startswith("application/json")


async def test_v1_prefix_does_not_404_at_openapi(async_client: AsyncClient) -> None:
    """OpenAPI describes the app; the v1 mount point is present (even if empty).

    Phase 1 attaches the v1 router with no routes, so the route table won't
    yet contain any ``/api/v1/...`` entries — but the schema must still
    serve. Regression guard: confirms the router include did not break
    OpenAPI generation.
    """
    response = await async_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Quant API Gateway"
    assert "/health" in schema["paths"]
