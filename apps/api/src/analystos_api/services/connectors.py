"""Bridge between stored ``DataSource`` rows and engine connectors.

Credentials are decrypted only in memory, only for the duration of a call, and never
logged or returned. Engine connector errors are scrubbed of secret values before they
reach the API caller.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..errors import ApiError, Unprocessable
from ..logs import redact
from ..models import DataSource
from ..security.secrets_box import SecretBox, SecretDecryptionError

KIND_LABELS = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "sqlserver": "SQL Server",
    "snowflake": "Snowflake",
    "bigquery": "BigQuery",
    "duckdb": "DuckDB file",
}

_COMMON_SECRET_NAMES = {
    "password",
    "private_key",
    "private_key_passphrase",
    "credentials_json",
    "service_account_json",
    "service_account_info",
    "token",
    "access_token",
    "client_secret",
    "passphrase",
}


class ConnectorFailure(ApiError):
    status_code = 502
    code = "connector_error"


def secret_field_names(kind: str) -> list[str]:
    """Secret fields for a connector kind, from the engine's JSON schema (writeOnly/password)."""
    from analystos_engine.connectors.base import connector_config_schema

    schema = connector_config_schema(kind)
    props: dict[str, Any] = schema.get("properties", {}) if isinstance(schema, dict) else {}
    out = [
        name
        for name, prop in props.items()
        if isinstance(prop, dict)
        and (prop.get("writeOnly") or prop.get("format") == "password" or name in _COMMON_SECRET_NAMES)
    ]
    if isinstance(schema, dict):
        for name in schema.get("x-secret-fields", []) or []:
            if name not in out:
                out.append(name)
    return out


def scrub(message: str, secrets: dict[str, Any]) -> str:
    out = message
    for value in secrets.values():
        if isinstance(value, str) and len(value) >= 3:
            out = out.replace(value, "***")
    return str(redact(out))


def _decrypt(box: SecretBox, ds: DataSource) -> dict[str, Any]:
    try:
        return box.decrypt(ds.secrets_encrypted)
    except SecretDecryptionError as exc:
        raise Unprocessable(str(exc), code="secret_decryption_failed") from exc


def _describe_invalid(exc: Exception) -> str:
    """Describe a settings error without echoing submitted values (pydantic includes ``input_value``)."""
    import re

    from pydantic import ValidationError

    for err in (exc, exc.__cause__, exc.__context__):
        if isinstance(err, ValidationError):
            parts = [
                f"{'.'.join(str(p) for p in e.get('loc', ())) or 'settings'}: {e.get('msg', '')}"
                for e in err.errors(include_input=False, include_url=False)
            ]
            return "; ".join(parts) or "invalid settings"
    text = re.sub(r"input_value=.*?(?=, input_type=|\])", "input_value=<redacted>", str(exc), flags=re.S)
    return re.sub(r"\s*For further information visit \S+", "", text)


def check_no_secrets_in_config(kind: str, config: dict[str, Any]) -> None:
    leaked = set(config) & set(secret_field_names(kind))
    if leaked:
        raise Unprocessable(
            f"Secret fields must be sent in 'secrets', not 'config': {sorted(leaked)}",
            code="secret_in_config",
        )


@contextmanager
def open_connector(
    box: SecretBox, kind: str, config: dict[str, Any], secrets: dict[str, Any]
) -> Iterator[Any]:
    from analystos_engine.connectors.base import ConnectorError, get_connector

    try:
        conn = get_connector(kind, config, secrets)
    except ConnectorError as exc:
        raise ConnectorFailure(scrub(str(exc), secrets)) from exc
    except (ValueError, TypeError) as exc:
        raise Unprocessable(
            f"Invalid connection settings: {scrub(_describe_invalid(exc), secrets)}",
            code="invalid_connector_config",
        ) from exc
    try:
        yield conn
    except ConnectorError as exc:
        raise ConnectorFailure(scrub(str(exc), secrets)) from exc
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):  # closing must not mask the real error
                close()


@contextmanager
def connector_for(box: SecretBox, ds: DataSource) -> Iterator[Any]:
    secrets = _decrypt(box, ds)
    with open_connector(box, ds.kind, dict(ds.config or {}), secrets) as conn:
        yield conn


def test_connection(
    box: SecretBox, kind: str, config: dict[str, Any], secrets: dict[str, Any]
) -> dict[str, Any]:
    from analystos_engine.connectors.base import ConnectorError

    try:
        with open_connector(box, kind, config, secrets) as conn:
            result = conn.test()
    except (ConnectorFailure, Unprocessable) as exc:
        return {"ok": False, "message": exc.detail, "details": {}}
    except ConnectorError as exc:
        return {"ok": False, "message": scrub(str(exc), secrets), "details": {}}
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    data["message"] = scrub(str(data.get("message", "")), secrets)
    return data


def decrypt_secrets(box: SecretBox, ds: DataSource) -> dict[str, Any]:
    return _decrypt(box, ds)
