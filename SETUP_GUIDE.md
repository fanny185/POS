# RetailPOS — Shop PC Setup Guide

## What this system does

```
Shop PC (RetailPOS.exe)
       │
       │  sells, records to local SQLite
       │  syncs every 15 seconds  ──────────────►  Supabase (cloud database)
                                                          │
                                                          │  reads live data
                                                          ▼
                                               Owner Dashboard (Render)
                                               https://pos-z7il.onrender.com
```

- The shop PC works **fully offline** — all sales are saved locally first.
- When internet is available, the shop PC automatically pushes new sales and
  inventory changes to Supabase.
- The owner dashboard at Render reads from Supabase and shows live data.

---

## Step 1 — Copy files to the shop PC

Copy the entire `RetailPOS` folder (from `dist\RetailPOS\`) to the shop PC.
Recommended location: `C:\RetailPOS\`

The folder must stay together — `RetailPOS.exe` cannot be moved out of it.

---

## Step 2 — Create the .env file

Inside the `RetailPOS` folder, create a plain text file named exactly `.env`
(no other extension). Paste the following content and fill in the values:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-service-role-key
DASHBOARD_PASSWORD=RetailPOS@2026
```

> **Keep this file private.** Do not share or email it.
> The SUPABASE_KEY is the service-role key — anyone with it can read/write
> all your data.

---

## Step 3 — Create a desktop shortcut

Right-click `RetailPOS.exe` → Send to → Desktop (create shortcut).

Or run `setup_shop.bat` (see below) which does this automatically.

---

## Step 4 — First launch

Double-click the shortcut. The app will:
1. Create `pos.db` (the local database) on first run.
2. Show "● Offline" in the top-right if there is no internet yet.
3. Show "✓ Synced" once it successfully reaches Supabase.

---

## Step 5 — Owner dashboard

The owner can log in from any browser at:

**https://pos-z7il.onrender.com**

- Username: `admin`
- Password: `RetailPOS@2026`

> Change the password in Render → Environment → `DASHBOARD_PASSWORD`.

---

## Automatic installer (Windows)

If you have the full project folder, run `setup_shop.bat` on the shop PC.
It will:
- Copy the app to `C:\RetailPOS\`
- Create the `.env` file automatically
- Create a desktop shortcut
- Optionally add it to Windows startup

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "☁ No cloud" in nav bar | `.env` missing or empty | Check `.env` exists next to the `.exe` |
| "● Offline" stays permanently | No internet / wrong Supabase URL | Check internet, check `.env` values |
| "✗ Sync error" | Wrong SUPABASE_KEY | Paste key again from Supabase dashboard |
| Dashboard shows old data | Shop PC offline for a while | Wait — it will sync when back online |
| App won't start | Antivirus blocked the exe | Add `RetailPOS.exe` to antivirus exclusions |

---

## Supabase table setup (first time only)

If you are connecting to a **brand new** Supabase project, run the SQL below
in Supabase → SQL Editor to create the required tables:

```sql
CREATE TABLE IF NOT EXISTS products (
  product_id     BIGINT PRIMARY KEY,
  product_name   TEXT NOT NULL,
  quantity       INTEGER NOT NULL DEFAULT 0,
  selling_price  NUMERIC(10,2) NOT NULL DEFAULT 0,
  ordering_price NUMERIC(10,2),
  units_per_pack INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sales (
  sale_id         BIGINT PRIMARY KEY,
  sale_date       TEXT NOT NULL,
  total_amount    NUMERIC(10,2) NOT NULL DEFAULT 0,
  amount_received NUMERIC(10,2) NOT NULL DEFAULT 0,
  change_given    NUMERIC(10,2) NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'completed',
  voided_at       TEXT,
  void_reason     TEXT
);

CREATE TABLE IF NOT EXISTS sale_items (
  sale_item_id BIGINT PRIMARY KEY,
  sale_id      BIGINT NOT NULL REFERENCES sales(sale_id),
  product_id   BIGINT NOT NULL,
  product_name TEXT NOT NULL,
  quantity     INTEGER NOT NULL,
  unit_price   NUMERIC(10,2) NOT NULL,
  line_total   NUMERIC(10,2) NOT NULL,
  unit_cost    NUMERIC(10,6)
);
```
