"""Unit tests for Web Dashboard UI static files and routes."""

from fastapi.testclient import TestClient

from assistant_core.main import create_app


def test_ui_index_served() -> None:
    """Test that /ui and /ui/ return 200 OK with HTML content."""
    app = create_app()
    client = TestClient(app)
    res = client.get("/ui")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Assistant Core" in res.text
    assert "Memory Manager" in res.text

    res_slash = client.get("/ui/")
    assert res_slash.status_code == 200
    assert "text/html" in res_slash.headers["content-type"]


def test_ui_static_assets_served() -> None:
    """Test that CSS and JS assets are accessible."""
    app = create_app()
    client = TestClient(app)
    css_res = client.get("/ui/styles.css")
    assert css_res.status_code == 200
    assert "text/css" in css_res.headers.get("content-type", "")

    js_res = client.get("/ui/app.js")
    assert js_res.status_code == 200
    assert "javascript" in js_res.headers.get("content-type", "")


def test_inspection_stats_and_recent_with_session_token() -> None:
    """Test that inspection endpoints accept session authentication issued by /v1/auth/login."""
    from typing import Self

    class MockQueryResult:
        def __init__(self, val: object = 0) -> None:
            self._val = val

        def scalar_one_or_none(self) -> object | None:
            return None

        def scalar_one(self) -> object:
            return self._val

        def scalars(self) -> "MockQueryResult":
            return self

        def all(self) -> list[object]:
            return []

    class MockSessionContext:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, _statement: object) -> MockQueryResult:
            return MockQueryResult()

    app = create_app()
    app.state.session_factory = lambda: MockSessionContext()
    client = TestClient(app)

    # 1. Login to get a session token
    login_res = client.post("/v1/auth/login", json={"password": "development-hmac-secret-change-me"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]

    # 2. Call /v1/inspection/stats with session header
    stats_res = client.post(
        "/v1/inspection/stats",
        json={"native_user_id": "test-user"},
        headers={"x-assistant-session": token},
    )
    assert stats_res.status_code == 200
    stats_data = stats_res.json()
    assert "total_segments" in stats_data
    assert "queued_jobs" in stats_data

    # 3. Call /v1/inspection/recent with session header
    recent_res = client.post(
        "/v1/inspection/recent",
        json={"native_user_id": "test-user", "limit": 5},
        headers={"x-assistant-session": token},
    )
    assert recent_res.status_code == 200
    recent_data = recent_res.json()
    assert "results" in recent_data
