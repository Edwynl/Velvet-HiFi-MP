import logging
import sqlite3
import threading
import time
from pathlib import Path
from queue import Empty, Queue

from settings import get_settings


settings = get_settings()
DB_PATH = settings.db_path

DB_POOL_SIZE = 200
POOL_USAGE_WARNING = 150
POOL_RECYCLE_THRESHOLD = 20
POOL_MAX_IDLE_TIME = 300

_db_pool = None
_pool_lock = threading.Lock()
_pool_recycle_lock = threading.Lock()
_pool_recovery_in_progress = False
_pool_last_recycle_time = time.time()

log = logging.getLogger("velvet")


def _create_db_connection(db_path: Path | None = None):
    """Create a new database connection with optimized settings."""
    target_path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(target_path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn


def _init_db_pool():
    """Initialize the database connection pool."""
    global _db_pool, _pool_last_recycle_time
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                _db_pool = Queue(maxsize=DB_POOL_SIZE)
                for _ in range(DB_POOL_SIZE):
                    _db_pool.put(_create_db_connection())
                _pool_last_recycle_time = time.time()
                log.warning(
                    "[DB] Connection pool initialized with %s connections",
                    DB_POOL_SIZE,
                )


def _recycle_pool():
    """Recycle the database connection pool with fresh connections."""
    global _db_pool, _pool_recovery_in_progress, _pool_last_recycle_time

    if _pool_recovery_in_progress:
        log.warning("[DB] Pool recovery already in progress, skipping")
        return

    with _pool_recycle_lock:
        if _pool_recovery_in_progress:
            return
        _pool_recovery_in_progress = True

    try:
        log.warning("[DB] Starting pool recycling...")
        old_pool = _db_pool
        _db_pool = Queue(maxsize=DB_POOL_SIZE)

        closed = 0
        while True:
            try:
                conn = old_pool.get_nowait()
                try:
                    conn.close()
                except Exception:
                    pass
                closed += 1
            except Empty:
                break

        for _ in range(DB_POOL_SIZE):
            _db_pool.put(_create_db_connection())

        _pool_last_recycle_time = time.time()
        log.warning(
            "[DB] Pool recycled: %s old connections closed, %s fresh connections created",
            closed,
            DB_POOL_SIZE,
        )
    except Exception as exc:
        log.error("[DB] Pool recycle failed: %s", exc)
    finally:
        _pool_recovery_in_progress = False


def _trigger_pool_recovery():
    """Trigger pool recovery if the pool is critically low."""
    if _db_pool is None:
        return

    remaining = _db_pool.qsize()
    if remaining <= POOL_RECYCLE_THRESHOLD and not _pool_recovery_in_progress:
        log.warning(
            "[DB] Pool critically low (%s/%s), triggering recovery",
            remaining,
            DB_POOL_SIZE,
        )
        recovery_thread = threading.Thread(target=_recycle_pool, daemon=True)
        recovery_thread.start()


def get_db():
    """Get a database connection from the pool."""
    if _db_pool is None:
        _init_db_pool()

    try:
        conn = _db_pool.get(timeout=30.0)
        remaining = _db_pool.qsize()
        if remaining < (DB_POOL_SIZE - POOL_USAGE_WARNING):
            log.warning("[DB] Pool getting low: %s/%s available", remaining, DB_POOL_SIZE)
            _trigger_pool_recovery()
        return conn
    except Empty:
        log.warning("[DB] Pool exhausted, creating temporary connection")
        _trigger_pool_recovery()
        return _create_db_connection()


def _return_db(conn):
    """Return a database connection to the pool."""
    if conn is None:
        return

    if _db_pool is not None and not _db_pool.full():
        try:
            conn.rollback()
            _db_pool.put_nowait(conn)
            return
        except Exception as exc:
            log.warning("[DB] Failed to return connection to pool: %s", exc)

    try:
        conn.close()
    except Exception:
        pass


class DatabaseConnection:
    """Context manager that returns pooled connections automatically."""

    def __init__(self):
        self.conn = None

    def __enter__(self):
        self.conn = get_db()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            _return_db(self.conn)
        return False


def get_pool_status() -> dict:
    """Return a snapshot of pool health for diagnostics."""
    available = 0
    initialized = _db_pool is not None
    if initialized:
        available = _db_pool.qsize()

    if not initialized:
        health = "uninitialized"
    elif available <= POOL_RECYCLE_THRESHOLD:
        health = "degraded"
    elif available <= (DB_POOL_SIZE - POOL_USAGE_WARNING):
        health = "warning"
    else:
        health = "healthy"

    return {
        "initialized": initialized,
        "available": available,
        "total": DB_POOL_SIZE,
        "recycling": _pool_recovery_in_progress,
        "recycle_threshold": POOL_RECYCLE_THRESHOLD,
        "usage_warning_threshold": DB_POOL_SIZE - POOL_USAGE_WARNING,
        "last_recycle_time": _pool_last_recycle_time,
        "health": health,
    }


def start_pool_recycle() -> dict:
    """Start background pool recycling if possible."""
    if _db_pool is None:
        return {"started": False, "reason": "uninitialized", "connections_before_recycle": 0}

    if _pool_recovery_in_progress:
        return {
            "started": False,
            "reason": "already_running",
            "connections_before_recycle": _db_pool.qsize(),
        }

    pool_size_before = _db_pool.qsize()
    recycle_thread = threading.Thread(target=_recycle_pool, daemon=True)
    recycle_thread.start()
    return {
        "started": True,
        "reason": "started",
        "connections_before_recycle": pool_size_before,
    }
