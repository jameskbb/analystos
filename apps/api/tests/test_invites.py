"""Workspace invites (decision Q1): single-use, expiring tokens that create the account and membership."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from analystos_api.main import create_app
from conftest import ApiClient, make_settings, remote_client, signup

API = "/api/v1"


def _owner(tmp_path: Path) -> tuple[Any, ApiClient, str]:
    app = create_app(make_settings(tmp_path, AOS_ALLOW_SIGNUP=False))
    owner = ApiClient(app)
    owner.get(f"{API}/auth/session")
    signup(owner, "owner@example.com")
    ws = owner.post(f"{API}/workspaces", json={"name": "Team"}).json()["id"]
    return app, owner, ws


def test_invite_create_preview_accept_and_single_use(tmp_path: Path) -> None:
    app, owner, ws = _owner(tmp_path)
    r = owner.post(
        f"{API}/workspaces/{ws}/invites", json={"email": "New.Person@Example.com", "role": "editor"}
    )
    assert r.status_code == 201, r.text
    created = r.json()
    token = created["token"]
    assert created["accept_path"] == f"/invite/{token}" and created["invite"]["status"] == "pending"
    assert created["invite"]["email"] == "new.person@example.com"
    from analystos_api.models import Invite

    with app.state.aos.session_factory() as db:
        stored = db.get(Invite, created["invite"]["id"])
        assert stored is not None and token not in stored.token_hash  # only the hash is stored
    with remote_client(app) as guest:
        # Sign-up is closed, but the invite works.
        r = guest.post(
            f"{API}/auth/signup", json={"email": "x@example.com", "name": "X", "password": "p" * 12}
        )
        assert r.status_code == 403
        p = guest.get(f"{API}/auth/invites/{token}").json()
        assert p["workspace_name"] == "Team" and p["role"] == "editor" and p["account_exists"] is False
        assert (
            guest.post(f"{API}/auth/invites/accept", json={"token": token}).json()["code"]
            == "password_required"
        )
        r = guest.post(
            f"{API}/auth/invites/accept",
            json={"token": token, "name": "New", "password": "a strong password"},
        )
        assert r.status_code == 201, r.text
        s = r.json()
        assert (
            s["authenticated"]
            and s["user"]["email"] == "new.person@example.com"
            and s["default_workspace_id"] == ws
        )
        me = guest.get(f"{API}/workspaces").json()
        assert [(w["id"], w["role"]) for w in me] == [(ws, "editor")]
        # Single use.
        r = guest.post(
            f"{API}/auth/invites/accept", json={"token": token, "name": "N", "password": "a strong password"}
        )
        assert r.status_code == 404 and r.json()["code"] == "invite_invalid"
    # The new person can log in with the password they chose.
    with remote_client(app) as again:
        r = again.post(
            f"{API}/auth/login", json={"email": "new.person@example.com", "password": "a strong password"}
        )
        assert r.status_code == 200
    listed = owner.get(f"{API}/workspaces/{ws}/invites").json()
    assert [i["status"] for i in listed] == ["accepted"]
    actions = [a["action"] for a in owner.get(f"{API}/workspaces/{ws}/audit").json()["items"]]
    assert {"invite.create", "invite.accept"} <= set(actions)


def test_invite_revoke_expiry_and_permissions(tmp_path: Path) -> None:
    app, owner, ws = _owner(tmp_path)
    t1 = owner.post(f"{API}/workspaces/{ws}/invites", json={"email": "a@example.com"}).json()
    assert owner.delete(f"{API}/workspaces/{ws}/invites/{t1['invite']['id']}").status_code == 200
    with remote_client(app) as guest:
        assert guest.get(f"{API}/auth/invites/{t1['token']}").status_code == 404
    # Re-inviting replaces a pending invite.
    t2 = owner.post(
        f"{API}/workspaces/{ws}/invites", json={"email": "b@example.com", "role": "viewer"}
    ).json()
    t3 = owner.post(
        f"{API}/workspaces/{ws}/invites", json={"email": "b@example.com", "role": "editor"}
    ).json()
    statuses = {i["id"]: i["status"] for i in owner.get(f"{API}/workspaces/{ws}/invites").json()}
    assert statuses[t2["invite"]["id"]] == "revoked" and statuses[t3["invite"]["id"]] == "pending"
    # Expired.
    from analystos_api.models import Invite

    with app.state.aos.session_factory() as db:
        inv = db.get(Invite, t3["invite"]["id"])
        assert inv is not None
        inv.expires_at = inv.expires_at - timedelta(days=30)
        db.commit()
    with remote_client(app) as guest:
        r = guest.post(
            f"{API}/auth/invites/accept", json={"token": t3["token"], "name": "B", "password": "p" * 12}
        )
        assert r.status_code == 404
    assert {i["status"] for i in owner.get(f"{API}/workspaces/{ws}/invites").json()} >= {"expired", "revoked"}
    # Already a member; non-owners cannot invite.
    r = owner.post(f"{API}/workspaces/{ws}/invites", json={"email": "owner@example.com"})
    assert r.status_code == 409 and r.json()["code"] == "already_member"
    t4 = owner.post(
        f"{API}/workspaces/{ws}/invites", json={"email": "c@example.com", "role": "editor"}
    ).json()
    with remote_client(app) as editor:
        editor.post(
            f"{API}/auth/invites/accept", json={"token": t4["token"], "name": "C", "password": "p" * 12}
        )
        r = editor.post(f"{API}/workspaces/{ws}/invites", json={"email": "d@example.com"})
        assert r.status_code == 403 and r.json()["code"] == "insufficient_role"
        assert editor.get(f"{API}/workspaces/{ws}/invites").status_code == 403


def test_existing_account_accepts_with_session(tmp_path: Path) -> None:
    app, owner, ws = _owner(tmp_path)
    other_ws_owner = ApiClient(app)
    # A second person who already has an account (created through another workspace's invite).
    first = owner.post(f"{API}/workspaces/{ws}/invites", json={"email": "known@example.com"}).json()
    with remote_client(app) as known:
        known.post(
            f"{API}/auth/invites/accept", json={"token": first["token"], "name": "K", "password": "k" * 12}
        )
    ws2 = owner.post(f"{API}/workspaces", json={"name": "Second"}).json()["id"]
    inv = owner.post(
        f"{API}/workspaces/{ws2}/invites", json={"email": "known@example.com", "role": "editor"}
    ).json()
    with remote_client(app) as anon:
        p = anon.get(f"{API}/auth/invites/{inv['token']}").json()
        assert p["account_exists"] is True
        r = anon.post(
            f"{API}/auth/invites/accept", json={"token": inv["token"], "name": "K", "password": "x" * 12}
        )
        assert r.status_code == 409 and r.json()["code"] == "account_exists"
    with remote_client(app) as known:
        known.post(f"{API}/auth/login", json={"email": "known@example.com", "password": "k" * 12})
        r = known.post(f"{API}/auth/invites/accept", json={"token": inv["token"]})
        assert r.status_code == 200, r.text
        assert {w["id"] for w in known.get(f"{API}/workspaces").json()} == {ws, ws2}
    other_ws_owner.close()


def test_bad_invite_tokens_are_throttled(tmp_path: Path) -> None:
    app, _owner_client, _ws = _owner(tmp_path)
    with remote_client(app) as guest:
        codes = [guest.get(f"{API}/auth/invites/bogus-token-{i:04d}").status_code for i in range(21)]
        assert codes[:20] == [404] * 20 and codes[20] == 429
