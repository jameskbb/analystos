"""Authentication: server-side sessions, local single-user mode and API tokens.

Cookie model
------------
* ``aos_session``: opaque 256-bit token, HttpOnly, SameSite=Lax, Path=/. The database only
  stores its SHA-256 digest.
* ``aos_csrf``: per-session CSRF token, readable by JavaScript (not HttpOnly), SameSite=Lax.
  Unsafe requests (POST/PUT/PATCH/DELETE) authenticated by cookie must echo it in the
  ``X-CSRF-Token`` header (double-submit). The header is compared against the value stored
  server-side for the session, so a forged cookie is useless.
* Bearer API tokens (``Authorization: Bearer aos_...``) are not subject to CSRF because
  browsers never attach them automatically.

Security model for local mode (review finding R-01)
---------------------------------------------------
Local mode (``AUTH_MODE=local``, or ``auto`` before the first password account exists) signs
requests in as the "Local Analyst" without a password. It exists only when ``AOS_ENV=development``
(any other environment behaves as ``password`` mode) and only for requests from this machine, i.e.
when *all* of these hold:

* the TCP peer is a loopback address (127.0.0.0/8, ::1);
* the ``Host`` header (and ``X-Forwarded-Host`` when a local proxy sets it) names a loopback host:
  ``localhost``, ``*.localhost``, ``127.x.x.x`` or ``[::1]`` (blocks DNS-rebinding pages);
* there is no ``X-AOS-Client-Addr`` header, or it is authenticated by ``X-AOS-Proxy-Secret`` equal
  to ``AOS_PROXY_SECRET`` and names a loopback client. The web server's proxy sends both, so a
  remote browser reaching a proxy on this machine is recognised as remote. An unauthenticated or
  non-loopback proxy header disables local mode for that request.

Anything else gets ``authenticated: false, local_mode_denied: true``, and existing local sessions
are refused from non-local requests. ``AOS_ALLOW_INSECURE_LOCAL=true`` is the explicit opt-in to
drop these checks (e.g. a single-user container behind a trusted proxy); without it the server
refuses to start with ``AUTH_MODE=auto|local`` when ``AOS_ENV=production``.

Creating the first password account in ``auto`` mode needs proof of local access (a valid local
session cookie from this machine) or the ``AOS_BOOTSTRAP_TOKEN``; only then does the new
account take over the local analyst's workspaces, and every local session is revoked. Without
that proof a sign-up creates a fresh account with no workspaces, or is refused in ``auto`` mode.
After the first account exists, people join through workspace invites; open sign-up is an escape
hatch (``AOS_ALLOW_SIGNUP=true``, default false).
"""

from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import utcnow
from ..models import ApiToken, AuthSession, Membership, User, Workspace
from ..security.tokens import constant_time_equals, hash_token, new_token

SESSION_COOKIE = "aos_session"
CSRF_COOKIE = "aos_csrf"
CSRF_HEADER = "X-CSRF-Token"
LOCAL_USER_EMAIL = "local@analystos.local"
LOCAL_USER_NAME = "Local Analyst"


@dataclass
class Principal:
    user: User
    via: str  # "session" | "token"
    session: AuthSession | None = None
    token: ApiToken | None = None

    @property
    def token_workspace_id(self) -> str | None:
        return self.token.workspace_id if self.token else None

    @property
    def read_only(self) -> bool:
        return bool(self.token and self.token.read_only)


def password_users_exist(db: Session) -> bool:
    return (
        db.scalar(select(User.id).where(User.password_hash.is_not(None), User.is_local.is_(False)).limit(1))
        is not None
    )


def effective_auth_mode(db: Session, settings: Settings) -> str:
    if not settings.local_mode_allowed():
        return "password"  # AOS_ENV is not development: local mode is off unless AOS_ALLOW_INSECURE_LOCAL
    if settings.auth_mode == "auto":
        return "password" if password_users_exist(db) else "local"
    return settings.auth_mode


def ensure_local_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.is_local.is_(True)).limit(1))
    if user is None:
        user = User(email=LOCAL_USER_EMAIL, name=LOCAL_USER_NAME, is_local=True)
        db.add(user)
        db.flush()
    return user


def create_session(
    db: Session, settings: Settings, user: User, user_agent: str | None, ip: str | None
) -> tuple[str, AuthSession]:
    raw = new_token()
    session = AuthSession(
        id=hash_token(raw),
        user_id=user.id,
        csrf_token=new_token(24),
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
        user_agent=(user_agent or "")[:400],
        ip=ip,
    )
    db.add(session)
    user.last_login_at = utcnow()
    db.flush()
    return raw, session


def set_session_cookies(response: Response, settings: Settings, raw_token: str, session: AuthSession) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        max_age=max_age,
        httponly=False,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", samesite="lax", secure=settings.cookie_secure, httponly=True
    )
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="lax", secure=settings.cookie_secure)


def lookup_session(db: Session, raw_token: str | None) -> AuthSession | None:
    if not raw_token:
        return None
    sess = db.get(AuthSession, hash_token(raw_token))
    if sess is None or sess.revoked_at is not None or sess.expires_at <= utcnow():
        return None
    return sess


def lookup_api_token(db: Session, raw_token: str) -> ApiToken | None:
    tok = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw_token)))
    if tok is None or tok.revoked_at is not None:
        return None
    if tok.expires_at is not None and tok.expires_at <= utcnow():
        return None
    return tok


def claim_local_user(db: Session, email: str, name: str, password_hash: str) -> User | None:
    """Convert the local analyst into a password account, keeping its workspaces.

    Callers must have verified proof of local access (a local session from this machine) or the
    bootstrap token first. Every session of the local analyst is revoked; the caller creates a
    fresh one for the new account.
    """
    local = db.scalar(select(User).where(User.is_local.is_(True)).limit(1))
    if local is None:
        return None
    local.email = email
    local.name = name
    local.password_hash = password_hash
    local.is_local = False
    revoke_sessions(db, local.id)
    db.flush()
    return local


def revoke_sessions(db: Session, user_id: str, *, keep: str | None = None) -> int:
    """Revoke every active session of ``user_id`` except ``keep`` (a session id)."""
    stmt = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep is not None:
        stmt = stmt.where(AuthSession.id != keep)
    return int(db.execute(stmt.values(revoked_at=utcnow())).rowcount or 0)  # type: ignore[attr-defined]


def revoke_tokens(db: Session, user_id: str) -> int:
    stmt = update(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
    return int(db.execute(stmt.values(revoked_at=utcnow())).rowcount or 0)  # type: ignore[attr-defined]


# ------------------------------------------------------------------------------ local access


def _is_loopback_ip(host: str | None) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _is_loopback_hostname(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:3000
        host = host[1 : host.find("]")] if "]" in host else host[1:]
        return _is_loopback_ip(host)
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if host == "localhost" or host.endswith(".localhost"):
        return True
    return _is_loopback_ip(host)


PROXY_CLIENT_HEADER = "X-AOS-Client-Addr"
PROXY_SECRET_HEADER = "X-AOS-Proxy-Secret"


def _proxy_client_ok(request: Request, settings: Settings) -> bool:
    """No proxy header, or an authenticated one (``X-AOS-Proxy-Secret`` == ``AOS_PROXY_SECRET``) whose client
    address (``X-AOS-Client-Addr``) is loopback. An unauthenticated or non-loopback header means not local."""
    client = request.headers.get(PROXY_CLIENT_HEADER)
    if client is None:
        return True
    if not _proxy_secret_ok(request, settings):
        return False
    addrs = [a.strip() for a in client.split(",") if a.strip()]
    return bool(addrs) and all(_is_loopback_ip(a) for a in addrs)


def _proxy_secret_ok(request: Request, settings: Settings) -> bool:
    secret = settings.proxy_secret.get_secret_value() if settings.proxy_secret else ""
    return bool(secret) and constant_time_equals(request.headers.get(PROXY_SECRET_HEADER), secret)


def client_address(request: Request, settings: Settings) -> str | None:
    """The address to attribute a request to (login throttling, audit, logs).

    Behind the web server's proxy every request arrives from the proxy's address, so per-address throttling
    would lump all browsers together and one attacker could lock everyone out. When ``X-AOS-Client-Addr`` is
    authenticated by ``X-AOS-Proxy-Secret`` its first address (the browser) is used; otherwise, including an
    unauthenticated or malformed header, the socket peer is used, so the header cannot be spoofed."""
    peer = request.client.host if request.client else None
    header = request.headers.get(PROXY_CLIENT_HEADER)
    if header is None or not _proxy_secret_ok(request, settings):
        return peer
    first = header.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(first.strip("[]")))
    except ValueError:
        return peer


def is_local_request(request: Request, settings: Settings) -> bool:
    """True when the request provably comes from this machine (see the module docstring)."""
    if settings.allow_insecure_local:
        return True
    if not settings.local_mode_allowed():
        return False
    peer = request.client.host if request.client else None
    if not _is_loopback_ip(peer):
        return False
    if not _is_loopback_hostname(request.headers.get("host")):
        return False
    fwd = request.headers.get("x-forwarded-host")
    if fwd is not None and not all(_is_loopback_hostname(h) for h in fwd.split(",")):
        return False
    return _proxy_client_ok(request, settings)


# ------------------------------------------------------------------------------ login throttling


@dataclass
class _Counter:
    failures: int = 0
    locked_until: float = 0.0
    last_failure: float = 0.0


@dataclass
class LoginThrottle:
    """Per-account and per-IP failed-login throttling with exponential backoff (in-process).

    After ``max_failures`` consecutive failures a key is locked for ``base_s``; each further
    failure doubles the lock up to ``max_s``. A successful login clears the account key. Counters
    expire after an hour without failures. Per-IP limits allow 4x as many failures, so one address
    cannot probe many accounts. State lives in this process (documented limitation: several API
    processes each keep their own counters).
    """

    max_failures: int = 5
    base_s: float = 30.0
    max_s: float = 900.0
    _counters: dict[str, _Counter] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    clock: object = time.monotonic

    def _now(self) -> float:
        return float(self.clock())  # type: ignore[operator]

    def _limit(self, key: str) -> int:
        return self.max_failures * (4 if key.startswith("ip:") else 1)

    def retry_after(self, *keys: str) -> float:
        now = self._now()
        with self._lock:
            waits = [c.locked_until - now for k in keys if (c := self._counters.get(k)) is not None]
        return max([0.0, *waits])

    def failure(self, *keys: str) -> float:
        now = self._now()
        wait = 0.0
        with self._lock:
            for key in keys:
                c = self._counters.get(key)
                if c is None or now - c.last_failure > 3600:
                    c = self._counters[key] = _Counter()
                c.failures += 1
                c.last_failure = now
                over = c.failures - self._limit(key)
                if over >= 0:
                    lock = min(self.max_s, self.base_s * (2**over))
                    c.locked_until = now + lock
                    wait = max(wait, lock)
            if len(self._counters) > 50_000:  # bound memory under a spray attack
                for k in [k for k, c in self._counters.items() if now - c.last_failure > 3600]:
                    self._counters.pop(k, None)
        return wait

    def success(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._counters.pop(key, None)


def default_workspace_for(db: Session, user: User) -> Workspace | None:
    return db.scalar(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
        .limit(1)
    )
