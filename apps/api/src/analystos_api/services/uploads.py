"""Safe handling of uploaded files.

* The upload is streamed to disk with a hard byte cap (``AOS_MAX_UPLOAD_MB``); the request
  is rejected as soon as the cap is exceeded.
* The extension must be one of the supported formats *and* the content must match that
  format's signature (magic bytes / text sniff). A renamed binary is rejected.
* Files are stored under ``DATA_DIR/workspaces/{workspace_id}/uploads/{upload_id}/`` with a
  sanitised name, permissions 0600, and are only ever *read* by the ingest code (pyarrow,
  pandas, openpyxl with ``data_only=True``). Nothing uploaded is ever executed or imported.
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..errors import PayloadTooLarge, Unprocessable

EXTENSION_KINDS = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".json": "json",
    ".ndjson": "json",
    ".jsonl": "json",
}

_CHUNK = 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]+")


@dataclass
class StoredUpload:
    path: Path
    size_bytes: int
    sha256: str
    file_kind: str
    safe_name: str


def safe_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/")).strip() or "upload"
    cleaned = _SAFE_NAME.sub("_", base).lstrip(".") or "upload"
    return cleaned[:200]


def kind_for_filename(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".xls":
        raise Unprocessable(
            "Legacy .xls workbooks are not supported; save the file as .xlsx and upload again",
            code="unsupported_file_type",
        )
    kind = EXTENSION_KINDS.get(ext)
    if kind is None:
        raise Unprocessable(
            f"Unsupported file type {ext or '(none)'}; supported: {', '.join(sorted(EXTENSION_KINDS))}",
            code="unsupported_file_type",
        )
    return kind


def sniff_matches(path: Path, kind: str) -> tuple[bool, str]:
    """Check file content against the claimed format. Returns (ok, reason)."""
    with path.open("rb") as fh:
        head = fh.read(8192)
        if kind == "parquet":
            if not head.startswith(b"PAR1"):
                return False, "missing Parquet header"
            fh.seek(-4, os.SEEK_END)
            return (fh.read(4) == b"PAR1"), "missing Parquet footer"
    if kind == "excel":
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            return False, "legacy/encrypted OLE2 workbook; save as .xlsx"
        if not head.startswith(b"PK\x03\x04"):
            return False, "not a ZIP-based .xlsx workbook"
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if len(names) > 10_000:
                    return False, "workbook contains too many parts"
                total = sum(i.file_size for i in zf.infolist())
                if total > 4 * 1024 * 1024 * 1024:
                    return False, "workbook expands to more than 4 GB (possible zip bomb)"
        except zipfile.BadZipFile:
            return False, "corrupt ZIP container"
        if not any(n.startswith("xl/") for n in names):
            return False, "ZIP file is not an Excel workbook"
        return True, ""
    if b"\x00" in head and not _is_utf16(head):
        return False, "binary content in a text file"
    if kind == "json":
        text = head.lstrip(b"\xef\xbb\xbf").lstrip()
        if _is_utf16(head):
            return True, ""
        if text[:1] not in (b"[", b"{"):
            return False, "JSON must start with '[' or '{'"
        return True, ""
    # csv / text
    if not head.strip():
        return False, "file is empty"
    return True, ""


def _is_utf16(head: bytes) -> bool:
    return head.startswith((b"\xff\xfe", b"\xfe\xff"))


def store_upload(stream: BinaryIO, filename: str, target_dir: Path, max_bytes: int) -> StoredUpload:
    kind = kind_for_filename(filename)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(filename)
    path = target_dir / name
    digest = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise PayloadTooLarge(f"File exceeds the upload limit of {max_bytes // (1024 * 1024)} MB")
                digest.update(chunk)
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise Unprocessable("The uploaded file is empty", code="empty_file")
    ok, reason = sniff_matches(path, kind)
    if not ok:
        path.unlink(missing_ok=True)
        raise Unprocessable(f"File content does not match its extension: {reason}", code="content_mismatch")
    return StoredUpload(path=path, size_bytes=size, sha256=digest.hexdigest(), file_kind=kind, safe_name=name)
