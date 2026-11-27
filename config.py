# ============================================================
#  RetailPOS  –  Cloud Configuration
#
#  Secrets are stored in a .env file next to this file.
#  Copy .env.example to .env and fill in your values.
#  Never commit .env to Git.
# ============================================================

import os, secrets as _secrets
from pathlib import Path

# Load .env file if it exists (local development / deployed EXE)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Credentials (read from environment / .env) ────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_USERNAME   = os.environ.get("DASHBOARD_USERNAME",  "admin")
DASHBOARD_PASSWORD   = os.environ.get("DASHBOARD_PASSWORD",  "RetailPOS@2026")
DASHBOARD_SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY", _secrets.token_hex(32))
DASHBOARD_PORT      = int(os.environ.get("DASHBOARD_PORT", 5000))

# ── App settings ──────────────────────────────────────────────────────────────
SYNC_INTERVAL_SECONDS = 15
LOW_STOCK_THRESHOLD   = 5

# ── Derived helpers ───────────────────────────────────────────────────────────
def is_configured():
    """Return True when Supabase credentials are present."""
    return bool(SUPABASE_URL and SUPABASE_KEY)
