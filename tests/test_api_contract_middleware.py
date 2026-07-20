import asyncio

import httpx
import pytest
from fastapi import FastAPI

from api_contracts import ApiContractMiddleware
from auth import get_actor
from main import app as main_app


def contract_app(payload: dict) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiContractMiddleware)

    @app.get("/result")
    async def result():
        return payload

    return app


async def request(app: FastAPI, path: str = "/result") -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": "error", "message": "Not authenticated"}, 401),
        ({"status": "error", "message": "Permission denied"}, 403),
        ({"status": "error", "message": "Issue missing not found"}, 404),
        ({"status": "error", "message": "Record changed since you loaded it"}, 409),
        ({"status": "error", "message": "Work order WO-1 is deleted"}, 410),
        ({"status": "error", "message": "Engine not ready"}, 503),
        ({"status": "error", "message": "Invalid collection name"}, 400),
        ({"status": "duplicate", "message": "File already ingested"}, 409),
        ({"status": "not_found", "message": "Document not found"}, 404),
        ({"status": "accepted", "job_id": "job-1"}, 202),
        ({"status": "ok"}, 200),
    ],
)
def test_legacy_envelopes_receive_meaningful_http_status(payload: dict, expected: int) -> None:
    response = asyncio.run(request(contract_app(payload)))

    assert response.status_code == expected
    expected_payload = {**payload, "status": "error"} if payload.get("status") == "not_found" else payload
    assert response.json() == expected_payload
    if expected == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_historical_work_order_endpoint_no_longer_returns_200_for_missing_auth() -> None:
    response = asyncio.run(request(main_app, "/work-orders/missing"))

    assert response.status_code == 401
    assert response.json() == {"status": "error", "message": "Not authenticated"}


def test_historical_work_order_endpoint_uses_404_for_missing_record() -> None:
    main_app.dependency_overrides[get_actor] = lambda: {
        "user_id": "admin01",
        "role": "admin",
        "line_scope": ["*"],
    }
    try:
        response = asyncio.run(request(main_app, "/work-orders/missing"))
    finally:
        main_app.dependency_overrides.pop(get_actor, None)

    assert response.status_code == 404
    assert response.json()["status"] == "error"
