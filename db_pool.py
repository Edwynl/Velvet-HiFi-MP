import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from settings import get_settings


settings = get_settings()
DB_PATH = settings.db_path

CPU_COUNT = os.cpu_count() or 4
DB_POOL_SIZE = max(4, min(CPU_COUNT * 2, 16))
POOL_USAGE_WARNING = max(2, DB_POOL_SIZE - 4)
POOL_RECYCLE_THRESHOLD = max(2, DB_POOL_SIZE // 4)
POOL_MAX_IDLE_TIME = 300

_thread_local = threading.local()
_registry_lock = threading.Lock()
_pool_recycle_lock = threading.Lock()
_pool_recovery_in_progress = False
_pool_last_recycle_time = time.time()
_connection_registry: dict[int, dict] = {}

log = logging.getLogger("velvet")


class _ManagedSQLiteConnection(sqlite3.Connection):
    """SQLite connection that treats .close() as pool release.

    Existing application code calls `db.close()` in many places.
    With thread-local pooling, we keep the physical connection alive and
    close it only during recycle/cleanup via `force_close()`.
    """

    def close(self):  # type: ignore[override]
        return None

    def force_close(self) -> None:
        super().close()


def _create_db_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Create a thread-owned SQLite connection with tuned pragmas."""
    target_path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(
        str(target_path),
        timeout=30.0,
        factory=_ManagedSQLiteConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn


def _get_thread_connection() -> sqlite3.Connection | None:
    conn = getattr(_thread_local, "connection", None)
    return conn


def _set_thread_connection(conn: sqlite3.Connection) -> None:
    _thread_local.connection = conn
    thread_id = threading.get_ident()
    with _registry_lock:
        _connection_registry[thread_id] = {
            "connection": conn,
            "last_used": time.time(),
            "stale": False,
        }


def _is_connection_healthy(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _close_current_thread_connection() -> None:
    conn = _get_thread_connection()
    if conn is not None:
        try:
            if hasattr(conn, "force_close"):
                conn.force_close()  # type: ignore[attr-defined]
            else:
                conn.close()
        except Exception:
            pass

    if hasattr(_thread_local, "connection"):
        _thread_local.connection = None

    thread_id = threading.get_ident()
    with _registry_lock:
        _connection_registry.pop(thread_id, None)


def _is_current_thread_connection_stale() -> bool:
    thread_id = threading.get_ident()
    with _registry_lock:
        info = _connection_registry.get(thread_id)
        return bool(info and info.get("stale"))


def get_db() -> sqlite3.Connection:
    """Return the current thread's connection, creating/replacing as needed."""
    conn = _get_thread_connection()
    if conn is not None:
        if _is_current_thread_connection_stale() or not _is_connection_healthy(conn):
            _close_current_thread_connection()
        else:
            thread_id = threading.get_ident()
            with _registry_lock:
                if thread_id in _connection_registry:
                    _connection_registry[thread_id]["last_used"] = time.time()
            return conn

    conn = _create_db_connection()
    _set_thread_connection(conn)

    active_connections = len(_connection_registry)
    if active_connections > POOL_USAGE_WARNING:
        log.warning("[DB] High active thread connections: %s/%s", active_connections, DB_POOL_SIZE)

    return conn


def _return_db(conn: sqlite3.Connection | None) -> None:
    """Compatibility no-op for thread-local connections.

    We keep the thread-owned connection alive for reuse and only close
    non-thread-owned/invalid connections.
    """
    if conn is None:
        return

    current_thread_conn = _get_thread_connection()
    if current_thread_conn is conn:
        if not _is_connection_healthy(conn):
            _close_current_thread_connection()
        return

    try:
        if hasattr(conn, "force_close"):
            conn.force_close()  # type: ignore[attr-defined]
        else:
            conn.close()
    except Exception:
        pass


class DatabaseConnection:
    """Context manager for obtaining and returning db connections."""

    def __init__(self):
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = get_db()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and self.conn is not None:
            try:
                self.conn.rollback()
            except Exception:
                pass
        if self.conn is not None:
            _return_db(self.conn)
        return False


def get_pool_status() -> dict:
    """Return compatibility health snapshot for diagnostics endpoints."""
    with _registry_lock:
        active = len(_connection_registry)

    initialized = active > 0
    available = max(DB_POOL_SIZE - active, 0)

    if not initialized:
        health = "uninitialized"
    elif active >= DB_POOL_SIZE:
        health = "degraded"
    elif active >= POOL_USAGE_WARNING:
        health = "warning"
    else:
        health = "healthy"

    return {
        "initialized": initialized,
        "available": available,
        "total": DB_POOL_SIZE,
        "recycling": _pool_recovery_in_progress,
        "recycle_threshold": POOL_RECYCLE_THRESHOLD,
        "usage_warning_threshold": POOL_USAGE_WARNING,
        "last_recycle_time": _pool_last_recycle_time,
        "health": health,
    }


def _recycle_pool() -> None:
    global _pool_recovery_in_progress, _pool_last_recycle_time

    with _pool_recycle_lock:
        if _pool_recovery_in_progress:
            return
        _pool_recovery_in_progress = True

    try:
        with _registry_lock:
            entries = list(_connection_registry.values())
            _connection_registry.clear()

        closed = 0
        for info in entries:
            try:
                conn = info["connection"]
                if hasattr(conn, "force_close"):
                    conn.force_close()  # type: ignore[attr-defined]
                else:
                    conn.close()
                closed += 1
            except Exception:
                pass

        # If recycle is initiated from current thread, clear its stale handle.
        if hasattr(_thread_local, "connection"):
            _thread_local.connection = None

        _pool_last_recycle_time = time.time()
        log.warning("[DB] Recycled %s thread-local connection(s)", closed)
    finally:
        _pool_recovery_in_progress = False


def _mark_idle_connections_stale() -> None:
    while True:
        time.sleep(60)
        now = time.time()
        with _registry_lock:
            for info in _connection_registry.values():
                if now - info["last_used"] > POOL_MAX_IDLE_TIME:
                    info["stale"] = True


def start_pool_recycle() -> dict:
    """Trigger async recycle and keep original endpoint contract."""
    with _registry_lock:
        active = len(_connection_registry)

    if active == 0:
        return {"started": False, "reason": "uninitialized", "connections_before_recycle": 0}

    if _pool_recovery_in_progress:
        return {
            "started": False,
            "reason": "already_running",
            "connections_before_recycle": active,
        }

    recycle_thread = threading.Thread(target=_recycle_pool, daemon=True, name="DBPoolRecycle")
    recycle_thread.start()
    return {
        "started": True,
        "reason": "started",
        "connections_before_recycle": active,
    }


_cleanup_thread = threading.Thread(target=_mark_idle_connections_stale, daemon=True, name="DBPoolIdleCleanup")
_cleanup_thread.start()
