from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_no_auth_required(client: AsyncClient) -> None:
    """Health endpoint must be reachable without any credentials."""
    response = await client.get("/health", headers={})
    assert response.status_code == 200
