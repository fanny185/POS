# ============================================================
#  RetailPOS  –  Background Sync Engine  (two-way)
#
#  Every SYNC_INTERVAL_SECONDS it:
#    1. Checks internet / Supabase connectivity
#    2. PULL: fetches all products from Supabase and applies
#             any changes (edits, deletions, new products added
#             from the owner dashboard) into local SQLite
#    3. PUSH: upserts any locally unsynced products/sales/items
#             to Supabase, and fires deletes for deleted_products
#    4. Updates the status indicator in the nav bar
#
#  Supabase is the single source of truth for products.
#  Sales are always created on the shop PC and pushed up.
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
    "state":       "disabled",
    "last_sync":   None,
    "pending":     0,
    "last_error":  "",
}
_lock      = threading.Lock()
_stop      = threading.Event()
_wake      = threading.Event()
_callbacks = []


def get_status():
    with _lock:
        return dict(_status)


def on_status_change(fn):
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
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_sync_columns():
    conn = _local_conn()
    cur  = conn.cursor()
    for table in ("products", "sales", "sale_items"):
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        if "synced" not in cols:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN synced INTEGER NOT NULL DEFAULT 0"
            )
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


def _get_all(table, params=None, timeout=15):
    """Fetch all rows from a Supabase table with pagination."""
    rows   = []
    offset = 0
    limit  = 1000
    h = {
        "apikey":        config.SUPABASE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_KEY}",
        "Prefer":        "count=none",
    }
    while True:
        p = dict(params or {})
        p["limit"]  = limit
        p["offset"] = offset
        r = requests.get(
            f"{config.SUPABASE_URL}/rest/v1/{table}",
            headers=h, params=p, timeout=timeout
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return rows


def _upsert(table, rows, timeout=10):
    if not rows:
        return
    resp = requests.post(
        f"{config.SUPABASE_URL}/rest/v1/{table}",
        json=rows, headers=_headers(), timeout=timeout
    )
    resp.raise_for_status()


def _delete_from_supabase(table, column, value, timeout=10):
    requests.delete(
        f"{config.SUPABASE_URL}/rest/v1/{table}?{column}=eq.{value}",
        headers={**_headers(), "Prefer": ""},
        timeout=timeout
    )


def _check_connectivity(timeout=5):
    try:
        r = requests.get(
            f"{config.SUPABASE_URL}/rest/v1/sales?limit=1",
            headers=_headers(), timeout=timeout
        )
        return r.status_code in (200, 206)
    except Exception:
        return False


# ------------------------------------------------------------------
# PULL: Supabase → local SQLite
# ------------------------------------------------------------------
def _pull(conn):
    """Apply all Supabase product changes to the local database.

    - New products added from the dashboard are inserted locally.
    - Products edited on the dashboard are updated locally.
    - Products deleted from the dashboard are removed locally.
    Sales are not pulled (they always originate on the shop PC).
    """
    cur = conn.cursor()

    sb_products = _get_all("products", {"select": "*"})
    sb_ids      = {p["product_id"] for p in sb_products}

    # First: gather local products that have pending unsynced changes
    cur.execute("SELECT product_id FROM products WHERE synced = 0")
    locally_dirty = {r["product_id"] for r in cur.fetchall()}

    # Also gather IDs queued for deletion locally
    cur.execute("SELECT product_id FROM deleted_products WHERE synced = 0")
    locally_deleted = {r["product_id"] for r in cur.fetchall()}

    for p in sb_products:
        pid = p["product_id"]
        # Skip products that have unsaved local edits — push wins
        if pid in locally_dirty:
            continue
        cur.execute("""
            INSERT INTO products
                (product_id, product_name, quantity, selling_price,
                 ordering_price, units_per_pack, synced)
            VALUES (?,?,?,?,?,?,1)
            ON CONFLICT(product_id) DO UPDATE SET
                product_name   = excluded.product_name,
                quantity       = excluded.quantity,
                selling_price  = excluded.selling_price,
                ordering_price = excluded.ordering_price,
                units_per_pack = excluded.units_per_pack,
                synced         = 1
        """, (pid, p["product_name"], p["quantity"],
              p["selling_price"], p.get("ordering_price"),
              p.get("units_per_pack", 1)))

    # Remove local products no longer in Supabase (deleted from dashboard)
    cur.execute("SELECT product_id FROM products")
    local_ids = {r["product_id"] for r in cur.fetchall()}
    for lid in local_ids:
        if lid not in sb_ids and lid not in locally_dirty and lid not in locally_deleted:
            cur.execute("DELETE FROM products WHERE product_id=?", (lid,))

    conn.commit()


# ------------------------------------------------------------------
# PUSH: local SQLite → Supabase
# ------------------------------------------------------------------
def _push(conn):
    cur    = conn.cursor()
    pushed = 0

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
        SELECT sale_item_id, sale_id, product_id,
               product_name, quantity, unit_price,
               line_total, unit_cost
        FROM sale_items WHERE synced = 0
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
        cur.execute("SELECT product_id FROM deleted_products WHERE synced = 0")
        deleted_ids = [r["product_id"] for r in cur.fetchall()]
        for pid in deleted_ids:
            try:
                _delete_from_supabase("products", "product_id", pid)
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
        pass

    return pushed


# ------------------------------------------------------------------
# Combined sync cycle
# ------------------------------------------------------------------
def _sync_once():
    conn = _local_conn()
    try:
        _pull(conn)          # apply dashboard changes to shop PC first
        pushed = _push(conn) # then push local changes up
        return pushed
    finally:
        conn.close()


# ------------------------------------------------------------------
# Background thread
# ------------------------------------------------------------------
def _run():
    _ensure_sync_columns()

    backoff     = config.SYNC_INTERVAL_SECONDS
    MAX_BACKOFF = 300

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

            backoff = config.SYNC_INTERVAL_SECONDS

            _set_status(state="syncing")
            pushed = _sync_once()
            _set_status(
                state="ok",
                last_sync=datetime.now(),
                pending=max(0, pending - pushed),
                last_error=""
            )

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
    global _thread
    _ensure_sync_columns()
    _thread = threading.Thread(target=_run, daemon=True, name="SyncThread")
    _thread.start()


def stop():
    _stop.set()
    _wake.set()
    if config.is_configured() and REQUESTS_AVAILABLE:
        try:
            if _check_connectivity(timeout=4):
                conn = _local_conn()
                try:
                    _pull(conn)
                    _push(conn)
                finally:
                    conn.close()
        except Exception:
            pass


def push_now():
    """Wake the sync thread immediately."""
    _wake.set()
