"""Centralized SQLite connection manager for WOS Bot.

Usage:
    from .database import DatabaseManager
    db = DatabaseManager.instance()
    conn = db.get("settings")  # returns connection to db/settings.sqlite
"""

import os
import sqlite3
import threading


_DB_REGISTRY = {
    "alliance": "db/alliance.sqlite",
    "giftcode": "db/giftcode.sqlite",
    "settings": "db/settings.sqlite",
    "users": "db/users.sqlite",
    "changes": "db/changes.sqlite",
    "beartime": "db/beartime.sqlite",
    "backup": "db/backup.sqlite",
    "id_channel": "db/id_channel.sqlite",
}


class DatabaseManager:
    """Singleton connection manager. All connections use WAL mode and timeout=30."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._connections: dict[str, sqlite3.Connection] = {}
        self._conn_lock = threading.Lock()
        os.makedirs("db", exist_ok=True)

    @classmethod
    def instance(cls) -> "DatabaseManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get(self, name: str) -> sqlite3.Connection:
        """Return a cached connection for the given database name."""
        if name not in _DB_REGISTRY:
            raise ValueError(f"Unknown database: {name!r}. Known: {list(_DB_REGISTRY)}")
        with self._conn_lock:
            conn = self._connections.get(name)
            if conn is None:
                path = _DB_REGISTRY[name]
                conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                self._connections[name] = conn
            return conn

    def close_all(self):
        """Close all managed connections (call at bot shutdown)."""
        with self._conn_lock:
            for name, conn in self._connections.items():
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
