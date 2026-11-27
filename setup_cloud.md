# RetailPOS — Cloud Setup Guide

This guide takes about **10 minutes** and is free.

---

## Step 1 — Create a Supabase project (free)

1. Go to **https://supabase.com** and sign up (free, no credit card).
2. Click **New project**.
3. Choose a name (e.g. `retailpos`), set a database password, pick the
   region closest to your shop.
4. Wait ~2 minutes for the project to be ready.

---

## Step 2 — Create the database tables

In your Supabase project, open **SQL Editor** and run this script:

```sql
-- Products
CREATE TABLE IF NOT EXISTS products (
    product_id      SERIAL PRIMARY KEY,
    product_name    TEXT    NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 0,
    selling_price   NUMERIC NOT NULL,
    ordering_price  NUMERIC,
    units_per_pack  INTEGER NOT NULL DEFAULT 1
);

-- Sales
CREATE TABLE IF NOT EXISTS sales (
    sale_id         SERIAL PRIMARY KEY,
    sale_date       TEXT    NOT NULL,
    total_amount    NUMERIC NOT NULL,
    amount_received NUMERIC NOT NULL DEFAULT 0,
    change_given    NUMERIC NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'completed',
    voided_at       TEXT,
    void_reason     TEXT
);

-- Sale line items
CREATE TABLE IF NOT EXISTS sale_items (
    sale_item_id SERIAL PRIMARY KEY,
    sale_id      INTEGER NOT NULL REFERENCES sales(sale_id),
    product_id   INTEGER NOT NULL,
    product_name TEXT    NOT NULL,
    quantity     INTEGER NOT NULL,
    unit_price   NUMERIC NOT NULL,
    line_total   NUMERIC NOT NULL,
    unit_cost    NUMERIC
);

-- Allow the dashboard to read data (Row Level Security off for simplicity)
ALTER TABLE products  ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales     ENABLE ROW LEVEL SECURITY;
ALTER TABLE sale_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all via service key" ON products  FOR ALL USING (true);
CREATE POLICY "Allow all via service key" ON sales     FOR ALL USING (true);
CREATE POLICY "Allow all via service key" ON sale_items FOR ALL USING (true);
```

---

## Step 3 — Get your credentials

1. In your Supabase project go to **Settings → API**.
2. Copy:
   - **Project URL** (looks like `https://xyzabc.supabase.co`)
   - **service_role** key under *Project API keys*
     (use service_role, NOT anon — it has write access)

> ⚠ Keep the service_role key secret.
> Never commit `config.py` to a public git repo.

---

## Step 4 — Edit config.py

Open `config.py` in the RetailPOS folder and fill in:

```python
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."  # service_role key
```

Save the file.  The POS will start syncing automatically on the next launch.

---

## Step 5 — Install Python packages (once)

Open a Command Prompt in the RetailPOS folder and run:

```
pip install -r requirements.txt
```

---

## Step 6 — Start the owner dashboard

Double-click **`start_dashboard.bat`** on the shop PC, or run:

```
python dashboard/app.py
```

Open **http://localhost:5000** in any browser.

Default password: `admin1234`
Change it by setting the environment variable:
```
set DASHBOARD_PASSWORD=your-new-password
```

---

## Step 7 — Remote access (owner viewing from home / phone)

### Option A — ngrok (easiest, free)

1. Download ngrok from https://ngrok.com (free account)
2. Run:
   ```
   ngrok http 5000
   ```
3. ngrok gives you a public URL like `https://abc123.ngrok-free.app`
4. Share that URL with the owner — they open it in any browser.

### Option B — Tailscale (more permanent, also free)

1. Install Tailscale on the shop PC and on the owner's device.
2. Both devices join the same Tailscale network.
3. The owner accesses the dashboard at the shop PC's Tailscale IP:
   `http://100.x.x.x:5000`

### Option C — Deploy dashboard to Railway/Render

For an always-on URL that works even when the shop PC is off,
deploy `dashboard/app.py` to Railway or Render (both free tiers).
Set the environment variables `SUPABASE_URL`, `SUPABASE_KEY`,
and `DASHBOARD_PASSWORD` in the hosting platform's settings.

---

## How sync works

```
Shop PC  →  local SQLite  (always, instant, works offline)
              ↓  background thread (every 30 seconds)
         →  Supabase PostgreSQL  (when internet available)
              ↓
         →  Owner dashboard reads from Supabase
```

- Every sale, product change, and stock update is written to SQLite first.
- A `synced` column tracks which rows have been pushed to the cloud.
- When the internet is restored, all pending rows are uploaded automatically.
- The nav bar shows: `✓ Synced HH:MM` / `● Offline` / `⟳ Syncing…`

---

## Build a standalone EXE (no Python needed on the shop PC)

```
build_exe.bat
```

The finished `dist\RetailPOS.exe` runs on any Windows PC.
Copy it along with `config.py` so the target machine also syncs.

---

## File overview

```
RetailPOS/
├── main.py               ← POS application (run this daily)
├── config.py             ← YOUR CREDENTIALS go here
├── sync.py               ← Background sync engine
├── requirements.txt      ← pip dependencies
├── start_pos.bat         ← Double-click to open the POS
├── start_dashboard.bat   ← Double-click to start the web dashboard
├── build_exe.bat         ← Builds a standalone Windows EXE
├── RetailPOS.spec        ← PyInstaller spec (advanced builds)
├── setup_cloud.md        ← This guide
├── pos.db                ← Local SQLite database (never delete)
└── dashboard/
    ├── app.py            ← Flask web dashboard
    └── templates/        ← HTML pages
```
