# ============================================================
#  RetailPOS  –  Owner Web Dashboard
#  Reads from Supabase (cloud) when online, local SQLite as fallback.
#  Writes always go to local SQLite (sync.py pushes them to cloud).
# ============================================================

import os, sys, sqlite3, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, date
from functools import wraps
from flask import (Flask, render_template, redirect, url_for,
                   session, request, jsonify, flash)
import config

# ── Cloud credentials — environment variables take priority over config.py.
# On Render/Railway set SUPABASE_URL, SUPABASE_KEY, DASHBOARD_PASSWORD,
# and DASHBOARD_SECRET_KEY as environment variables in the dashboard.
# Locally they fall back to config.py values so nothing breaks.
_SUPABASE_URL = os.environ.get("SUPABASE_URL",      config.SUPABASE_URL)
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY",      config.SUPABASE_KEY)
_SECRET_KEY   = os.environ.get("DASHBOARD_SECRET_KEY", config.DASHBOARD_SECRET_KEY)

app = Flask(__name__)
app.secret_key = _SECRET_KEY
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", config.DASHBOARD_USERNAME)
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", config.DASHBOARD_PASSWORD)
LOW_STOCK = config.LOW_STOCK_THRESHOLD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# On Render there is no local pos.db — use a temp one just for the session.
# All real data comes from Supabase; the local DB is only a cache.
_DB_DEFAULT = os.path.join(ROOT, "pos.db")
DB = os.environ.get("DB_PATH", _DB_DEFAULT)

# Module-level sync cache timestamp (not per-session, so multiple tabs share it)
_sync_cache_ts = 0.0


# ── Cloud connectivity (cached 15 s) ─────────────────────────────────────────
_cloud_cache = {"status": "not_configured", "checked": 0.0}

def cloud_status():
    now = time.time()
    if now - _cloud_cache["checked"] < 15:
        return _cloud_cache["status"]
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        _cloud_cache.update(status="not_configured", checked=now)
        return "not_configured"
    try:
        import requests as req
        r = req.get(
            f"{_SUPABASE_URL}/rest/v1/sales?select=sale_id&limit=1",
            headers={"apikey": _SUPABASE_KEY,
                     "Authorization": f"Bearer {_SUPABASE_KEY}"},
            timeout=4)
        status = "online" if r.status_code in (200, 206) else "error"
    except Exception:
        status = "offline"
    _cloud_cache.update(status=status, checked=now)
    return status


def _sb(table, params=None):
    """GET rows from Supabase. Returns list of dicts."""
    import requests as req
    h = {"apikey":        _SUPABASE_KEY,
         "Authorization": f"Bearer {_SUPABASE_KEY}",
         "Prefer":        "count=none"}
    r = req.get(f"{_SUPABASE_URL}/rest/v1/{table}",
                headers=h, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Local SQLite ──────────────────────────────────────────────────────────────
def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _ensure_schema():
    """Create tables if running on a fresh deployment with no pos.db."""
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            product_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name  TEXT NOT NULL,
            quantity      INTEGER NOT NULL DEFAULT 0,
            selling_price REAL NOT NULL,
            ordering_price REAL,
            units_per_pack INTEGER NOT NULL DEFAULT 1,
            synced        INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sales (
            sale_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_date       TEXT NOT NULL,
            total_amount    REAL NOT NULL,
            amount_received REAL NOT NULL DEFAULT 0,
            change_given    REAL NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'completed',
            voided_at       TEXT,
            void_reason     TEXT,
            synced          INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            sale_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id      INTEGER NOT NULL,
            product_id   INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity     INTEGER NOT NULL,
            unit_price   REAL NOT NULL,
            line_total   REAL NOT NULL,
            unit_cost    REAL,
            synced       INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (sale_id)    REFERENCES sales(sale_id),
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        );
    """)
    c.commit()
    c.close()


_ensure_schema()  # runs once on startup — no-op if tables already exist

def q(sql, params=()):
    c = _conn()
    rows = c.execute(sql, params).fetchall()
    c.close()
    return [dict(r) for r in rows]

def q1(sql, params=()):
    r = q(sql, params); return r[0] if r else {}

def run(sql, params=()):
    c = _conn(); cur = c.execute(sql, params); c.commit()
    lid = cur.lastrowid; c.close(); return lid

def run_many(statements):
    c = _conn()
    try:
        for sql, params in statements: c.execute(sql, params)
        c.commit()
    except Exception:
        c.rollback(); c.close(); raise
    c.close()


# ── Smart read: Supabase when online, SQLite as fallback ─────────────────────
def cloud_q(table, sb_params=None, local_sql=None, local_params=()):
    if cloud_status() == "online":
        try:
            return _sb(table, sb_params)
        except Exception:
            pass
    return q(local_sql, local_params) if local_sql else []


def sync_cloud_to_local():
    """Pull ALL Supabase data into local SQLite so every query works fast.

    Cached at module level for 15 seconds (not per-session) so multiple
    browser tabs don't double-fire.  Uses pagination to fetch more than
    the default 1,000-row PostgREST limit.
    """
    if cloud_status() != "online":
        return

    global _sync_cache_ts
    if time.time() - _sync_cache_ts < 15:
        return

    def _sb_all(table, params=None):
        """Fetch all rows from a Supabase table using range pagination."""
        import requests as req
        h = {"apikey":        _SUPABASE_KEY,
             "Authorization": f"Bearer {_SUPABASE_KEY}",
             "Prefer":        "count=none"}
        rows   = []
        offset = 0
        limit  = 1000
        while True:
            p = dict(params or {})
            p["limit"]  = limit
            p["offset"] = offset
            r = req.get(f"{_SUPABASE_URL}/rest/v1/{table}",
                        headers=h, params=p, timeout=15)
            r.raise_for_status()
            batch = r.json()
            rows.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
        return rows

    try:
        c = _conn()

        # ── products ──────────────────────────────────────────────────────
        for p in _sb_all("products", {"select": "*"}):
            c.execute("""
                INSERT INTO products
                    (product_id, product_name, quantity, selling_price,
                     ordering_price, units_per_pack, synced)
                VALUES (?,?,?,?,?,?,1)
                ON CONFLICT(product_id) DO UPDATE SET
                    product_name   = excluded.product_name,
                    quantity       = CASE WHEN synced=1 THEN excluded.quantity
                                          ELSE quantity END,
                    selling_price  = excluded.selling_price,
                    ordering_price = excluded.ordering_price,
                    units_per_pack = excluded.units_per_pack,
                    synced         = CASE WHEN synced=1 THEN 1 ELSE 0 END
            """, (p["product_id"], p["product_name"], p["quantity"],
                  p["selling_price"], p.get("ordering_price"),
                  p.get("units_per_pack", 1)))

        # ── sales ──────────────────────────────────────────────────────────
        for s in _sb_all("sales", {"select": "*", "order": "sale_id.asc"}):
            c.execute("""
                INSERT INTO sales
                    (sale_id, sale_date, total_amount, amount_received,
                     change_given, status, voided_at, void_reason, synced)
                VALUES (?,?,?,?,?,?,?,?,1)
                ON CONFLICT(sale_id) DO UPDATE SET
                    sale_date       = excluded.sale_date,
                    total_amount    = excluded.total_amount,
                    amount_received = excluded.amount_received,
                    change_given    = excluded.change_given,
                    status          = excluded.status,
                    voided_at       = excluded.voided_at,
                    void_reason     = excluded.void_reason,
                    synced          = 1
            """, (s["sale_id"], s["sale_date"], s["total_amount"],
                  s.get("amount_received", 0), s.get("change_given", 0),
                  s.get("status", "completed"),
                  s.get("voided_at"), s.get("void_reason")))

        # ── sale_items ─────────────────────────────────────────────────────
        for it in _sb_all("sale_items", {"select": "*", "order": "sale_item_id.asc"}):
            c.execute("""
                INSERT INTO sale_items
                    (sale_item_id, sale_id, product_id, product_name,
                     quantity, unit_price, line_total, unit_cost, synced)
                VALUES (?,?,?,?,?,?,?,?,1)
                ON CONFLICT(sale_item_id) DO UPDATE SET
                    quantity   = excluded.quantity,
                    unit_price = excluded.unit_price,
                    line_total = excluded.line_total,
                    unit_cost  = excluded.unit_cost,
                    synced     = 1
            """, (it["sale_item_id"], it["sale_id"], it["product_id"],
                  it["product_name"], it["quantity"], it["unit_price"],
                  it["line_total"], it.get("unit_cost")))

        c.commit()
        c.close()
        _sync_cache_ts = time.time()

    except Exception:
        try: c.close()
        except Exception: pass


# ── Formatting ────────────────────────────────────────────────────────────────
def fm(v):
    if v is None: return "—"
    try:    return f"{float(v):,.2f}"
    except: return "—"

def fpct(profit, revenue):
    try:
        if profit is None or not revenue: return "—"
        return f"{float(profit)/float(revenue)*100:.1f}%"
    except: return "—"

def vs_text(curr, prev, label):
    try:    curr, prev = float(curr or 0), float(prev or 0)
    except: return "No data", "muted"
    if not prev: return f"No data for {label}", "muted"
    pct = (curr - prev) / abs(prev) * 100
    if abs(pct) < 0.5: return f"Same as {label}", "muted"
    p = abs(pct); s = f"{p:.0f}%"
    return (f"▲ Up {s} vs {label}", "up") if pct > 0 else (f"▼ Down {s} vs {label}", "dn")


# ── Auth ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def d(*a, **kw):
        if not session.get("ok"): return redirect(url_for("login"))
        return f(*a, **kw)
    return d

@app.route("/login", methods=["GET","POST"])
def login():
    err = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == DASHBOARD_USERNAME and password == DASHBOARD_PASSWORD:
            session["ok"] = True
            return redirect(url_for("home"))
        err = "Incorrect username or password."
    return render_template("login.html", err=err)

@app.route("/logout", methods=["GET","POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))



# ── Home ──────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def home():
    from collections import defaultdict
    now   = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yest  = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    w_st  = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    cs    = cloud_status()

    # ── ONE call for sales, ONE call for items, ONE call for products ─────
    # Fetch last 30 days of sales + all their items in just 2 HTTP requests.
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    if cs == "online":
        try:
            all_sales = _sb("sales", {
                "select":    "sale_id,sale_date,total_amount,status",
                "sale_date": f"gte.{month_ago} 00:00:00",
                "order":     "sale_id.desc",
            })
            all_items = _sb("sale_items", {
                "select": "sale_id,quantity,line_total,unit_cost",
            })
            prods = _sb("products", {
                "select": "product_id,product_name,quantity,"
                          "ordering_price,units_per_pack",
            })
        except Exception:
            cs = "offline"

    if cs != "online":
        all_sales = q("SELECT sale_id,sale_date,total_amount,status "
                      "FROM sales WHERE sale_date >= ? ORDER BY sale_id DESC",
                      (month_ago + " 00:00:00",))
        all_items = q("SELECT si.sale_id,si.quantity,si.line_total,si.unit_cost "
                      "FROM sale_items si JOIN sales s ON s.sale_id=si.sale_id "
                      "WHERE s.sale_date >= ?",
                      (month_ago + " 00:00:00",))
        prods = q("SELECT product_id,product_name,quantity,"
                  "ordering_price,units_per_pack FROM products")

    # Build item lookup
    items_by = defaultdict(list)
    for it in all_items:
        items_by[it["sale_id"]].append(it)

    # Compute KPI totals entirely in Python — no extra HTTP calls
    def _totals(d1, d2):
        rows = [s for s in all_sales
                if s["status"] == "completed"
                and d1 <= s["sale_date"][:10] <= d2]
        cnt = len(rows)
        rev = sum(r["total_amount"] for r in rows)
        profit = sum(
            it["line_total"] - it["quantity"] * it["unit_cost"]
            for r in rows
            for it in items_by[r["sale_id"]]
            if it.get("unit_cost") is not None
        )
        return {"cnt": cnt, "rev": rev, "profit": profit}

    td = _totals(today, today)
    yd = _totals(yest,  yest)
    wk = _totals(w_st,  today)

    # Stock stats
    out_n      = sum(1 for p in prods if p["quantity"] == 0)
    low_n      = sum(1 for p in prods if 0 < p["quantity"] <= LOW_STOCK)
    stock_cost = sum(
        p["quantity"] * p["ordering_price"] / max(p["units_per_pack"] or 1, 1)
        for p in prods if p["ordering_price"] is not None)

    # Recent sales (from already-fetched data)
    recent_raw = sorted(all_sales, key=lambda r: r["sale_id"], reverse=True)[:15]
    recent = [{
        "sale_id":      r["sale_id"],
        "time":         r["sale_date"][11:16],
        "item_count":   sum(it["quantity"] for it in items_by[r["sale_id"]]),
        "total_amount": r["total_amount"],
        "status":       r["status"],
    } for r in recent_raw]

    # Low stock alerts
    alerts = sorted(
        [p for p in prods if p["quantity"] <= LOW_STOCK],
        key=lambda p: (p["quantity"], p["product_name"])
    )[:25]

    # 7-day chart
    per_day = defaultdict(float)
    for s in all_sales:
        if s["status"] == "completed":
            per_day[s["sale_date"][:10]] += s["total_amount"]
    chart = [{"label":    (now - timedelta(days=b)).strftime("%a"),
              "value":    per_day.get((now-timedelta(days=b)).strftime("%Y-%m-%d"), 0),
              "is_today": b == 0}
             for b in range(6, -1, -1)]

    return render_template("home.html",
        now=now, today=today,
        td=td, yd=yd, wk=wk,
        out_n=out_n, low_n=low_n,
        total_prods=len(prods), stock_cost=stock_cost,
        recent=recent, alerts=alerts, chart=chart,
        vs_text=vs_text, fm=fm, cloud=cs)


# ── Sales ─────────────────────────────────────────────────────────────────────
@app.route("/sales")
@login_required
def sales():
    sync_cloud_to_local()   # pull Supabase → local SQLite first
    today       = date.today().isoformat()
    yest        = (date.today()-timedelta(days=1)).isoformat()
    week_start  = (date.today()-timedelta(days=6)).isoformat()
    lweek_start = (date.today()-timedelta(days=13)).isoformat()
    lweek_end   = (date.today()-timedelta(days=7)).isoformat()
    month_start = date.today().replace(day=1).isoformat()
    lmonth_end  = date.today().replace(day=1) - timedelta(days=1)
    lmonth_start= lmonth_end.replace(day=1).isoformat()
    lmonth_end  = lmonth_end.isoformat()
    dfrom  = request.args.get("from",  week_start)
    dto    = request.args.get("to",    today)
    stflt  = request.args.get("status","all")
    srch   = request.args.get("q","").strip().lower()
    sel_id = request.args.get("sel","")

    sc = "" if stflt=="all" else "AND s.status=?"
    p  = [dfrom+" 00:00:00", dto+" 23:59:59"]
    if stflt != "all": p.append(stflt)
    rows = q(f"""
        SELECT s.sale_id, substr(s.sale_date,1,10) AS date,
               substr(s.sale_date,12,5) AS time,
               s.total_amount, s.amount_received, s.change_given,
               s.status, s.voided_at, s.void_reason,
               COALESCE(SUM(si.quantity),0) AS item_count,
               SUM(CASE WHEN si.unit_cost IS NOT NULL
                   THEN si.line_total - si.quantity*si.unit_cost END) AS profit,
               GROUP_CONCAT(si.product_name||' ('||si.quantity||')',', ') AS summary
        FROM sales s LEFT JOIN sale_items si ON si.sale_id=s.sale_id
        WHERE s.sale_date BETWEEN ? AND ? {sc}
        GROUP BY s.sale_id ORDER BY s.sale_id DESC
    """, p)

    if srch:
        rows = [r for r in rows if srch == str(r["sale_id"])
                or (r.get("summary") and srch in r["summary"].lower())]

    comp_rows  = [r for r in rows if r["status"] == "completed"]
    total_rev  = sum(r["total_amount"] for r in comp_rows)
    total_prof = sum(r.get("profit") or 0 for r in comp_rows)

    sel_sale, sel_items = None, []
    if sel_id:
        sel_sale  = q1("SELECT * FROM sales WHERE sale_id=?", (sel_id,))
        sel_items = q("""SELECT product_name, quantity, unit_price,
                                line_total, unit_cost
                         FROM sale_items WHERE sale_id=?
                         ORDER BY sale_item_id""", (sel_id,))

    return render_template("sales.html",
        rows=rows, dfrom=dfrom, dto=dto,
        stflt=stflt, srch=srch,
        total_rev=total_rev, total_prof=total_prof,
        sel_id=sel_id, sel_sale=sel_sale, sel_items=sel_items,
        today=today, yest=yest, week_start=week_start,
        lweek_start=lweek_start, lweek_end=lweek_end,
        month_start=month_start,
        lmonth_start=lmonth_start, lmonth_end=lmonth_end,
        fm=fm, cloud=cloud_status())


# ── Reports ───────────────────────────────────────────────────────────────────
@app.route("/reports")
@login_required
def reports():
    sync_cloud_to_local()   # pull Supabase → local SQLite first
    today       = date.today().isoformat()
    dfrom       = request.args.get("from",  (date.today()-timedelta(days=6)).isoformat())
    dto         = request.args.get("to",    today)
    group       = request.args.get("group", "Daily")
    yest        = (date.today()-timedelta(days=1)).isoformat()
    week_start  = (date.today()-timedelta(days=6)).isoformat()
    lweek_start = (date.today()-timedelta(days=13)).isoformat()
    lweek_end   = (date.today()-timedelta(days=7)).isoformat()
    month_start = date.today().replace(day=1).isoformat()
    _lme        = date.today().replace(day=1) - timedelta(days=1)
    lmonth_start= _lme.replace(day=1).isoformat()
    lmonth_end  = _lme.isoformat()

    psql = {"Daily":   "substr(s.sale_date,1,10)",
            "Weekly":  "date(substr(s.sale_date,1,10),'weekday 1','-6 days')",
            "Monthly": "substr(s.sale_date,1,7)"}.get(group,"substr(s.sale_date,1,10)")
    span = (dfrom+" 00:00:00", dto+" 23:59:59")

    # all aggregation done in local SQLite (fast, no Supabase query limits)
    def totals(d1, d2):
        r = q1("""
            SELECT COUNT(DISTINCT s.sale_id) AS cnt,
                   COALESCE(SUM(si.quantity),0) AS items,
                   COALESCE(SUM(si.line_total),0) AS rev,
                   COALESCE(SUM(CASE WHEN si.unit_cost IS NOT NULL
                       THEN si.quantity*si.unit_cost END),0) AS cost,
                   COALESCE(SUM(CASE WHEN si.unit_cost IS NOT NULL
                       THEN si.line_total END),0) AS crev
            FROM sales s JOIN sale_items si ON si.sale_id=s.sale_id
            WHERE s.sale_date BETWEEN ? AND ? AND s.status='completed'
        """, (d1, d2))
        r["profit"]  = (r.get("crev") or 0) - (r.get("cost") or 0)
        r["average"] = r["rev"] / r["cnt"] if r.get("cnt") else 0
        return r

    days   = (datetime.strptime(dto,"%Y-%m-%d")-datetime.strptime(dfrom,"%Y-%m-%d")).days+1
    b_end  = (datetime.strptime(dfrom,"%Y-%m-%d")-timedelta(days=1)).strftime("%Y-%m-%d")
    b_st   = (datetime.strptime(dfrom,"%Y-%m-%d")-timedelta(days=days)).strftime("%Y-%m-%d")
    cur    = totals(dfrom+" 00:00:00", dto+" 23:59:59")
    prev   = totals(b_st+" 00:00:00",  b_end+" 23:59:59")
    vs     = "yesterday" if days==1 else f"prev {days}d"

    kpis = {}
    for key, cv, pv in [
        ("revenue", cur.get("rev",0),     prev.get("rev",0)),
        ("profit",  cur.get("profit",0),  prev.get("profit",0)),
        ("sales",   cur.get("cnt",0),     prev.get("cnt",0)),
        ("average", cur.get("average",0), prev.get("average",0)),
        ("items",   cur.get("items",0),   prev.get("items",0)),
    ]:
        txt, cls = vs_text(cv, pv, vs)
        kpis[key] = {"value": fm(cv) if key not in ("sales","items") else str(int(cv or 0)),
                     "txt": txt, "cls": cls, "raw": cv}

    margin = fpct(cur.get("profit"), cur.get("crev"))
    vo = q1("""SELECT COUNT(*) AS c, COALESCE(SUM(total_amount),0) AS a
               FROM sales WHERE sale_date BETWEEN ? AND ? AND status='voided'""", span)

    prows = q(f"""
        SELECT {psql} AS period, COUNT(DISTINCT s.sale_id) AS sales_cnt,
               SUM(si.line_total) AS rev,
               SUM(CASE WHEN si.unit_cost IS NOT NULL
                   THEN si.quantity*si.unit_cost END) AS cost,
               SUM(CASE WHEN si.unit_cost IS NOT NULL
                   THEN si.line_total END) AS crev,
               GROUP_CONCAT(si.product_name||' ('||si.quantity||')',', ') AS products
        FROM sales s JOIN sale_items si ON si.sale_id=s.sale_id
        WHERE s.sale_date BETWEEN ? AND ? AND s.status='completed'
        GROUP BY period ORDER BY period DESC
    """, span)
    for r in prows:
        r["profit"] = None if r["crev"] is None else (r["crev"] or 0)-(r["cost"] or 0)
        r["margin"] = fpct(r["profit"], r["crev"])

    trend = list(reversed(prows[-60:]))

    top = q(f"""
        SELECT MAX(si.product_name) AS name, SUM(si.quantity) AS units,
               SUM(si.line_total) AS rev,
               SUM(CASE WHEN si.unit_cost IS NOT NULL
                   THEN si.line_total - si.quantity*si.unit_cost END) AS profit
        FROM sale_items si JOIN sales s ON s.sale_id=si.sale_id
        WHERE s.sale_date BETWEEN ? AND ? AND s.status='completed'
        GROUP BY si.product_id ORDER BY 2 DESC, 3 DESC LIMIT 20
    """, span)
    total_rev_share = cur.get("rev") or 1
    for i, r in enumerate(top):
        r["rank"] = i+1; r["share"] = fpct(r["rev"], total_rev_share)

    low_rows = q("""SELECT product_id, product_name, quantity FROM products
                    WHERE quantity <= ? ORDER BY quantity,
                    product_name COLLATE NOCASE""", (LOW_STOCK,))

    sales_rows = q("""
        SELECT s.sale_id, substr(s.sale_date,1,10) AS date,
               substr(s.sale_date,12,5) AS time, s.total_amount,
               s.amount_received, s.change_given, s.status,
               COALESCE(SUM(si.quantity),0) AS item_count,
               SUM(CASE WHEN si.unit_cost IS NOT NULL
                   THEN si.line_total - si.quantity*si.unit_cost END) AS profit,
               GROUP_CONCAT(si.product_name||' ('||si.quantity||')',', ') AS summary
        FROM sales s LEFT JOIN sale_items si ON si.sale_id=s.sale_id
        WHERE s.sale_date BETWEEN ? AND ?
        GROUP BY s.sale_id ORDER BY s.sale_id DESC
    """, span)

    return render_template("reports.html",
        today=today, dfrom=dfrom, dto=dto, group=group,
        yest=yest, week_start=week_start, month_start=month_start,
        lweek_start=lweek_start, lweek_end=lweek_end,
        lmonth_start=lmonth_start, lmonth_end=lmonth_end,
        kpis=kpis, margin=margin,
        vo_count=vo.get("c",0), vo_amount=fm(vo.get("a")),
        prows=prows, trend=trend, top=top, low_rows=low_rows,
        sales_rows=sales_rows, cur=cur,
        fm=fm, fpct=fpct, cloud=cloud_status())


# ── Inventory ─────────────────────────────────────────────────────────────────
@app.route("/inventory")
@login_required
def inventory():
    sync_cloud_to_local()   # pull Supabase → local SQLite first
    flt  = request.args.get("filter","all")
    srch = request.args.get("q","").strip().lower()

    all_rows = q("""SELECT product_id, product_name, quantity, selling_price,
                           ordering_price, units_per_pack
                    FROM products ORDER BY product_name COLLATE NOCASE""")

    for r in all_rows:
        up = max(r.get("units_per_pack") or 1, 1)
        op = r.get("ordering_price")
        r["unit_cost"]   = round(op/up, 4) if op is not None else None
        r["unit_profit"] = (r["selling_price"]-r["unit_cost"]
                            if r["unit_cost"] is not None else None)
        r["margin"]      = fpct(r["unit_profit"], r["selling_price"])

    total     = len(all_rows)
    out_n     = sum(1 for r in all_rows if r["quantity"]==0)
    low_n     = sum(1 for r in all_rows if 0<r["quantity"]<=LOW_STOCK)
    in_stk    = total - out_n
    miss_c    = sum(1 for r in all_rows if r["unit_cost"] is None)
    s_cost    = sum(r["quantity"]*r["unit_cost"]
                    for r in all_rows if r["unit_cost"] is not None)
    s_retail  = sum(r["quantity"]*r["selling_price"] for r in all_rows)
    potential = s_retail - s_cost

    rows = all_rows
    if   flt=="low":  rows=[r for r in rows if 0<r["quantity"]<=LOW_STOCK]
    elif flt=="out":  rows=[r for r in rows if r["quantity"]==0]
    elif flt=="loss": rows=[r for r in rows if r["unit_profit"] is not None
                            and r["unit_profit"]<0]
    if srch:
        rows=[r for r in rows if srch in r["product_name"].lower()
              or srch==str(r["product_id"])]

    return render_template("inventory.html",
        rows=rows, all_count=total,
        flt=flt, srch=srch,
        total=total, out_n=out_n, low_n=low_n, in_stk=in_stk,
        miss_c=miss_c, s_cost=s_cost, potential=potential,
        LOW_STOCK=LOW_STOCK,
        fm=fm, fpct=fpct, cloud=cloud_status())


# ── Add product ───────────────────────────────────────────────────────────────
@app.route("/inventory/add", methods=["GET","POST"])
@login_required
def product_add():
    error = None; preview = None
    if request.method == "POST":
        name  = request.form.get("name","").strip()
        units = request.form.get("units","1").strip()
        cost  = request.form.get("cost","").strip()
        price = request.form.get("price","").strip()
        qty   = request.form.get("quantity","").strip()
        if request.form.get("action") == "preview":
            try:
                u=int(units); c=float(cost); p=float(price)
                uc=c/u; up=p-uc
                preview={"unit_cost":f"{uc:.4f}".rstrip("0") or "0",
                         "unit_profit":f"{up:.4f}".rstrip("0") or "0",
                         "margin":f"{up/p*100:.1f}%" if p else "—",
                         "pack_profit":f"{p*u-c:.2f}","is_loss":up<0}
            except Exception: pass
            return render_template("product_form.html",
                editing=False, error=error, preview=preview,
                f={"name":name,"units":units,"cost":cost,"price":price,"quantity":qty})
        try:
            if not all([name,units,cost,price,qty]):
                raise ValueError("All fields are required.")
            u=int(units)
            if u<1: raise ValueError("Units per pack must be at least 1.")
            c=round(float(cost),2); p=round(float(price),2); qt=int(qty)
            if any(x<0 for x in [c,p,qt]): raise ValueError("Values cannot be negative.")
        except ValueError as e: error=str(e)
        else:
            dup=q("SELECT product_id FROM products WHERE LOWER(product_name)=LOWER(?)",(name,))
            if dup: error=f"A product named '{name}' already exists."
            else:
                run("""INSERT INTO products
                           (product_name,quantity,selling_price,
                            ordering_price,units_per_pack,synced)
                       VALUES (?,?,?,?,?,0)""",(name,qt,p,c,u))
                flash(f"✓ '{name}' added successfully.","success")
                return redirect(url_for("inventory"))
    return render_template("product_form.html",
        editing=False,error=error,preview=preview,
        f={"name":"","units":"1","cost":"","price":"","quantity":""})


# ── Edit product ──────────────────────────────────────────────────────────────
@app.route("/inventory/edit/<int:pid>", methods=["GET","POST"])
@login_required
def product_edit(pid):
    prod=q1("SELECT * FROM products WHERE product_id=?",(pid,))
    if not prod: flash("Product not found.","error"); return redirect(url_for("inventory"))
    error=None; preview=None
    f={"name":prod["product_name"],"units":str(prod["units_per_pack"] or 1),
       "cost":"" if prod["ordering_price"] is None else f"{prod['ordering_price']:.2f}",
       "price":f"{prod['selling_price']:.2f}","quantity":str(prod["quantity"])}
    if request.method == "POST":
        name=request.form.get("name","").strip()
        units=request.form.get("units","1").strip()
        cost=request.form.get("cost","").strip()
        price=request.form.get("price","").strip()
        qty=request.form.get("quantity","").strip()
        f={"name":name,"units":units,"cost":cost,"price":price,"quantity":qty}
        if request.form.get("action")=="preview":
            try:
                u=int(units); c=float(cost); p=float(price)
                uc=c/u; up=p-uc
                preview={"unit_cost":f"{uc:.4f}".rstrip("0") or "0",
                         "unit_profit":f"{up:.4f}".rstrip("0") or "0",
                         "margin":f"{up/p*100:.1f}%" if p else "—",
                         "pack_profit":f"{p*u-c:.2f}","is_loss":up<0}
            except Exception: pass
            return render_template("product_form.html",
                editing=True,pid=pid,error=error,preview=preview,f=f,prod=prod)
        try:
            if not all([name,units,cost,price,qty]): raise ValueError("All fields are required.")
            u=int(units)
            if u<1: raise ValueError("Units per pack must be at least 1.")
            c=round(float(cost),2); p=round(float(price),2); qt=int(qty)
            if any(x<0 for x in [c,p,qt]): raise ValueError("Values cannot be negative.")
        except ValueError as e: error=str(e)
        else:
            dup=q("SELECT product_id FROM products WHERE LOWER(product_name)=LOWER(?) AND product_id!=?",(name,pid))
            if dup: error=f"Another product named '{name}' already exists."
            else:
                run("UPDATE products SET product_name=?,quantity=?,selling_price=?,ordering_price=?,units_per_pack=?,synced=0 WHERE product_id=?",(name,qt,p,c,u,pid))
                flash(f"✓ '{name}' updated.","success")
                return redirect(url_for("inventory"))
    return render_template("product_form.html",
        editing=True,pid=pid,error=error,preview=preview,f=f,prod=prod)


# ── Restock product ───────────────────────────────────────────────────────────
@app.route("/inventory/restock/<int:pid>", methods=["GET","POST"])
@login_required
def product_restock(pid):
    prod=q1("SELECT product_id,product_name,quantity,selling_price,ordering_price,units_per_pack FROM products WHERE product_id=?",(pid,))
    if not prod: flash("Product not found.","error"); return redirect(url_for("inventory"))
    units=max(prod["units_per_pack"] or 1,1); error=None; result=None
    if request.method=="POST":
        mode=request.form.get("mode","packs"); amount=request.form.get("amount","").strip()
        cost=request.form.get("cost","").strip()
        try:
            amt=int(amount)
            if amt<1: raise ValueError
        except ValueError: error="Enter a whole number greater than 0."
        else:
            added=amt*units if mode=="packs" else amt; new_cost=None
            if cost:
                try:
                    new_cost=round(float(cost),2)
                    if new_cost<0: raise ValueError
                except ValueError: error="Ordering price must be a valid non-negative number."
            if not error:
                run("UPDATE products SET quantity=quantity+?,ordering_price=COALESCE(?,ordering_price),synced=0 WHERE product_id=?",(added,new_cost,pid))
                flash(f"✓ Added {added} units to '{prod['product_name']}'. New stock: {prod['quantity']+added}.","success")
                return redirect(url_for("inventory"))
        try:
            a=int(amount); ap=a*units if request.form.get("mode","packs")=="packs" else a
            result=f"Adds {ap} units → new stock: {prod['quantity']+ap}"
        except Exception: pass
    return render_template("restock_form.html",
        prod=prod,units=units,error=error,result=result,fm=fm,
        f={"mode":request.form.get("mode","packs" if units>1 else "units"),
           "amount":request.form.get("amount",""),
           "cost":request.form.get("cost","" if prod["ordering_price"] is None else f"{prod['ordering_price']:.2f}")})


# ── Delete product ────────────────────────────────────────────────────────────
@app.route("/inventory/delete/<int:pid>", methods=["POST"])
@login_required
def product_delete(pid):
    prod=q1("SELECT product_name FROM products WHERE product_id=?",(pid,))
    if not prod: flash("Product not found.","error"); return redirect(url_for("inventory"))
    name=prod["product_name"]
    if q("SELECT COUNT(*) AS c FROM sale_items WHERE product_id=?",(pid,))[0]["c"]>0:
        flash(f"Cannot delete '{name}' — it appears in past sales. Set its quantity to 0 instead.","error")
        return redirect(url_for("inventory"))
    run("DELETE FROM products WHERE product_id=?",(pid,))
    flash(f"✓ '{name}' deleted.","success")
    return redirect(url_for("inventory"))


# ── Reverse a sale ────────────────────────────────────────────────────────────
@app.route("/reports/reverse/<int:sale_id>", methods=["GET","POST"])
@login_required
def sale_reverse(sale_id):
    sale=q1("SELECT * FROM sales WHERE sale_id=?",(sale_id,))
    if not sale: flash("Sale not found.","error"); return redirect(url_for("reports"))
    if sale["status"]=="voided": flash(f"Sale #{sale_id} has already been reversed.","error"); return redirect(url_for("reports"))
    if request.method=="POST":
        reason=request.form.get("reason","").strip()
        if not reason: return render_template("reverse_form.html",sale=sale,error="A reason is required.",fm=fm)
        try:
            items=q("SELECT product_id,quantity FROM sale_items WHERE sale_id=?",(sale_id,))
            stmts=[("""UPDATE products SET quantity=quantity+?,synced=0 WHERE product_id=?""",(it["quantity"],it["product_id"])) for it in items]
            stmts.append(("""UPDATE sales SET status='voided',voided_at=?,void_reason=?,synced=0 WHERE sale_id=? AND status='completed'""",(datetime.now().strftime("%Y-%m-%d %H:%M:%S"),reason,sale_id)))
            stmts.append(("""UPDATE sale_items SET synced=0 WHERE sale_id=?""",(sale_id,)))
            run_many(stmts)
            flash(f"✓ Sale #{sale_id} reversed. Stock returned. Refund customer: {fm(sale['total_amount'])}","success")
        except Exception as e: flash(f"Reversal failed: {e}","error")
        return redirect(url_for("reports"))
    return render_template("reverse_form.html",sale=sale,error=None,fm=fm)


# ── Delete a single sale ──────────────────────────────────────────────────────
@app.route("/reports/delete/<int:sale_id>", methods=["POST"])
@login_required
def sale_delete(sale_id):
    sale=q1("SELECT * FROM sales WHERE sale_id=?",(sale_id,))
    if not sale: flash("Sale not found.","error"); return redirect(url_for("reports"))
    restore=request.form.get("restore_stock")=="yes"
    try:
        stmts=[]
        if restore and sale["status"]=="completed":
            for it in q("SELECT product_id,quantity FROM sale_items WHERE sale_id=?",(sale_id,)):
                stmts.append(("UPDATE products SET quantity=quantity+?,synced=0 WHERE product_id=?",(it["quantity"],it["product_id"])))
        stmts.append(("DELETE FROM sale_items WHERE sale_id=?",(sale_id,)))
        stmts.append(("DELETE FROM sales WHERE sale_id=?",(sale_id,)))
        run_many(stmts)
        flash(f"✓ Sale #{sale_id} permanently deleted."+(" Stock returned." if restore else ""),"success")
    except Exception as e: flash(f"Delete failed: {e}","error")
    return redirect(url_for("reports"))


# ── Delete all sales in date range ────────────────────────────────────────────
@app.route("/reports/delete_range", methods=["POST"])
@login_required
def sale_delete_range():
    dfrom=request.form.get("from","").strip(); dto=request.form.get("to","").strip()
    restore=request.form.get("restore_stock")=="yes"
    confirm=request.form.get("confirm","").strip()
    if not dfrom or not dto: flash("Please select both a start and end date.","error"); return redirect(url_for("reports"))
    if confirm!="DELETE": flash("Type DELETE (in capitals) to confirm bulk deletion.","error"); return redirect(url_for("reports",**{"from":dfrom,"to":dto}))
    try:
        rows=q("SELECT sale_id,status FROM sales WHERE sale_date BETWEEN ? AND ?",(dfrom+" 00:00:00",dto+" 23:59:59"))
        if not rows: flash("No sales found in that date range.","error"); return redirect(url_for("reports",**{"from":dfrom,"to":dto}))
        stmts=[]
        if restore:
            comp=[r["sale_id"] for r in rows if r["status"]=="completed"]
            if comp:
                marks=",".join("?"*len(comp))
                for it in q(f"SELECT product_id,SUM(quantity) AS qty FROM sale_items WHERE sale_id IN ({marks}) GROUP BY product_id",comp):
                    stmts.append(("UPDATE products SET quantity=quantity+?,synced=0 WHERE product_id=?",(it["qty"],it["product_id"])))
        ids=[r["sale_id"] for r in rows]; marks=",".join("?"*len(ids))
        stmts.append((f"DELETE FROM sale_items WHERE sale_id IN ({marks})",ids))
        stmts.append((f"DELETE FROM sales WHERE sale_id IN ({marks})",ids))
        run_many(stmts)
        flash(f"✓ {len(ids)} sale(s) deleted ({dfrom} → {dto})."+(" Stock returned." if restore else ""),"success")
    except Exception as e: flash(f"Delete failed: {e}","error")
    return redirect(url_for("reports",**{"from":dfrom,"to":dto}))


# ── API status + force sync ───────────────────────────────────────────────────
@app.route("/api/status")
@login_required
def api_status():
    return jsonify({"cloud": cloud_status(),
                    "time": datetime.now().strftime("%H:%M:%S")})

@app.route("/api/refresh", methods=["GET","POST"])
@login_required
def api_refresh():
    """Force a fresh cloud→local sync (clears the module-level cache)."""
    global _sync_cache_ts
    _sync_cache_ts = 0.0
    sync_cloud_to_local()
    return jsonify({"ok": True, "cloud": cloud_status(),
                    "time": datetime.now().strftime("%H:%M:%S")})


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import socket
    port = config.DASHBOARD_PORT
    # Try to get the local network IP for display purposes
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "YOUR-PC-IP"
    print(f"\n  RetailPOS Dashboard")
    print(f"  ─────────────────────────────────────────")
    print(f"  This computer:  http://localhost:{port}")
    print(f"  Phone/Tablet:   http://{local_ip}:{port}")
    print(f"  ─────────────────────────────────────────")
    print(f"  Make sure all devices are on the same Wi-Fi.")
    print(f"  Press Ctrl+C to stop.\n")
    app.run(host="0.0.0.0", port=port, debug=False)
