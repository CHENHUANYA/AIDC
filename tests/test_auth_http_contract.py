import asyncio
from unittest.mock import patch

import httpx
from fastapi import FastAPI

import auth


app = FastAPI()
app.include_router(auth.router)


async def request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def test_invalid_login_returns_401_with_bearer_challenge() -> None:
    with patch.object(auth, "load_users", return_value={}):
        response = asyncio.run(
            request("POST", "/auth/login", json={"username": "unknown", "password": "invalid-password"})
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"status": "error", "message": "Invalid username or password"}


def test_current_user_without_session_returns_401_with_bearer_challenge() -> None:
    response = asyncio.run(request("GET", "/auth/me"))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"status": "error", "message": "Not authenticated"}
