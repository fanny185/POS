# ============================================================
#  RetailPOS  –  Background Sync Engine
#
#  Runs in a daemon thread inside the POS application.
#  Every SYNC_INTERVAL_SECONDS it:
#    1. Checks for internet / Supabase connectivity
#    2. Pushes any unsynced products, sales, and sale_items
#       to the Supabase (PostgreSQL) cloud database
#    3. Updates the status label in the nav bar
#
#  The local SQLite database is ALWAYS the source of truth.
#  Supabase is a read replica for the owner's web dashboard.
# ============================================================

import sqlite3
import threading
import traceback
from datetime import datetime

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

import config

# ------------------------------------------------------------------
# Status tracking  (read by the UI thread)
# ------------------------------------------------------------------
_status = {
    "state":       "disabled",   # disabled | offline | syncing | ok | error
    "last_sync":   None,         # datetime of last successful sync
    "pending":     0,            # unsynced rows waiting to be pushed
    "last_error":  "",           # last error message
}
_lock      = threading.Lock()
_stop      = threading.Event()
_wake      = threading.Event()   # set to trigger an immediate sync
_callbacks = []                  # UI functions to call when status changes


def get_status():
    with _lock:
        return dict(_status)


def on_status_change(fn):
    """Register a callback that runs (in the sync thread) on status change."""
    _callbacks.append(fn)


def _set_status(**kwargs):
    with _lock:
        _status.update(kwargs)
    for fn in _callbacks:
        try:
            fn()
        except Exception:
            pass


# ------------------------------------------------------------------
# Local DB helpers
# ------------------------------------------------------------------
DB_FILE = "pos.db"


def _local_conn():
    """Open a WAL-mode connection with foreign keys enforced."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_sync_columns():
    """Add synced=0 column to tables that need it, and create deleted_products table (idempotent)."""
    conn = _local_conn()
    cur  = conn.cursor()
    for table in ("products", "sales", "sale_items"):
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        if "synced" not in cols:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN synced INTEGER NOT NULL DEFAULT 0"
            )
    # Track deleted product IDs so the sync can remove them from Supabase
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deleted_products (
            product_id INTEGER PRIMARY KEY,
            deleted_at TEXT NOT NULL,
            synced     INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _count_pending():
    conn = _local_conn()
    cur  = conn.cursor()
    total = 0
    for table in ("products", "sales", "sale_items"):
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE synced = 0")
            total += cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass
    try:
        cur.execute("SELECT COUNT(*) FROM deleted_products WHERE synced = 0")
        total += cur.fetchone()[0]
    except sqlite3.OperationalError:
        pass
    conn.close()
    return total


# ------------------------------------------------------------------
# Supabase REST helpers
# ------------------------------------------------------------------
def _headers():
    return {
        "apikey":        config.SUPABASE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",
    }


def _upsert(table, rows, timeout=10):
    """POST a list of dicts to a Supabase table (upsert by primary key)."""
    if not rows:
        return
    url  = f"{config.SUPABASE_URL}/rest/v1/{table}"
    resp = requests.post(url, json=rows, headers=_headers(), timeout=timeout)
    resp.raise_for_status()


def _check_connectivity(timeout=5):
    """Return True if Supabase is reachable."""
    try:
        url  = f"{config.SUPABASE_URL}/rest/v1/sales?limit=1"
        resp = requests.get(url, headers=_headers(), timeout=timeout)
        return resp.status_code in (200, 206)
    except Exception:
        return False


# ------------------------------------------------------------------
# Sync logic
# ------------------------------------------------------------------
def _sync_once():
    """Push all unsynced rows.  Called from the background thread.
    Commits each table's synced=1 flags immediately after a successful
    upsert so a crash mid-sync only re-pushes the remaining tables."""
    conn = _local_conn()
    cur  = conn.cursor()
    pushed = 0

    try:
        # ---- products ----
        cur.execute("""
            SELECT product_id, product_name, quantity, selling_price,
                   ordering_price, units_per_pack
            FROM products WHERE synced = 0
        """)
        products = [dict(r) for r in cur.fetchall()]
        if products:
            _upsert("products", products)
            ids = [p["product_id"] for p in products]
            cur.executemany(
                "UPDATE products SET synced=1 WHERE product_id=?",
                [(i,) for i in ids]
            )
            conn.commit()
            pushed += len(products)

        # ---- sales ----
        cur.execute("""
            SELECT sale_id, sale_date, total_amount, amount_received,
                   change_given, status, voided_at, void_reason
            FROM sales WHERE synced = 0
        """)
        sales = [dict(r) for r in cur.fetchall()]
        if sales:
            _upsert("sales", sales)
            ids = [s["sale_id"] for s in sales]
            cur.executemany(
                "UPDATE sales SET synced=1 WHERE sale_id=?",
                [(i,) for i in ids]
            )
            conn.commit()
            pushed += len(sales)

        # ---- sale_items ----
        cur.execute("""
            SELECT si.sale_item_id, si.sale_id, si.product_id,
                   si.product_name, si.quantity, si.unit_price,
                   si.line_total, si.unit_cost
            FROM sale_items si
            WHERE si.synced = 0
        """)
        items = [dict(r) for r in cur.fetchall()]
        if items:
            _upsert("sale_items", items)
            ids = [i["sale_item_id"] for i in items]
            cur.executemany(
                "UPDATE sale_items SET synced=1 WHERE sale_item_id=?",
                [(i,) for i in ids]
            )
            conn.commit()
            pushed += len(items)

        # ---- deleted products ----
        try:
            cur.execute("""
                SELECT product_id FROM deleted_products WHERE synced = 0
            """)
            deleted_ids = [r["product_id"] for r in cur.fetchall()]
            for pid in deleted_ids:
                try:
                    requests.delete(
                        f"{config.SUPABASE_URL}/rest/v1/products?product_id=eq.{pid}",
                        headers={**_headers(), "Prefer": ""},
                        timeout=10
                    )
                except Exception:
                    pass
            if deleted_ids:
                cur.executemany(
                    "UPDATE deleted_products SET synced=1 WHERE product_id=?",
                    [(i,) for i in deleted_ids]
                )
                conn.commit()
                pushed += len(deleted_ids)
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet on older installs

        return pushed

    finally:
        conn.close()


# ------------------------------------------------------------------
# Background thread entry point
# ------------------------------------------------------------------
def _run():
    _ensure_sync_columns()

    backoff = config.SYNC_INTERVAL_SECONDS  # seconds to wait on error
    MAX_BACKOFF = 300                        # cap at 5 minutes

    while not _stop.is_set():
        _wake.clear()

        if not config.is_configured():
            _set_status(state="disabled", pending=0,
                        last_error="Supabase not configured")
            _wake.wait(config.SYNC_INTERVAL_SECONDS)
            continue

        if not REQUESTS_AVAILABLE:
            _set_status(state="error",
                        last_error="'requests' library not installed")
            _wake.wait(config.SYNC_INTERVAL_SECONDS)
            continue

        try:
            pending = _count_pending()
            _set_status(pending=pending)

            if not _check_connectivity():
                _set_status(state="offline",
                            last_error="Cannot reach Supabase")
                _wake.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            backoff = config.SYNC_INTERVAL_SECONDS  # reset on success

            if pending > 0:
                _set_status(state="syncing")
                pushed = _sync_once()
                _set_status(
                    state="ok",
                    last_sync=datetime.now(),
                    pending=max(0, pending - pushed),
                    last_error=""
                )
            else:
                _set_status(state="ok", last_error="")

        except Exception as exc:
            _set_status(state="error", last_error=str(exc))
            traceback.print_exc()
            backoff = min(backoff * 2, MAX_BACKOFF)

        _wake.wait(config.SYNC_INTERVAL_SECONDS)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------
_thread = None


def start():
    """Start the sync background thread.  Call once at app startup."""
    global _thread
    _ensure_sync_columns()
    _thread = threading.Thread(target=_run, daemon=True, name="SyncThread")
    _thread.start()


def stop():
    """Signal the sync thread to stop.
    Flushes any pending rows synchronously before returning so the
    last sale is not lost when the user closes the application."""
    _stop.set()
    _wake.set()   # wake immediately so it can exit cleanly
    # Attempt one final synchronous push before the process dies
    if config.is_configured() and REQUESTS_AVAILABLE:
        try:
            if _count_pending() > 0 and _check_connectivity(timeout=4):
                _sync_once()
        except Exception:
            pass


def push_now():
    """Wake the sync thread immediately (called after every sale/change)."""
    _wake.set()
