"""Security defaults (review R-01, R-18, R-43): local mode is machine-local, the first account cannot
silently take over the local analyst's data, login throttling, generic sign-up errors, session revocation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from analystos_api.main import create_app
from conftest import ApiClient, make_settings, remote_client, signup

API = "/api/v1"


def _local_with_workspace(app: Any) -> tuple[ApiClient, dict[str, Any]]:
    c = ApiClient(app)
    assert c.get(f"{API}/auth/session").json()["authenticated"]
    ws = c.post(f"{API}/workspaces", json={"name": "Victim data"}).json()
    return c, ws


def test_remote_caller_is_not_signed_in_as_local_analyst(app: Any) -> None:
    victim, ws = _local_with_workspace(app)
    with remote_client(app) as attacker:
        s = attacker.get(f"{API}/auth/session").json()
        assert s["authenticated"] is False
        assert s["local_mode_denied"] is True
        assert s["signup_allowed"] is False
        assert "aos_session" not in attacker.cookies
        assert attacker.get(f"{API}/workspaces").status_code == 401
        # The first account cannot be created remotely in auto mode.
        r = attacker.post(
            f"{API}/auth/signup",
            json={"email": "evil@example.com", "name": "E", "password": "correct horse battery"},
        )
        assert r.status_code == 403 and r.json()["code"] == "signup_requires_local_access"
    # The local analyst still has the workspace, still in local mode.
    assert [w["id"] for w in victim.get(f"{API}/workspaces").json()] == [ws["id"]]


def test_local_session_cookie_is_refused_from_a_remote_peer(app: Any) -> None:
    victim, _ = _local_with_workspace(app)
    with remote_client(app) as attacker:
        attacker.cookies.set("aos_session", victim.cookies.get("aos_session") or "")
        r = attacker.get(f"{API}/workspaces")
        assert r.status_code == 401 and r.json()["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example.com"},  # DNS rebinding: loopback peer, foreign hostname
        {"X-Forwarded-Host": "analystos.example.com"},  # a local proxy relaying a remote hostname
    ],
)
def test_loopback_peer_with_foreign_host_is_not_local(app: Any, headers: dict[str, str]) -> None:
    with ApiClient(app) as c:
        s = c.get(f"{API}/auth/session", headers=headers).json()
        assert s["authenticated"] is False and s["local_mode_denied"] is True


@pytest.mark.parametrize(
    "peer,host",
    [
        ("127.0.0.1", "localhost:3000"),
        ("::1", "[::1]:8000"),
        ("127.0.0.1", "127.0.0.1:8000"),
        ("::ffff:127.0.0.1", "app.localhost"),
    ],
)
def test_loopback_variants_are_local(app: Any, peer: str, host: str) -> None:
    with ApiClient(app, client=(peer, 1234)) as c:
        assert c.get(f"{API}/auth/session", headers={"Host": host}).json()["authenticated"] is True


def test_signup_without_local_session_does_not_claim_local_data(app: Any) -> None:
    victim, ws = _local_with_workspace(app)
    with ApiClient(app) as other:  # same machine, but not holding the local analyst's session
        info = signup(other, "new@example.com")
        assert info["user"]["is_local"] is False
        assert other.get(f"{API}/workspaces").json() == []
    # Password mode is now on: the local session no longer works, and the data was not handed over.
    assert victim.get(f"{API}/workspaces").status_code == 401


def test_signup_with_local_session_claims_and_revokes_old_sessions(app: Any) -> None:
    victim, ws = _local_with_workspace(app)
    old_cookie = victim.cookies.get("aos_session")
    info = signup(victim, "owner@example.com")
    assert info["auth_mode"] == "password" and info["user"]["is_local"] is False
    assert [w["id"] for w in victim.get(f"{API}/workspaces").json()] == [ws["id"]]
    with ApiClient(app) as replay:
        replay.cookies.set("aos_session", old_cookie or "")
        assert replay.get(f"{API}/workspaces").status_code == 401


def test_production_refuses_local_modes_without_opt_in(tmp_path: Path) -> None:
    for mode in ("auto", "local"):
        with pytest.raises(RuntimeError, match="AOS_ALLOW_INSECURE_LOCAL"):
            create_app(make_settings(tmp_path, AOS_ENV="production", AUTH_MODE=mode))
    create_app(make_settings(tmp_path / "a", AOS_ENV="production", AUTH_MODE="password"))
    create_app(
        make_settings(tmp_path / "b", AOS_ENV="production", AUTH_MODE="auto", AOS_ALLOW_INSECURE_LOCAL=True)
    )


def test_insecure_local_opt_in_allows_remote_local_mode(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, AOS_ALLOW_INSECURE_LOCAL=True))
    with remote_client(app) as c:
        assert c.get(f"{API}/auth/session").json()["authenticated"] is True


def test_bootstrap_token_gates_first_account_and_claims(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, AUTH_MODE="auto", AOS_BOOTSTRAP_TOKEN="boot-" + "z" * 30))
    victim, ws = _local_with_workspace(app)
    with remote_client(app) as c:
        s = c.get(f"{API}/auth/session").json()
        assert s["bootstrap_token_required"] is True and s["signup_allowed"] is True
        body = {"email": "admin@example.com", "name": "Admin", "password": "correct horse battery"}
        r = c.post(f"{API}/auth/signup", json=body)
        assert r.status_code == 403 and r.json()["code"] == "bootstrap_token_required"
        r = c.post(f"{API}/auth/signup", json={**body, "bootstrap_token": "wrong"})
        assert r.status_code == 403
        r = c.post(f"{API}/auth/signup", json={**body, "bootstrap_token": "boot-" + "z" * 30})
        assert r.status_code == 201, r.text
        assert [w["id"] for w in c.get(f"{API}/workspaces").json()] == [ws["id"]]


def test_password_mode_first_signup_gets_no_existing_data(tmp_path: Path) -> None:
    # A local analyst existed (auto mode), then the operator switched to password mode.
    s1 = make_settings(tmp_path, AUTH_MODE="auto")
    app = create_app(s1)
    _local_with_workspace(app)
    app2 = create_app(make_settings(tmp_path, AUTH_MODE="password", AOS_ALLOW_SIGNUP=False))
    with remote_client(app2) as stranger:
        signup(stranger, "stranger@example.com")
        assert stranger.get(f"{API}/workspaces").json() == []


def test_signup_closed_after_first_account_by_default(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, AOS_ALLOW_SIGNUP=False))
    with ApiClient(app) as first:
        first.get(f"{API}/auth/session")
        signup(first, "first@example.com")
    with remote_client(app) as second:
        s = second.get(f"{API}/auth/session").json()
        assert s["signup_allowed"] is False
        r = second.post(
            f"{API}/auth/signup",
            json={"email": "second@example.com", "name": "S", "password": "correct horse battery"},
        )
        assert r.status_code == 403 and r.json()["code"] == "signup_disabled"


def test_signup_does_not_reveal_existing_accounts(client: ApiClient) -> None:
    signup(client, "taken@example.com")
    with ApiClient(client.app) as other:
        r = other.post(
            f"{API}/auth/signup",
            json={"email": "taken@example.com", "name": "X", "password": "correct horse battery"},
        )
        assert r.status_code == 400 and r.json()["code"] == "signup_failed"
        assert "exist" not in r.json()["detail"].lower()


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_login_throttling_locks_account_with_backoff(app: Any) -> None:
    clock = _Clock()
    app.state.aos.login_throttle.clock = clock
    with ApiClient(app) as c:
        signup(c, "vic@example.com")
        c.post(f"{API}/auth/logout")
        bad = {"email": "vic@example.com", "password": "wrong-password-1"}
        codes = [c.post(f"{API}/auth/login", json=bad).status_code for _ in range(5)]
        assert codes == [401] * 5
        r = c.post(
            f"{API}/auth/login", json={"email": "vic@example.com", "password": "correct horse battery"}
        )
        assert r.status_code == 429 and r.json()["code"] == "too_many_attempts"
        assert int(r.headers["Retry-After"]) == 30
        clock.t += 31
        # One more failure after the lock doubles it.
        assert c.post(f"{API}/auth/login", json=bad).status_code == 401
        r = c.post(f"{API}/auth/login", json=bad)
        assert r.status_code == 429 and int(r.headers["Retry-After"]) == 60
        clock.t += 61
        r = c.post(
            f"{API}/auth/login", json={"email": "vic@example.com", "password": "correct horse battery"}
        )
        assert r.status_code == 200
        # Success resets the account counter.
        assert c.post(f"{API}/auth/login", json=bad).status_code == 401


def test_login_throttling_per_ip_across_accounts(app: Any) -> None:
    app.state.aos.login_throttle.clock = _Clock()
    with remote_client(app) as c:
        statuses = [
            c.post(f"{API}/auth/login", json={"email": f"u{i}@example.com", "password": "x"}).status_code
            for i in range(21)
        ]
        assert statuses[:20] == [401] * 20 and statuses[20] == 429
    audit_codes = app.state.aos.session_factory
    from analystos_api.models import AuditLog
    from sqlalchemy import func, select

    with audit_codes() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth.login_failed")
            )
            == 20
        )


def test_password_change_revokes_other_sessions_and_optionally_tokens(client: ApiClient) -> None:
    signup(client, "pw@example.com")
    tok = client.post(f"{API}/auth/tokens", json={"name": "mcp"}).json()["token"]
    with ApiClient(client.app) as other:
        other.post(f"{API}/auth/login", json={"email": "pw@example.com", "password": "correct horse battery"})
        assert other.get(f"{API}/auth/me").status_code == 200
        r = client.patch(
            f"{API}/auth/me",
            json={"current_password": "correct horse battery", "new_password": "another long password"},
        )
        assert r.status_code == 200
        assert other.get(f"{API}/auth/me").status_code == 401
        assert client.get(f"{API}/auth/me").status_code == 200
        assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
        client.patch(
            f"{API}/auth/me",
            json={
                "current_password": "another long password",
                "new_password": "third long password!",
                "revoke_tokens": True,
            },
        )
        assert client.get(f"{API}/workspaces", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


SECRET = "proxy-secret-" + "q" * 24


@pytest.mark.parametrize(
    "headers,local",
    [
        ({}, True),  # direct loopback request, no proxy header
        ({"X-AOS-Client-Addr": "127.0.0.1", "X-AOS-Proxy-Secret": SECRET}, True),
        ({"X-AOS-Client-Addr": "::1", "X-AOS-Proxy-Secret": SECRET}, True),
        (
            {"X-AOS-Client-Addr": "203.0.113.7", "X-AOS-Proxy-Secret": SECRET},
            False,
        ),  # remote browser via proxy
        ({"X-AOS-Client-Addr": "127.0.0.1"}, False),  # unauthenticated proxy header
        ({"X-AOS-Client-Addr": "127.0.0.1", "X-AOS-Proxy-Secret": "wrong"}, False),
        ({"X-AOS-Client-Addr": "127.0.0.1, 203.0.113.7", "X-AOS-Proxy-Secret": SECRET}, False),
        ({"X-AOS-Client-Addr": "not-an-ip", "X-AOS-Proxy-Secret": SECRET}, False),
    ],
)
def test_proxy_client_header_decides_local_mode(tmp_path: Path, headers: dict[str, str], local: bool) -> None:
    """Decision Q2(a): a loopback peer is local only if there is no proxy header or an authenticated proxy
    header names a loopback client."""
    app = create_app(make_settings(tmp_path, AOS_PROXY_SECRET=SECRET))
    with ApiClient(app) as c:
        s = c.get(f"{API}/auth/session", headers=headers).json()
        assert s["authenticated"] is local and s["local_mode_denied"] is (not local)
        if local:
            # The same session is refused as soon as a request arrives through the proxy for a remote client.
            r = c.get(
                f"{API}/workspaces",
                headers={"X-AOS-Client-Addr": "203.0.113.7", "X-AOS-Proxy-Secret": SECRET},
            )
            assert r.status_code == 401


def test_proxy_header_without_configured_secret_is_not_local(app: Any) -> None:
    with ApiClient(app) as c:
        s = c.get(
            f"{API}/auth/session", headers={"X-AOS-Client-Addr": "127.0.0.1", "X-AOS-Proxy-Secret": ""}
        ).json()
        assert s["authenticated"] is False


@pytest.mark.parametrize("env", ["test", "production"])
def test_local_mode_only_in_development(tmp_path: Path, env: str) -> None:
    """Decision Q2(b): outside development, auto mode behaves as password mode unless explicitly opted in."""
    app = create_app(
        make_settings(tmp_path, AOS_ENV=env, AUTH_MODE="password" if env == "production" else "auto")
    )
    with ApiClient(app) as c:
        s = c.get(f"{API}/auth/session").json()
        assert s["authenticated"] is False and s["auth_mode"] == "password"
        # The first account can be created (fresh, no local data to claim) and then sign-up closes.
        signup(c, "first@example.com")
    app2 = create_app(make_settings(tmp_path / "x", AOS_ENV="test", AOS_ALLOW_INSECURE_LOCAL=True))
    with ApiClient(app2) as c:
        assert c.get(f"{API}/auth/session").json()["authenticated"] is True


def _proxied(client_addr: str, secret: str | None = SECRET) -> dict[str, str]:
    h = {"X-AOS-Client-Addr": client_addr}
    if secret is not None:
        h["X-AOS-Proxy-Secret"] = secret
    return h


def test_throttling_uses_the_authenticated_proxy_client_address(tmp_path: Path) -> None:
    """Behind the web proxy every request comes from the proxy's address: per-address throttling must use the
    authenticated X-AOS-Client-Addr, so one attacker cannot lock every browser out."""
    app = create_app(make_settings(tmp_path, AOS_PROXY_SECRET=SECRET))
    app.state.aos.login_throttle.clock = _Clock()
    with ApiClient(app) as proxy:  # every call arrives from the proxy on loopback
        attacker = _proxied("203.0.113.66")
        codes = [
            proxy.post(
                f"{API}/auth/login", json={"email": f"u{i}@example.com", "password": "x"}, headers=attacker
            ).status_code
            for i in range(21)
        ]
        assert codes[:20] == [401] * 20 and codes[20] == 429
        # Another browser behind the same proxy is not locked out.
        r = proxy.post(
            f"{API}/auth/login",
            json={"email": "someone@example.com", "password": "x"},
            headers=_proxied("198.51.100.7"),
        )
        assert r.status_code == 401
    from analystos_api.models import AuditLog
    from sqlalchemy import select

    with app.state.aos.session_factory() as db:
        ips = {a.ip for a in db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed"))}
    assert ips == {"203.0.113.66", "198.51.100.7"}


@pytest.mark.parametrize("secret", [None, "wrong"])
def test_unauthenticated_proxy_client_address_is_ignored(tmp_path: Path, secret: str | None) -> None:
    """A spoofed X-AOS-Client-Addr (no or wrong secret) cannot dodge the per-address limit or fake audit IPs."""
    app = create_app(make_settings(tmp_path, AOS_PROXY_SECRET=SECRET))
    app.state.aos.login_throttle.clock = _Clock()
    with remote_client(app) as c:
        codes = [
            c.post(
                f"{API}/auth/login",
                json={"email": f"v{i}@example.com", "password": "x"},
                headers=_proxied(f"192.0.2.{i}", secret),
            ).status_code
            for i in range(21)
        ]
        assert codes[20] == 429
    from analystos_api.models import AuditLog
    from sqlalchemy import select

    with app.state.aos.session_factory() as db:
        ips = {a.ip for a in db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed"))}
    assert ips == {"203.0.113.9"}  # the socket peer of remote_client


def test_client_address_resolution(tmp_path: Path) -> None:
    from analystos_api.services.auth import client_address
    from starlette.requests import Request

    settings = make_settings(tmp_path, AOS_PROXY_SECRET=SECRET)

    def req(headers: dict[str, str]) -> Request:
        return Request(
            {
                "type": "http",
                "client": ("127.0.0.1", 1),
                "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            }
        )

    assert client_address(req({}), settings) == "127.0.0.1"
    assert client_address(req(_proxied("203.0.113.5, 10.0.0.1")), settings) == "203.0.113.5"
    assert client_address(req(_proxied("[2001:db8::1]")), settings) == "2001:db8::1"
    assert client_address(req(_proxied("garbage")), settings) == "127.0.0.1"
    assert client_address(req(_proxied("203.0.113.5", "nope")), settings) == "127.0.0.1"
    assert client_address(req(_proxied("203.0.113.5")), make_settings(tmp_path)) == "127.0.0.1"
