# ============================================================
#  RetailPOS  –  Cloud Configuration
#
#  Secrets are stored in a .env file next to this file.
#  Copy .env.example to .env and fill in your values.
#  Never commit .env to Git.
# ============================================================

import os, secrets as _secrets
from pathlib import Path

# Locate the .env file.
# When running as a PyInstaller exe, __file__ is inside a temp folder,
# so we also check next to the actual executable (sys.executable).
import sys as _sys

def _load_env():
    candidates = [
        Path(_sys.executable).parent / ".env",   # next to RetailPOS.exe  ← exe mode
        Path(__file__).parent / ".env",           # next to config.py      ← dev mode
    ]
    for _env_path in candidates:
        if _env_path.exists():
            for _line in _env_path.read_text().splitlines():
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())
            return  # stop after first found

_load_env()

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
