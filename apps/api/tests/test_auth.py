"""Authentication, sessions, CSRF and API tokens."""

from __future__ import annotations

from typing import Any

from conftest import ApiClient, signup


def test_local_mode_session_and_csrf(client: ApiClient) -> None:
    r = client.get("/api/v1/auth/session")
    body = r.json()
    assert body["authenticated"] and body["auth_mode"] == "local"
    assert body["user"]["is_local"] is True
    cookie_header = ",".join(r.headers.get_list("set-cookie"))
    assert "aos_session=" in cookie_header and "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header or "samesite=lax" in cookie_header.lower()
    # Unsafe request without the CSRF header is refused.
    r = client.post("/api/v1/workspaces", json={"name": "x"}, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"
    r = client.post("/api/v1/workspaces", json={"name": "x"})
    assert r.status_code == 201


def test_unauthenticated_requests_are_rejected(app: Any) -> None:
    with ApiClient(app) as c:
        r = c.get("/api/v1/workspaces")
        assert r.status_code == 401
        assert r.json()["code"] == "not_authenticated"
        assert r.json()["request_id"]


def test_signup_claims_local_user_and_switches_to_password_mode(client: ApiClient) -> None:
    client.get("/api/v1/auth/session")
    ws = client.post("/api/v1/workspaces", json={"name": "Mine"}).json()
    info = signup(client, "ana@example.com", name="Ana")
    assert info["auth_mode"] == "password" and info["user"]["email"] == "ana@example.com"
    assert info["user"]["is_local"] is False
    # The local analyst's workspace now belongs to Ana.
    assert [w["id"] for w in client.get("/api/v1/workspaces").json()] == [ws["id"]]
    # A fresh browser is no longer auto-signed-in.
    with ApiClient(client.app) as other:
        s = other.get("/api/v1/auth/session").json()
        assert s["authenticated"] is False and s["auth_mode"] == "password"


def test_login_logout_and_bad_password(client: ApiClient) -> None:
    signup(client, "bo@example.com")
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/workspaces").status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "bo@example.com", "password": "nope-nope-nope"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"
    r = client.post(
        "/api/v1/auth/login", json={"email": "BO@example.com", "password": "correct horse battery"}
    )
    assert r.status_code == 200 and r.json()["authenticated"]
    assert client.get("/api/v1/auth/me").json()["email"] == "bo@example.com"


def test_password_is_hashed_with_argon2(client: ApiClient, app: Any) -> None:
    signup(client, "hash@example.com")
    from analystos_api.models import User

    with app.state.aos.session_factory() as db:
        user = db.query(User).filter_by(email="hash@example.com").one()
        assert user.password_hash and user.password_hash.startswith("$argon2")
        assert "correct horse" not in user.password_hash


def test_short_password_rejected(client: ApiClient) -> None:
    r = client.post("/api/v1/auth/signup", json={"email": "s@example.com", "name": "S", "password": "short"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


def test_api_tokens_hashed_scoped_and_revocable(client: ApiClient, app: Any) -> None:
    signup(client, "tok@example.com")
    ws1 = client.post("/api/v1/workspaces", json={"name": "A"}).json()
    ws2 = client.post("/api/v1/workspaces", json={"name": "B"}).json()
    created = client.post("/api/v1/auth/tokens", json={"name": "mcp", "workspace_id": ws1["id"]}).json()
    raw = created["token"]
    assert raw.startswith("aos_")
    from analystos_api.models import ApiToken

    with app.state.aos.session_factory() as db:
        stored = db.get(ApiToken, created["meta"]["id"])
        assert stored is not None and stored.token_hash != raw and raw not in stored.token_hash
    with ApiClient(app) as bearer:
        h = {"Authorization": f"Bearer {raw}"}
        listed = bearer.get("/api/v1/workspaces", headers=h).json()
        assert [w["id"] for w in listed] == [ws1["id"]]
        assert bearer.get(f"/api/v1/workspaces/{ws2['id']}", headers=h).status_code == 404
        # Tokens cannot mint tokens.
        r = bearer.post("/api/v1/auth/tokens", json={"name": "x"}, headers=h)
        assert r.status_code == 403
        client.delete(f"/api/v1/auth/tokens/{created['meta']['id']}")
        assert bearer.get("/api/v1/workspaces", headers=h).status_code == 401


def test_read_only_token_cannot_write(client: ApiClient, app: Any) -> None:
    signup(client, "ro@example.com")
    ws = client.post("/api/v1/workspaces", json={"name": "A"}).json()
    raw = client.post("/api/v1/auth/tokens", json={"name": "ro", "read_only": True}).json()["token"]
    with ApiClient(app) as bearer:
        h = {"Authorization": f"Bearer {raw}"}
        assert bearer.get(f"/api/v1/workspaces/{ws['id']}", headers=h).status_code == 200
        r = bearer.patch(f"/api/v1/workspaces/{ws['id']}", json={"name": "B"}, headers=h)
        assert r.status_code == 403 and r.json()["code"] == "token_read_only"
        # Read-only POSTs (running a query) are allowed.
        r = bearer.post(
            f"/api/v1/workspaces/{ws['id']}/queries/run", json={"sql": "SELECT 1 AS x"}, headers=h
        )
        assert r.status_code == 200, r.text


def test_request_id_propagates(local_client: ApiClient) -> None:
    r = local_client.get("/api/v1/workspaces", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    r = local_client.get("/api/v1/workspaces", headers={"X-Request-ID": "bad id with spaces"})
    assert r.headers["X-Request-ID"] != "bad id with spaces"


def test_security_headers(local_client: ApiClient) -> None:
    r = local_client.get("/api/v1/workspaces")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
