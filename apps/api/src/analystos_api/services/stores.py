"""Per-workspace DuckDB stores (one ``warehouse.duckdb`` file per workspace).

Stores are opened lazily and cached per workspace. Writes (ingest) are serialised per
workspace with a lock; reads go through ``WorkspaceStore.execute_read`` which enforces the
read-only SQL policy, the row cap and the timeout.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..config import Settings


class StoreRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stores: dict[str, Any] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def workspace_dir(self, workspace_id: str) -> Path:
        path = self._settings.workspace_dir(workspace_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def uploads_dir(self, workspace_id: str) -> Path:
        path = self.workspace_dir(workspace_id) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def exports_dir(self, workspace_id: str) -> Path:
        path = self.workspace_dir(workspace_id) / "exports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def lock(self, workspace_id: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(workspace_id, threading.RLock())

    def get(self, workspace_id: str) -> Any:
        """Return the engine ``WorkspaceStore`` for a workspace (opening it on first use)."""
        with self._guard:
            store = self._stores.get(workspace_id)
            if store is None:
                from analystos_engine.store import WorkspaceStore

                store = WorkspaceStore(self.workspace_dir(workspace_id))
                self._stores[workspace_id] = store
            return store

    @contextmanager
    def writing(self, workspace_id: str) -> Iterator[Any]:
        with self.lock(workspace_id):
            yield self.get(workspace_id)

    def close(self, workspace_id: str) -> None:
        with self._guard:
            store = self._stores.pop(workspace_id, None)
        if store is not None and hasattr(store, "close"):
            store.close()

    def destroy(self, workspace_id: str) -> None:
        self.close(workspace_id)
        path = self._settings.workspace_dir(workspace_id)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def close_all(self) -> None:
        for ws in list(self._stores):
            self.close(ws)
