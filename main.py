import csv
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk

# Optional sync module — only loaded when config.py has credentials
try:
    import sync as _sync
    import config as _config
    _SYNC_AVAILABLE = True
except ImportError:
    _SYNC_AVAILABLE = False

# -------------------- UI CONSTANTS --------------------
FONT_FAMILY = "Segoe UI"
FONT_NORMAL  = (FONT_FAMILY, 11)
FONT_BOLD    = (FONT_FAMILY, 11, "bold")
FONT_SMALL   = (FONT_FAMILY, 10)
FONT_HEADING = (FONT_FAMILY, 15, "bold")
FONT_TITLE   = (FONT_FAMILY, 21, "bold")
FONT_GIANT   = (FONT_FAMILY, 23, "bold")
FONT_KPI     = (FONT_FAMILY, 20, "bold")

# ── Theme palettes ────────────────────────────────────────────────────────────
_THEMES = {
    "light": {
        "bg":          "#f0f2f5",
        "card":        "white",
        "border":      "#d1d5db",
        "muted":       "#6b7280",
        "dark":        "#1f2937",
        "text":        "#111827",
        "blue":        "#2563eb",
        "green":       "#16a34a",
        "red":         "#dc2626",
        "amber":       "#92400e",
        "nav_bg":      "#1f2937",
        "nav_fg":      "white",
        "nav_active":  "#2563eb",
        "btn_bg":      "#e5e7eb",
        "btn_fg":      "#1f2937",
        "btn_hover":   "#d1d5db",
        "entry_bg":    "white",
        "entry_fg":    "#111827",
        "tree_bg":     "white",
        "tree_sel":    "#bfdbfe",
        "tree_sel_fg": "#1f2937",
        "tree_odd":    "#f3f4f6",
        "tab_bg":      "#e5e7eb",
        "tab_sel":     "white",
        "sep":         "#d1d5db",
        "row_low":     "#fff4cc",
        "row_out":     "#fde2e2",
    },
    "dark": {
        "bg":          "#0f172a",
        "card":        "#1e293b",
        "border":      "#334155",
        "muted":       "#94a3b8",
        "dark":        "#f1f5f9",
        "text":        "#f8fafc",
        "blue":        "#3b82f6",
        "green":       "#22c55e",
        "red":         "#ef4444",
        "amber":       "#f59e0b",
        "nav_bg":      "#0f172a",
        "nav_fg":      "#e2e8f0",
        "nav_active":  "#3b82f6",
        "btn_bg":      "#334155",
        "btn_fg":      "#e2e8f0",
        "btn_hover":   "#475569",
        "entry_bg":    "#1e293b",
        "entry_fg":    "#f1f5f9",
        "tree_bg":     "#1e293b",
        "tree_sel":    "#1d4ed8",
        "tree_sel_fg": "#f1f5f9",
        "tree_odd":    "#253347",
        "tab_bg":      "#1e293b",
        "tab_sel":     "#0f172a",
        "sep":         "#334155",
        "row_low":     "#3d3200",
        "row_out":     "#3d0f0f",
    },
}

_current_theme = {"name": "light"}


def _t(key):
    """Return the current theme colour for the given key."""
    return _THEMES[_current_theme["name"]][key]


# Convenience aliases — these are read at widget-creation time AND
# updated when the theme changes via apply_theme().
def _update_clr():
    global CLR_BG, CLR_CARD, CLR_BORDER, CLR_MUTED, CLR_DARK
    global CLR_TEXT, CLR_BLUE, CLR_GREEN, CLR_RED, CLR_AMBER
    t = _THEMES[_current_theme["name"]]
    CLR_BG     = t["bg"]
    CLR_CARD   = t["card"]
    CLR_BORDER = t["border"]
    CLR_MUTED  = t["muted"]
    CLR_DARK   = t["dark"]
    CLR_TEXT   = t["text"]
    CLR_BLUE   = t["blue"]
    CLR_GREEN  = t["green"]
    CLR_RED    = t["red"]
    CLR_AMBER  = t["amber"]


_update_clr()   # initialise with light theme
CLR_AMBER = "#92400e"

# -------------------- SETTINGS --------------------
APP_VERSION = "3.5"
STORE_NAME = "RETAIL POS SYSTEM"
LOW_STOCK_LEVEL = _config.LOW_STOCK_THRESHOLD if _SYNC_AVAILABLE else 5
DB_FILE = "pos.db"
RECEIPT_WIDTH = 46
DATE_FORMAT = "%Y-%m-%d"
REPORT_REFRESH_MS = 2000
MANAGER_PIN = ""
BACKUP_FOLDER = "backups"


# -------------------- DATABASE --------------------
connection = sqlite3.connect(DB_FILE)
# Enable WAL mode so the sync thread and UI thread don't block each other
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("PRAGMA foreign_keys = ON")
cursor = connection.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        selling_price REAL NOT NULL
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_date TEXT NOT NULL,
        total_amount REAL NOT NULL
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS sale_items (
        sale_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price REAL NOT NULL,
        line_total REAL NOT NULL,
        FOREIGN KEY (sale_id) REFERENCES sales(sale_id),
        FOREIGN KEY (product_id) REFERENCES products(product_id)
    )
""")


def add_column_if_missing(table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = [row[1] for row in cursor.fetchall()]
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


add_column_if_missing("sales", "amount_received", "REAL NOT NULL DEFAULT 0")
add_column_if_missing("sales", "change_given",    "REAL NOT NULL DEFAULT 0")
add_column_if_missing("sales", "status",          "TEXT NOT NULL DEFAULT 'completed'")
add_column_if_missing("sales", "voided_at",       "TEXT")
add_column_if_missing("sales", "void_reason",     "TEXT")
add_column_if_missing("products", "ordering_price",  "REAL")
add_column_if_missing("products", "units_per_pack",  "INTEGER NOT NULL DEFAULT 1")
add_column_if_missing("sale_items", "unit_cost",     "REAL")

# ── Settings table ────────────────────────────────────────────────────────────
cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
""")
cursor.execute(
    "INSERT OR IGNORE INTO settings (key,value) VALUES ('low_stock_threshold','5')"
)
connection.commit()


def get_setting(key, default=None):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def save_setting(key, value):
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
        (key, str(value))
    )
    connection.commit()


# Reload LOW_STOCK_LEVEL from settings now that the DB is ready
LOW_STOCK_LEVEL = int(get_setting("low_stock_threshold", LOW_STOCK_LEVEL))

connection.commit()


# -------------------- HELPERS --------------------
def money(value):
    """Round to 2 decimal places using standard ROUND_HALF_UP."""
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_number(text, whole=False):
    """Parse a numeric string, raising ValueError with a message on bad input."""
    text = str(text).strip()
    if not text:
        raise ValueError("Value cannot be empty.")
    try:
        value = int(text) if whole else float(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a valid {'whole number' if whole else 'number'}.")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Value must be a positive number.")
    return value


def unit_cost_of(ordering_price, units_per_pack):
    if ordering_price is None:
        return None
    return round(ordering_price / max(units_per_pack or 1, 1), 6)


def fmt_unit(value):
    text = f"{value:.4f}".rstrip("0")
    whole, _, fraction = text.partition(".")
    return f"{whole}.{fraction.ljust(2, '0')}"


def fmt_or_dash(value, unit=False):
    if value is None:
        return "—"
    return fmt_unit(value) if unit else f"{value:.2f}"


def fmt_percent(profit, revenue):
    if profit is None or not revenue:
        return "—"
    return f"{profit / revenue * 100:.1f}%"


def fmt_money(value):
    return f"{float(value):,.2f}"


def money_or_dash(value):
    return "—" if value is None else fmt_money(value)


def nice_ceiling(value):
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10.0 * magnitude


def change_text(current, previous, versus):
    """Return a plain-English comparison string and a colour.

    Examples:
        "Up 25% vs yesterday"   (green)
        "Down 10% vs prev 7 days"  (red)
        "Same as yesterday"  (grey)
        "No data to compare"  (grey)
    """
    if not previous:
        return "No previous data", CLR_MUTED

    change = (current - previous) / abs(previous) * 100

    if abs(change) < 0.5:                     # treat anything < 0.5 % as flat
        return f"Same as {versus}", CLR_MUTED

    pct = abs(change)
    # Express small changes as "Up 3%" and larger ones more naturally
    if pct < 1:
        readable = f"{pct:.1f}%"
    elif pct < 10:
        readable = f"{pct:.0f}%"
    elif pct < 100:
        readable = f"{pct:.0f}%"
    else:
        readable = f"{pct:.0f}%"             # e.g. "Up 220% vs yesterday"

    if change > 0:
        return f"▲  Up {readable} vs {versus}", "#166534"
    else:
        return f"▼  Down {readable} vs {versus}", "#b91c1c"


def sort_key(text):
    cleaned = str(text).replace(",", "").replace("%", "").strip()
    try:
        return (1, float(cleaned), "")
    except ValueError:
        pass
    if cleaned in ("", "—"):
        return (0, 0.0, "")
    return (2, 0.0, cleaned.lower())


def make_table(parent, columns, widths=None, numeric=(), left=(),
               stripe=False, sortable=False):
    frame = ttk.Frame(parent)
    table = ttk.Treeview(frame, columns=columns, show="headings")
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)

    labels = {column: column for column in columns}
    sort_state = {"column": None, "reverse": False}

    def show_headings():
        for column in columns:
            arrow = ""
            if sortable and sort_state["column"] == column:
                arrow = "  ▼" if sort_state["reverse"] else "  ▲"
            table.heading(column, text=labels[column] + arrow)

    def refresh_view():
        column = sort_state["column"]
        if sortable and column is not None:
            rows = [
                (sort_key(table.set(item, column)), item)
                for item in table.get_children("")
            ]
            rows.sort(key=lambda pair: pair[0], reverse=sort_state["reverse"])
            for index, (_, item) in enumerate(rows):
                table.move(item, "", index)
        if stripe:
            for index, item in enumerate(table.get_children("")):
                tags = table.item(item, "tags")
                tags = tags.split() if isinstance(tags, str) else list(tags)
                tags = [tag for tag in tags if tag != "odd"]
                if index % 2:
                    tags.append("odd")
                table.item(item, tags=tuple(tags))

    def sort_by(column):
        if sort_state["column"] == column:
            sort_state["reverse"] = not sort_state["reverse"]
        else:
            sort_state["column"] = column
            sort_state["reverse"] = False
        show_headings()
        refresh_view()

    def set_heading(column, text):
        labels[column] = text
        show_headings()

    for index, column in enumerate(columns):
        anchor = (
            "e" if column in numeric else "w" if column in left else "center"
        )
        table.heading(column, text=column, anchor=anchor)
        table.column(
            column,
            width=widths[index] if widths else 120,
            anchor=anchor
        )
        if sortable:
            table.heading(column, command=lambda c=column: sort_by(c))

    if stripe:
        table.tag_configure("odd", background="#f3f4f6")

    table.refresh_view = refresh_view
    table.set_heading  = set_heading

    table.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # Register for theme recolouring
    try:
        _all_treeviews.append(table)
    except NameError:
        pass   # _all_treeviews not defined yet during early startup

    return frame, table


def export_table_csv(table, columns, default_name, parent):
    rows = [table.item(item, "values") for item in table.get_children("")]
    if not rows:
        messagebox.showinfo("Nothing to Export", "The table is empty.", parent=parent)
        return
    path = filedialog.asksaveasfilename(
        parent=parent, defaultextension=".csv", initialfile=default_name,
        filetypes=[("CSV (Excel)", "*.csv"), ("All files", "*.*")]
    )
    if not path:
        return
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(columns)
            writer.writerows(rows)
    except OSError as error:
        messagebox.showerror("Export Failed", str(error), parent=parent)
        return
    messagebox.showinfo("Exported", f"Saved {len(rows)} rows to:\n{path}", parent=parent)


def clear_table(table):
    for item in table.get_children():
        table.delete(item)


product_listeners = []


def register_listener(window, callback):
    product_listeners.append(callback)

    def on_destroy(event):
        if event.widget is window and callback in product_listeners:
            product_listeners.remove(callback)

    window.bind("<Destroy>", on_destroy, add="+")


def notify_products_changed():
    for callback in list(product_listeners):
        try:
            callback()
        except tk.TclError:
            pass
        except Exception:
            traceback.print_exc()


def get_stock(product_id):
    cursor.execute(
        "SELECT quantity FROM products WHERE product_id = ?", (product_id,)
    )
    row = cursor.fetchone()
    return row[0] if row else 0



# -------------------- MAIN WINDOW --------------------
root = tk.Tk()
root.title(f"RetailPOS - Point of Sale System (v{APP_VERSION})")
root.geometry("1280x800")
root.minsize(900, 600)
root.configure(bg=CLR_BG)

# Detect screen size and set a flag for tablet-like screens
_sw = root.winfo_screenwidth()
_sh = root.winfo_screenheight()
# A tablet is typically 768-1366px wide; also covers smaller laptop screens.
IS_TABLET = _sw <= 1366 or _sh <= 900

try:
    root.state("zoomed")
except tk.TclError:
    try:
        root.attributes("-zoomed", True)
    except tk.TclError:
        pass


def setup_styles():
    """Configure ttk styles — touch-friendly sizing for tablet use."""
    style = ttk.Style()
    style.theme_use("clam")

    # On a tablet/small screen use larger tap targets
    _btn_pad   = (14, 10) if IS_TABLET else (11, 7)
    _btn_bold  = (14, 11) if IS_TABLET else (13, 9)
    _row_h     = 38       if IS_TABLET else 32
    _tab_pad   = (18, 10) if IS_TABLET else (15, 8)
    _entry_pad = 8        if IS_TABLET else 6

    style.configure("TFrame",       background=CLR_BG)
    style.configure("TLabel",       font=FONT_NORMAL, background=CLR_BG)
    style.configure("TEntry",       font=FONT_NORMAL, padding=_entry_pad)
    style.configure("TCombobox",    font=FONT_NORMAL, padding=_entry_pad - 1)
    style.configure("TRadiobutton", font=FONT_NORMAL, background=CLR_BG)
    style.configure("TCheckbutton", font=FONT_NORMAL, background=CLR_BG)
    style.configure("TSeparator",   background=CLR_BORDER)

    # Generic button
    style.configure(
        "TButton",
        font=FONT_NORMAL, padding=_btn_pad, relief="flat",
        background="#e5e7eb", foreground=CLR_DARK, borderwidth=0
    )
    style.map("TButton",
              background=[("active", "#d1d5db"), ("pressed", "#9ca3af")],
              foreground=[("disabled", CLR_MUTED)])

    # Accent – primary action (blue)
    style.configure(
        "Accent.TButton",
        font=FONT_BOLD, padding=_btn_bold,
        background=CLR_BLUE, foreground="white", borderwidth=0
    )
    style.map("Accent.TButton",
              background=[("active", "#1d4ed8"), ("pressed", "#1e40af")],
              foreground=[("disabled", "#93c5fd")])

    # Success – complete / confirm (green)
    style.configure(
        "Success.TButton",
        font=FONT_BOLD, padding=_btn_bold,
        background=CLR_GREEN, foreground="white", borderwidth=0
    )
    style.map("Success.TButton",
              background=[("active", "#15803d"), ("pressed", "#166534")],
              foreground=[("disabled", "#86efac")])

    # Danger – delete / reverse (red)
    style.configure(
        "Danger.TButton",
        font=FONT_BOLD, padding=_btn_bold,
        background=CLR_RED, foreground="white", borderwidth=0
    )
    style.map("Danger.TButton",
              background=[("active", "#b91c1c"), ("pressed", "#991b1b")],
              foreground=[("disabled", "#fca5a5")])

    # Treeview — taller rows for touch / finger tap accuracy
    style.configure(
        "Treeview",
        font=FONT_NORMAL, rowheight=_row_h,
        background=CLR_CARD, fieldbackground=CLR_CARD, foreground=CLR_TEXT
    )
    style.configure(
        "Treeview.Heading",
        font=FONT_BOLD, background="#e5e7eb", foreground=CLR_DARK,
        relief="flat", padding=(5, 8)
    )
    style.map("Treeview",
              background=[("selected", "#bfdbfe")],
              foreground=[("selected", CLR_DARK)])

    # Notebook — larger tabs on tablet
    style.configure("TNotebook",     background=CLR_BG, borderwidth=0)
    style.configure("TNotebook.Tab", font=FONT_NORMAL, padding=_tab_pad,
                    background="#e5e7eb", foreground=CLR_DARK)
    style.map("TNotebook.Tab",
              background=[("selected", CLR_CARD)],
              foreground=[("selected", CLR_BLUE)])

    # Scrollbar — wider on tablet so it's easier to drag
    _sb_w = 18 if IS_TABLET else 14
    style.configure("TScrollbar", background="#e5e7eb", troughcolor=CLR_BG,
                    arrowsize=_sb_w, width=_sb_w)
    style.configure("TPanedwindow", background=CLR_BG)


setup_styles()


def apply_theme():
    """Switch between light and dark mode live — no restart needed."""
    _update_clr()

    t = _THEMES[_current_theme["name"]]
    is_dark = _current_theme["name"] == "dark"

    # ── ttk styles ────────────────────────────────────────────────────────
    style = ttk.Style()

    _bp   = (14, 10) if IS_TABLET else (11, 7)
    _bp2  = (14, 11) if IS_TABLET else (13, 9)
    _rowh = 38       if IS_TABLET else 32

    style.configure("TFrame",       background=t["bg"])
    style.configure("TLabel",       font=FONT_NORMAL, background=t["bg"],
                    foreground=t["text"])
    style.configure("TEntry",       font=FONT_NORMAL,
                    fieldbackground=t["entry_bg"],
                    foreground=t["entry_fg"])
    style.configure("TCombobox",    font=FONT_NORMAL,
                    fieldbackground=t["entry_bg"],
                    foreground=t["entry_fg"])
    style.configure("TRadiobutton", font=FONT_NORMAL, background=t["bg"],
                    foreground=t["text"])
    style.configure("TCheckbutton", font=FONT_NORMAL, background=t["bg"],
                    foreground=t["text"])
    style.configure("TSeparator",   background=t["border"])

    style.configure("TButton",
                    font=FONT_NORMAL, padding=_bp, relief="flat",
                    background=t["btn_bg"], foreground=t["btn_fg"],
                    borderwidth=0)
    style.map("TButton",
              background=[("active", t["btn_hover"]), ("pressed", t["border"])],
              foreground=[("disabled", t["muted"])])

    style.configure("Accent.TButton",
                    font=FONT_BOLD, padding=_bp2,
                    background=t["blue"], foreground="white", borderwidth=0)
    style.map("Accent.TButton",
              background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])

    style.configure("Success.TButton",
                    font=FONT_BOLD, padding=_bp2,
                    background=t["green"], foreground="white", borderwidth=0)
    style.map("Success.TButton",
              background=[("active", "#15803d"), ("pressed", "#166534")])

    style.configure("Danger.TButton",
                    font=FONT_BOLD, padding=_bp2,
                    background=t["red"], foreground="white", borderwidth=0)
    style.map("Danger.TButton",
              background=[("active", "#b91c1c"), ("pressed", "#991b1b")])

    style.configure("Treeview",
                    font=FONT_NORMAL, rowheight=_rowh,
                    background=t["tree_bg"],
                    fieldbackground=t["tree_bg"],
                    foreground=t["text"])
    style.configure("Treeview.Heading",
                    font=FONT_BOLD, background=t["btn_bg"],
                    foreground=t["text"], relief="flat", padding=(5, 8))
    style.map("Treeview",
              background=[("selected", t["tree_sel"])],
              foreground=[("selected", t["tree_sel_fg"])])
    style.tag_configure = lambda *a, **kw: None   # tags set per-table

    style.configure("TNotebook",     background=t["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", font=FONT_NORMAL,
                    padding=(15, 8) if not IS_TABLET else (18, 10),
                    background=t["tab_bg"], foreground=t["muted"])
    style.map("TNotebook.Tab",
              background=[("selected", t["tab_sel"])],
              foreground=[("selected", t["blue"])])

    _sb_w = 18 if IS_TABLET else 14
    style.configure("TScrollbar", background=t["btn_bg"],
                    troughcolor=t["bg"], arrowsize=_sb_w, width=_sb_w)
    style.configure("TPanedwindow", background=t["bg"])

    # ── tk widgets — walk every widget in the main window ─────────────────
    root.configure(bg=t["bg"])
    nav_bar.configure(bg=t["nav_bg"])

    def _walk(widget):
        try:
            cls = widget.winfo_class()
            if cls == "Frame":
                # nav_bar stays dark regardless
                if widget is not nav_bar:
                    try:
                        widget.configure(bg=t["bg"])
                    except tk.TclError:
                        pass
            elif cls == "Label":
                try:
                    cur_bg = widget.cget("bg")
                    # keep coloured labels (green totals, etc.) — only
                    # update labels whose bg matches the old theme bg/card
                    old_bg_light = (_THEMES["light"]["bg"],
                                    _THEMES["light"]["card"],
                                    _THEMES["light"]["nav_bg"])
                    old_bg_dark  = (_THEMES["dark"]["bg"],
                                    _THEMES["dark"]["card"],
                                    _THEMES["dark"]["nav_bg"])
                    if cur_bg in old_bg_light or cur_bg in old_bg_dark:
                        widget.configure(bg=t["bg"], fg=t["text"])
                except tk.TclError:
                    pass
            elif cls == "Button":
                try:
                    cur = widget.cget("bg")
                    # keep red Exit and coloured action buttons
                    if cur not in ("#991b1b", t["red"], t["green"],
                                   t["blue"], "#b45309", "#0f766e",
                                   "#7c3aed"):
                        if widget is not _theme_btn:
                            widget.configure(
                                bg=t["nav_bg"], fg=t["nav_fg"],
                                activebackground="#374151"
                            )
                except tk.TclError:
                    pass
            elif cls == "Canvas":
                try:
                    widget.configure(bg=t["card"])
                except tk.TclError:
                    pass
        except tk.TclError:
            pass

        for child in widget.winfo_children():
            _walk(child)

    _walk(root)

    # update nav buttons specifically
    for name, btn in nav_buttons.items():
        active = name == current_page.get("key")
        btn.configure(
            bg=t["nav_active"] if active else t["nav_bg"],
            fg="white",
            activebackground="#374151"
        )

    # update theme toggle button label
    _theme_btn.configure(
        text="☀  Light" if is_dark else "🌙  Dark",
        bg=t["nav_bg"], fg="#94a3b8" if is_dark else "#9ca3af"
    )

    # re-colour tree row tags (all registered tables)
    for tv in _all_treeviews:
        try:
            tv.tag_configure("odd",  background=t["tree_odd"])
            tv.tag_configure("low",  background=t["row_low"])
            tv.tag_configure("out",  background=t["row_out"])
            tv.tag_configure("loss", foreground=t["red"])
        except tk.TclError:
            pass

    # trigger a full refresh of whichever page is open
    entry = pages.get(current_page.get("key"))
    if entry and entry.get("on_show"):
        try:
            entry["on_show"]()
        except Exception:
            pass


# -------------------- SINGLE-WINDOW NAVIGATION --------------------
# Registry of all Treeview widgets so apply_theme() can re-colour them
_all_treeviews = []

_orig_make_table = None   # patched below after make_table is defined
NAV_ITEMS = (
    ("home",      "  Home  (F1)  "),
    ("pos",       "  Point of Sale  (F2)  "),
    ("inventory", "  Inventory  (F3)  "),
    ("reports",   "  Reports  (F4)  "),
)
NAV_LABELS = dict(NAV_ITEMS)
NAV_KEYS = {
    "<F1>": "home", "<F2>": "pos",
    "<F3>": "inventory", "<F4>": "reports"
}
PAGE_TITLES = {
    "home":      "Home",
    "pos":       "Point of Sale",
    "inventory": "Inventory Management",
    "reports":   "Sales Reports",
}

pages = {}
nav_buttons = {}
current_page = {"key": None}

nav_bar = tk.Frame(root, bg=CLR_DARK, pady=0)
nav_bar.pack(fill="x", side="top")

# thin accent line under nav bar
tk.Frame(root, bg=CLR_BLUE, height=3).pack(fill="x", side="top")

content = tk.Frame(root, bg=CLR_BG)
content.pack(fill="both", expand=True)

home_page = tk.Frame(content, bg=CLR_BG)


def register_module(key, frame, on_show=None, on_product=None, unsaved=None):
    pages[key] = {
        "frame": frame, "on_show": on_show,
        "on_product": on_product, "unsaved": unsaved
    }


def set_nav_badge(key, text=""):
    label = NAV_LABELS[key]
    nav_buttons[key].config(text=f"{label}  [{text}]" if text else label)


def go_to(key, product_id=None, action=None):
    builders = {
        "pos":       build_point_of_sale,
        "inventory": build_inventory,
        "reports":   build_reports,
    }
    entry = pages.get(key)

    if entry is None:
        frame = tk.Frame(content)
        try:
            builders[key](frame)
        except Exception:
            traceback.print_exc()
            frame.destroy()
            pages.pop(key, None)
            messagebox.showerror(
                "Error",
                f"Could not open {PAGE_TITLES[key]}.\n"
                "Details were printed to the console.",
                parent=root
            )
            return
        entry = pages[key]

    if current_page["key"] != key:
        previous = pages.get(current_page["key"])
        if previous:
            previous["frame"].pack_forget()
        entry["frame"].pack(fill="both", expand=True)
        current_page["key"] = key
        root.title(f"{STORE_NAME} - {PAGE_TITLES[key]}  (v{APP_VERSION})")

        for name, button in nav_buttons.items():
            active = name == key
            button.config(
                bg=CLR_BLUE if active else CLR_DARK,
                font=(FONT_FAMILY, 10, "bold" if active else "normal")
            )

    if entry["on_show"]:
        entry["on_show"]()
    if product_id is not None and entry["on_product"]:
        entry["on_product"](product_id, action)


def bind_navigation_keys(window):
    for sequence, key in NAV_KEYS.items():
        window.bind(sequence, lambda event, k=key: go_to(k))


for _key, _label in NAV_ITEMS:
    _nav_pady = 13 if IS_TABLET else 11
    nav_buttons[_key] = tk.Button(
        nav_bar,
        text=_label,
        command=lambda k=_key: go_to(k),
        relief="flat", bd=0,
        padx=4, pady=_nav_pady,
        bg=CLR_DARK, fg="white",
        activebackground="#374151", activeforeground="white",
        font=(FONT_FAMILY, 11 if IS_TABLET else 10),
        cursor="hand2"
    )
    nav_buttons[_key].pack(side="left")

# Store name in the nav bar
tk.Label(
    nav_bar, text=f"  {STORE_NAME}  ",
    font=(FONT_FAMILY, 11, "bold"),
    bg=CLR_DARK, fg=CLR_MUTED
).pack(side="left", padx=(20, 0))

tk.Button(
    nav_bar,
    text="  Exit  ",
    command=lambda: close_application(),
    relief="flat", bd=0,
    padx=10, pady=11,
    bg="#991b1b", fg="white",
    activebackground=CLR_RED, activeforeground="white",
    font=(FONT_FAMILY, 10, "bold"),
    cursor="hand2"
).pack(side="right", padx=4)

# ── Dark / Light mode toggle ──────────────────────────────────────────────
def _toggle_theme():
    _current_theme["name"] = "dark" if _current_theme["name"] == "light" else "light"
    apply_theme()

_theme_btn = tk.Button(
    nav_bar,
    text="🌙  Dark",
    command=_toggle_theme,
    relief="flat", bd=0,
    padx=10, pady=11,
    bg=CLR_DARK, fg=CLR_MUTED,
    activebackground="#374151", activeforeground="white",
    font=(FONT_FAMILY, 10),
    cursor="hand2"
)
_theme_btn.pack(side="right", padx=2)

# ── Sync status indicator ─────────────────────────────────────────────────
# Shows ● Offline / ● Syncing / ● Synced / ● No cloud  in the nav bar.
# Clicking it opens the sync settings / info panel.
_sync_label = tk.Label(
    nav_bar,
    text="",
    font=(FONT_FAMILY, 9, "bold"),
    bg=CLR_DARK, fg=CLR_MUTED,
    cursor="hand2",
    padx=8, pady=4
)
_sync_label.pack(side="right", padx=(0, 4))

# ── Manual Sync Now button ────────────────────────────────────────────────
# ── Settings popup ───────────────────────────────────────────────────────────
def open_settings(parent=None):
    parent = parent or root
    win = tk.Toplevel(parent)
    win.title("Settings")
    win.resizable(False, False)
    win.grab_set()
    win.configure(bg=CLR_BG)
    t = _THEMES[_current_theme["name"]]

    tk.Label(win, text="⚙  Settings", font=FONT_HEADING,
             bg=CLR_BG, fg=CLR_DARK).pack(anchor="w", padx=24, pady=(16,4))
    ttk.Separator(win, orient="horizontal").pack(fill="x", padx=20, pady=4)

    frame = tk.Frame(win, bg=CLR_BG)
    frame.pack(fill="x", padx=24, pady=8)

    # Low stock threshold
    tk.Label(frame, text="Low stock alert when quantity ≤",
             font=FONT_NORMAL, bg=CLR_BG, fg=CLR_TEXT).grid(
        row=0, column=0, sticky="w", pady=6)
    low_var = tk.StringVar(value=str(LOW_STOCK_LEVEL))
    ttk.Entry(frame, textvariable=low_var, width=6).grid(
        row=0, column=1, padx=12, sticky="w")
    tk.Label(frame, text="units", font=FONT_NORMAL,
             bg=CLR_BG, fg=CLR_MUTED).grid(row=0, column=2, sticky="w")

    err_var = tk.StringVar()
    tk.Label(win, textvariable=err_var, font=FONT_SMALL,
             fg=CLR_RED, bg=CLR_BG).pack(anchor="w", padx=24)

    def save():
        global LOW_STOCK_LEVEL
        try:
            v = int(low_var.get().strip())
            if v < 1:
                raise ValueError
        except ValueError:
            err_var.set("Must be a whole number ≥ 1")
            return
        LOW_STOCK_LEVEL = v
        save_setting("low_stock_threshold", v)
        notify_products_changed()
        win.destroy()
        messagebox.showinfo("Settings saved",
            f"Low stock threshold set to {v} units.", parent=parent)

    btn_row = tk.Frame(win, bg=CLR_BG)
    btn_row.pack(pady=(4, 16))
    ttk.Button(btn_row, text="Save", style="Success.TButton",
               command=save).pack(side="left", padx=8)
    ttk.Button(btn_row, text="Cancel",
               command=win.destroy).pack(side="left", padx=8)


tk.Button(
    nav_bar, text="⚙ Settings",
    command=open_settings,
    relief="flat", bd=0,
    padx=10, pady=11,
    bg=CLR_DARK, fg=CLR_MUTED,
    activebackground="#374151", activeforeground="white",
    font=(FONT_FAMILY, 10),
    cursor="hand2"
).pack(side="right", padx=2)


def _manual_sync():
    if not _SYNC_AVAILABLE:
        messagebox.showinfo("Cloud Sync",
            "Cloud sync is not configured.\n"
            "Edit C:\\RetailPOS\\.env and add your Supabase credentials.",
            parent=root)
        return
    if not _config.is_configured():
        messagebox.showinfo("Cloud Sync",
            "Supabase credentials are missing.\n"
            "Edit C:\\RetailPOS\\.env and add SUPABASE_URL and SUPABASE_KEY.",
            parent=root)
        return
    _sync_btn.config(text="⟳ Syncing…", state="disabled")
    _sync.push_now()
    root.after(2000, lambda: _sync_btn.config(text="⟳ Sync Now", state="normal"))

_sync_btn = tk.Button(
    nav_bar,
    text="⟳ Sync Now",
    command=_manual_sync,
    relief="flat", bd=0,
    padx=10, pady=11,
    bg=CLR_DARK, fg="#60a5fa",
    activebackground="#374151", activeforeground="white",
    font=(FONT_FAMILY, 10),
    cursor="hand2"
)
_sync_btn.pack(side="right", padx=2)


def _update_sync_indicator():
    """Called from the sync background thread via root.after — thread-safe."""
    if not _SYNC_AVAILABLE:
        return
    st = _sync.get_status()
    state = st.get("state", "disabled")

    if state == "disabled":
        text, fg = "☁ No cloud", "#6b7280"
    elif state == "offline":
        text, fg = "● Offline", "#f97316"
    elif state == "syncing":
        text, fg = "⟳ Syncing…", "#60a5fa"
    elif state == "ok":
        pending = st.get("pending", 0)
        if pending:
            text, fg = f"● {pending} pending", "#fbbf24"
        else:
            last = st.get("last_sync")
            t    = last.strftime("%H:%M") if last else ""
            text, fg = f"✓ Synced {t}", "#34d399"
    elif state == "error":
        text, fg = "✗ Sync error", "#f87171"
    else:
        text, fg = "", "#6b7280"

    _sync_label.config(text=text, fg=fg)


def _on_sync_status_change():
    """Runs in sync thread — schedules UI update on main thread."""
    try:
        root.after(0, _update_sync_indicator)
        # When sync completes successfully, refresh inventory/product lists
        # so changes made on the dashboard appear on the shop PC immediately
        st = _sync.get_status()
        if st.get("state") == "ok":
            root.after(0, notify_products_changed)
    except Exception:
        pass


def _open_sync_info(event=None):
    """Small popup showing sync state detail."""
    if not _SYNC_AVAILABLE:
        messagebox.showinfo(
            "Cloud Sync",
            "Cloud sync is not configured.\n\n"
            "Edit  C:\\RetailPOS\\.env  and add your Supabase URL and key.",
            parent=root
        )
        return

    st  = _sync.get_status()
    cfg = _config.is_configured()

    lines = [
        f"Status:      {st.get('state', 'unknown').upper()}",
        f"Configured:  {'Yes' if cfg else 'No — edit config.py'}",
        f"Pending:     {st.get('pending', 0)} rows not yet synced",
    ]
    if st.get("last_sync"):
        lines.append(
            f"Last sync:   {st['last_sync'].strftime('%Y-%m-%d  %H:%M:%S')}"
        )
    if st.get("last_error"):
        lines.append(f"Last error:  {st['last_error']}")

    lines += [
        "",
        "The dashboard runs separately:",
        "  python dashboard/app.py",
        f"  then open  http://localhost:{_config.DASHBOARD_PORT}",
    ]

    messagebox.showinfo("Cloud Sync & Dashboard", "\n".join(lines), parent=root)


_sync_label.bind("<Button-1>", _open_sync_info)

register_module("home", home_page)


# -------------------- RECEIPTS --------------------
def build_receipt_text(sale_id, sale_date, items, total,
                       amount_received, change_given, void_note=None):
    line = "=" * RECEIPT_WIDTH
    dash = "-" * RECEIPT_WIDTH

    void_banner = ""
    if void_note:
        void_banner = (
            f"{'*** SALE REVERSED / CANCELLED ***'.center(RECEIPT_WIDTH)}\n"
            f"{void_note[:RECEIPT_WIDTH]}\n"
        )

    receipt = (
        f"{line}\n"
        f"{STORE_NAME.center(RECEIPT_WIDTH)}\n"
        f"{line}\n"
        f"Receipt Number: {sale_id}\n"
        f"Date: {sale_date}\n"
        f"{void_banner}"
        f"{dash}\n"
        f"Product              Qty   Price       Total\n"
        f"{dash}\n"
    )

    for name, qty, price, line_total in items:
        receipt += (
            f"{name[:24]:<24} "
            f"{qty:>3} "
            f"{price:>7.2f} "
            f"{line_total:>8.2f}\n"
        )

    receipt += (
        f"{dash}\n"
        f"TOTAL:                              {total:>10.2f}\n"
        f"AMOUNT RECEIVED:                    {amount_received:>10.2f}\n"
        f"CHANGE:                             {change_given:>10.2f}\n"
        f"{dash}\n"
        f"{'THANK YOU FOR SHOPPING!'.center(RECEIPT_WIDTH)}\n"
        f"{'PLEASE COME AGAIN'.center(RECEIPT_WIDTH)}\n"
        f"{line}\n"
    )
    return receipt


def print_text(text, parent):
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as file:
            file.write(text)
            path = file.name
        if sys.platform.startswith("win"):
            os.startfile(path, "print")
        else:
            subprocess.run(["lp", path], check=True)
    except Exception as error:
        messagebox.showerror("Print Error", str(error), parent=parent)


def show_receipt(sale_id, sale_date, items, total,
                 amount_received, change_given, void_note=None):
    receipt = build_receipt_text(
        sale_id, sale_date, items, total, amount_received, change_given,
        void_note
    )

    window = tk.Toplevel(root)
    window.title(f"Receipt #{sale_id}")
    window.geometry("500x660")
    window.resizable(False, False)
    window.transient(root)

    # Header
    hdr = tk.Frame(window, bg=CLR_DARK, pady=10)
    hdr.pack(fill="x")
    tk.Label(hdr, text=f"Receipt  #{sale_id}", font=FONT_HEADING,
             bg=CLR_DARK, fg="white").pack()

    text = tk.Text(
        window,
        font=("Courier New", 10),
        width=RECEIPT_WIDTH + 2,
        height=30,
        bg="#fafafa",
        relief="flat",
        padx=8, pady=8
    )
    text.pack(padx=12, pady=8, fill="both", expand=True)
    text.insert("1.0", receipt)
    text.config(state="disabled")

    sep = ttk.Separator(window, orient="horizontal")
    sep.pack(fill="x", padx=12)

    buttons = ttk.Frame(window)
    buttons.pack(pady=12)

    ttk.Button(
        buttons, text="🖨  Print Receipt", style="Accent.TButton",
        command=lambda: print_text(receipt, window)
    ).pack(side="left", padx=8)

    ttk.Button(
        buttons, text="Close",
        command=window.destroy
    ).pack(side="left", padx=8)


def show_saved_receipt(sale_id, parent):
    cursor.execute("""
        SELECT sale_date, total_amount, amount_received, change_given,
               status, voided_at, void_reason
        FROM sales
        WHERE sale_id = ?
    """, (sale_id,))
    sale = cursor.fetchone()

    if sale is None:
        messagebox.showerror("Not Found",
                             f"Sale #{sale_id} could not be found.",
                             parent=parent)
        return

    cursor.execute("""
        SELECT product_name, quantity, unit_price, line_total
        FROM sale_items
        WHERE sale_id = ?
        ORDER BY sale_item_id
    """, (sale_id,))

    sale_date, total, received, change, status, voided_at, reason = sale

    void_note = None
    if status == "voided":
        void_note = f"{voided_at} - {reason or 'no reason given'}"

    show_receipt(sale_id, sale_date, cursor.fetchall(),
                 total, received, change, void_note)


# -------------------- CANCEL / REVERSE SALES --------------------
def void_sale(sale_id, parent):
    cursor.execute("""
        SELECT sale_date, total_amount, status
        FROM sales WHERE sale_id = ?
    """, (sale_id,))
    sale = cursor.fetchone()

    if sale is None:
        messagebox.showerror("Not Found",
                             f"Sale #{sale_id} could not be found.",
                             parent=parent)
        return False

    sale_date, total, status = sale

    if status == "voided":
        messagebox.showinfo("Already Reversed",
                            f"Sale #{sale_id} has already been cancelled/reversed.",
                            parent=parent)
        return False

    if not messagebox.askyesno(
        "Confirm Reversal",
        f"Reverse sale #{sale_id} from {sale_date}?\n\n"
        "  • All items go back into inventory\n"
        f"  • {total:.2f} is deducted from sales and profit\n"
        f"  • You must refund the customer {total:.2f}\n\n"
        "This cannot be undone.",
        parent=parent
    ):
        return False

    reason = simpledialog.askstring(
        "Reason for Reversal",
        f"Reason for reversing sale #{sale_id}\n"
        "(e.g. customer changed mind, wrong item, price error):",
        parent=parent
    )

    if reason is None:
        return False

    reason = reason.strip()

    if not reason:
        messagebox.showerror("Reason Required",
                             "Please enter a reason so the reversal can be audited.",
                             parent=parent)
        return False

    try:
        cursor.execute("""
            SELECT product_id, quantity FROM sale_items WHERE sale_id = ?
        """, (sale_id,))
        for product_id, quantity in cursor.fetchall():
            cursor.execute("""
                UPDATE products SET quantity = quantity + ?, synced = 0
                WHERE product_id = ?
            """, (quantity, product_id))

        cursor.execute("""
            UPDATE sales
            SET status = 'voided', voided_at = ?, void_reason = ?
            WHERE sale_id = ? AND status = 'completed'
        """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), reason, sale_id))

        if cursor.rowcount != 1:
            raise ValueError("This sale was already reversed.")

        # Mark sale_items dirty so the void status propagates via sync
        cursor.execute(
            "UPDATE sale_items SET synced = 0 WHERE sale_id = ?", (sale_id,)
        )

        connection.commit()
        if _SYNC_AVAILABLE:
            _sync.push_now()
    except Exception as error:
        connection.rollback()
        messagebox.showerror("Reversal Failed", str(error), parent=parent)
        return False

    notify_products_changed()

    messagebox.showinfo(
        "Sale Reversed",
        f"Sale #{sale_id} has been reversed and the stock returned.\n\n"
        f"REFUND THE CUSTOMER:  {total:.2f}",
        parent=parent
    )
    return True


def open_sales_history(parent):
    window = tk.Toplevel(parent)
    window.title("Cancel / Reverse a Sale")
    window.geometry("900x580")
    window.transient(parent.winfo_toplevel())

    # Header
    hdr = tk.Frame(window, bg=CLR_DARK, pady=12)
    hdr.pack(fill="x")
    tk.Label(hdr, text="CANCEL / REVERSE A SALE", font=FONT_HEADING,
             bg=CLR_DARK, fg="white").pack()

    tk.Label(
        window,
        text="Showing the 50 most recent sales.  Enter a receipt number to find an older sale.",
        font=FONT_SMALL, fg=CLR_MUTED, bg=CLR_BG
    ).pack(pady=(8, 2))

    lookup_frame = ttk.Frame(window)
    lookup_frame.pack(pady=6)

    ttk.Label(lookup_frame, text="Receipt #:").pack(side="left", padx=6)
    receipt_entry = ttk.Entry(lookup_frame, width=14, font=FONT_NORMAL)
    receipt_entry.pack(side="left", padx=4)

    state = {"receipt": None}

    table_frame, table = make_table(
        window,
        ("Receipt #", "Date", "Products Sold", "Items", "Total", "Status"),
        (80, 150, 300, 60, 100, 100),
        numeric=("Receipt #", "Items", "Total"),
        left=("Products Sold",),
        stripe=True, sortable=True
    )
    table_frame.pack(fill="both", expand=True, padx=20, pady=5)
    table.tag_configure("voided", foreground="#9ca3af")

    def load(*_):
        clear_table(table)
        where, params = "", ()
        if state["receipt"] is not None:
            where, params = "WHERE s.sale_id = ?", (state["receipt"],)

        cursor.execute(f"""
            SELECT s.sale_id, s.sale_date,
                   COALESCE({PRODUCTS_SOLD}, ''),
                   COALESCE(SUM(si.quantity), 0),
                   s.total_amount, s.status
            FROM sales s
            LEFT JOIN sale_items si ON si.sale_id = s.sale_id
            {where}
            GROUP BY s.sale_id
            ORDER BY s.sale_id DESC
            LIMIT 50
        """, params)

        for sale_id, date, products, items, total, status in cursor.fetchall():
            table.insert(
                "", "end",
                values=(sale_id, date, products, items, fmt_money(total),
                        "Reversed" if status == "voided" else "Completed"),
                tags=("voided",) if status == "voided" else ()
            )
        table.refresh_view()

    def find():
        text = receipt_entry.get().strip()
        if not text:
            state["receipt"] = None
        else:
            try:
                state["receipt"] = parse_number(text, whole=True)
            except ValueError:
                messagebox.showerror("Invalid Receipt Number",
                                     "Enter the receipt number as a whole number.",
                                     parent=window)
                return
        load()
        if state["receipt"] is not None and not table.get_children():
            messagebox.showinfo("Not Found",
                                f"No sale with receipt number {state['receipt']}.",
                                parent=window)

    def show_recent():
        receipt_entry.delete(0, "end")
        state["receipt"] = None
        load()

    def selected_sale():
        selection = table.selection()
        if not selection:
            messagebox.showinfo("Select Sale",
                                "Select a sale in the table first.",
                                parent=window)
            return None
        return int(table.item(selection[0], "values")[0])

    def view_receipt(event=None):
        sale_id = selected_sale()
        if sale_id is not None:
            show_saved_receipt(sale_id, window)

    def reverse_selected():
        sale_id = selected_sale()
        if sale_id is not None:
            void_sale(sale_id, window)

    ttk.Button(lookup_frame, text="Find", style="Accent.TButton",
               command=find).pack(side="left", padx=4)
    ttk.Button(lookup_frame, text="Show Recent",
               command=show_recent).pack(side="left", padx=4)

    sep = ttk.Separator(window, orient="horizontal")
    sep.pack(fill="x", padx=20, pady=4)

    buttons = ttk.Frame(window)
    buttons.pack(pady=10)

    ttk.Button(buttons, text="View Receipt", style="Accent.TButton",
               command=view_receipt).pack(side="left", padx=8)
    ttk.Button(buttons, text="Cancel / Reverse Selected Sale",
               style="Danger.TButton",
               command=reverse_selected).pack(side="left", padx=8)

    receipt_entry.bind("<Return>", lambda event: find())
    table.bind("<Double-1>", view_receipt)

    register_listener(window, load)
    load()
    receipt_entry.focus_set()


# -------------------- INVENTORY --------------------
PRODUCT_COLUMNS = """
    product_id, product_name, quantity, selling_price,
    ordering_price, units_per_pack
"""


def open_product_form(parent, product=None):
    """Add a product, or edit one when `product` is given."""
    editing = product is not None

    form = tk.Toplevel(parent)
    form.title("Edit Product" if editing else "Add New Product")
    form.geometry("480x660")
    form.resizable(False, False)
    form.transient(parent.winfo_toplevel())
    form.grab_set()

    # Header bar
    hdr = tk.Frame(form, bg=CLR_BLUE, pady=12)
    hdr.pack(fill="x")
    tk.Label(
        hdr,
        text="EDIT PRODUCT" if editing else "ADD NEW PRODUCT",
        font=FONT_HEADING, bg=CLR_BLUE, fg="white"
    ).pack()

    body = ttk.Frame(form)
    body.pack(fill="both", expand=True, padx=24, pady=8)
    body.columnconfigure(1, weight=1)

    name_var     = tk.StringVar()
    units_var    = tk.StringVar(value="1")
    cost_var     = tk.StringVar()
    price_var    = tk.StringVar()
    quantity_var = tk.StringVar()

    entries = {}

    def add_field(row, label_text, variable, hint=None):
        ttk.Label(body, text=label_text, font=FONT_BOLD).grid(
            row=row * 2, column=0, columnspan=2,
            sticky="w", pady=(10, 0)
        )
        entry = ttk.Entry(body, textvariable=variable, font=FONT_NORMAL,
                          width=38)
        entry.grid(row=row * 2 + 1, column=0, columnspan=2,
                   sticky="ew", pady=(2, 0))
        if hint:
            ttk.Label(body, text=hint, font=FONT_SMALL,
                      foreground=CLR_MUTED).grid(
                row=row * 2 + 2, column=0, columnspan=2, sticky="w"
            )
        return entry

    name_entry = add_field(0, "Product Name *", name_var)
    add_field(1, "Units per Pack *",  units_var,
              "How many single items come in one pack  (use 1 if not bought in packs)")
    add_field(2, "Ordering Price per Pack *", cost_var,
              "What you pay your supplier for one pack")
    add_field(3, "Selling Price per Single Unit *", price_var,
              "What the customer pays for one item")
    add_field(4, "Quantity in Stock (single units) *", quantity_var,
              "Example: 2 packs of 12 = enter 24")

    # Profit preview card
    preview_var = tk.StringVar()
    preview_card = tk.Frame(body, bg="#f0fdf4", bd=1,
                            relief="solid", padx=12, pady=8)
    preview_card.grid(row=11, column=0, columnspan=2,
                      sticky="ew", pady=14)
    preview_label = tk.Label(
        preview_card,
        textvariable=preview_var,
        font=(FONT_FAMILY, 10),
        justify="left",
        bg="#f0fdf4"
    )
    preview_label.pack(anchor="w")

    def read_pricing():
        units = parse_number(units_var.get().strip(), whole=True)
        cost  = parse_number(cost_var.get().strip())
        price = parse_number(price_var.get().strip())
        if units < 1:
            raise ValueError
        return units, cost, price

    def update_preview(*_):
        try:
            units, cost, price = read_pricing()
        except ValueError:
            preview_var.set(
                "Fill in pack size, ordering price and selling price\n"
                "to see profit automatically."
            )
            preview_card.config(bg=CLR_CARD)
            preview_label.config(fg=CLR_MUTED, bg=CLR_CARD)
            return

        unit_cost   = cost / units
        unit_profit = price - unit_cost
        margin      = f"{unit_profit / price * 100:.1f}%" if price > 0 else "—"
        pack_profit = price * units - cost

        preview_var.set(
            f"Cost per unit:      {fmt_unit(unit_cost)}\n"
            f"Profit per unit:    {fmt_unit(unit_profit)}  ({margin} margin)\n"
            f"Profit per pack:    {pack_profit:.2f}"
        )
        is_loss = unit_profit < 0
        bg  = "#fff1f2" if is_loss else "#f0fdf4"
        clr = "#b91c1c" if is_loss else "#166534"
        preview_card.config(bg=bg)
        preview_label.config(fg=clr, bg=bg)

    for variable in (units_var, cost_var, price_var):
        variable.trace_add("write", update_preview)

    if editing:
        _, old_name, old_quantity, old_price, old_cost, old_units = product
        name_var.set(old_name)
        quantity_var.set(str(old_quantity))
        price_var.set(f"{old_price:.2f}")
        units_var.set(str(old_units or 1))
        cost_var.set("" if old_cost is None else f"{old_cost:.2f}")

    update_preview()
    name_entry.focus_set()

    def save_product():
        name = name_var.get().strip()

        if not (name and units_var.get().strip() and cost_var.get().strip()
                and price_var.get().strip() and quantity_var.get().strip()):
            messagebox.showerror("Missing Information",
                                 "Please fill in all fields marked with *.",
                                 parent=form)
            return

        try:
            units, cost, price = read_pricing()
            cost     = money(cost)
            price    = money(price)
            quantity = parse_number(quantity_var.get().strip(), whole=True)
        except ValueError:
            messagebox.showerror(
                "Invalid Input",
                "Units per pack must be a whole number ≥ 1, "
                "stock must be a whole number, "
                "and prices must be valid non-negative numbers.",
                parent=form
            )
            return

        product_id = product[0] if editing else -1

        cursor.execute("""
            SELECT product_id FROM products
            WHERE TRIM(LOWER(product_name)) = TRIM(LOWER(?)) AND product_id != ?
        """, (name, product_id))

        if cursor.fetchone():
            messagebox.showerror(
                "Duplicate Product",
                f"A product named '{name}' already exists.\n"
                "Use Restock or Edit on the existing product instead.",
                parent=form
            )
            return

        if price < cost / units and not messagebox.askyesno(
            "Selling Below Cost",
            f"The selling price ({price:.2f}) is lower than the cost per "
            f"unit ({fmt_unit(cost / units)}). You will lose money on every "
            "sale.\n\nSave anyway?",
            parent=form
        ):
            return

        if editing:
            cursor.execute("""
                UPDATE products
                SET product_name=?, quantity=?, selling_price=?,
                    ordering_price=?, units_per_pack=?
                WHERE product_id=?
            """, (name, quantity, price, cost, units, product_id))
        else:
            cursor.execute("""
                INSERT INTO products
                    (product_name, quantity, selling_price,
                     ordering_price, units_per_pack)
                VALUES (?, ?, ?, ?, ?)
            """, (name, quantity, price, cost, units))

        connection.commit()
        form.destroy()
        notify_products_changed()
        if _SYNC_AVAILABLE:
            _sync.push_now()

    sep = ttk.Separator(form, orient="horizontal")
    sep.pack(fill="x", padx=24, pady=(4, 0))

    btn_frame = ttk.Frame(form)
    btn_frame.pack(pady=12)

    ttk.Button(
        btn_frame, text="Save Product",
        style="Success.TButton", command=save_product
    ).pack(side="left", padx=8)

    ttk.Button(
        btn_frame, text="Cancel",
        command=form.destroy
    ).pack(side="left", padx=8)

    form.bind("<Return>", lambda event: save_product())


def open_restock_dialog(parent, product):
    """Add stock by packs or single units, optionally updating pack cost."""
    pid, name, quantity, _, pack_cost, units = product
    units = units or 1

    dialog = tk.Toplevel(parent)
    dialog.title("Restock Product")
    dialog.geometry("440x400")
    dialog.resizable(False, False)
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()

    # Header
    hdr = tk.Frame(dialog, bg=CLR_GREEN, pady=12)
    hdr.pack(fill="x")
    tk.Label(hdr, text="RESTOCK PRODUCT", font=FONT_HEADING,
             bg=CLR_GREEN, fg="white").pack()

    body = ttk.Frame(dialog)
    body.pack(fill="both", expand=True, padx=24, pady=10)

    ttk.Label(body, text=name, font=FONT_BOLD).pack(anchor="w")
    ttk.Label(body, text=f"Current stock:  {quantity} units",
              font=FONT_NORMAL, foreground=CLR_MUTED).pack(anchor="w", pady=(2, 10))

    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=6)

    mode_var = tk.StringVar(value="packs" if units > 1 else "units")

    if units > 1:
        mode_frame = ttk.Frame(body)
        mode_frame.pack(anchor="w", pady=(4, 8))
        ttk.Radiobutton(
            mode_frame,
            text=f"Packs received  (1 pack = {units} units)",
            variable=mode_var, value="packs"
        ).pack(anchor="w")
        ttk.Radiobutton(
            mode_frame,
            text="Single units received",
            variable=mode_var, value="units"
        ).pack(anchor="w", pady=2)

    ttk.Label(body, text="Amount received:", font=FONT_BOLD).pack(anchor="w", pady=(6, 2))
    amount_var = tk.StringVar()
    amount_entry = ttk.Entry(body, textvariable=amount_var, width=20,
                             font=FONT_NORMAL)
    amount_entry.pack(anchor="w", pady=(0, 4))

    result_var = tk.StringVar()
    tk.Label(body, textvariable=result_var, font=FONT_BOLD,
             fg=CLR_GREEN, bg=CLR_BG).pack(anchor="w")

    ttk.Label(body, text="Ordering price per pack (leave blank to keep current):",
              font=FONT_SMALL, foreground=CLR_MUTED).pack(anchor="w", pady=(10, 2))
    cost_var = tk.StringVar(
        value="" if pack_cost is None else f"{pack_cost:.2f}"
    )
    ttk.Entry(body, textvariable=cost_var, width=20,
              font=FONT_NORMAL).pack(anchor="w")

    def units_to_add():
        amount = parse_number(amount_var.get().strip(), whole=True)
        if amount < 1:
            raise ValueError
        return amount * units if mode_var.get() == "packs" else amount

    def update_result(*_):
        try:
            added = units_to_add()
            result_var.set(
                f"✓  Adds {added} units  →  new stock: {quantity + added}"
            )
        except ValueError:
            result_var.set("")

    amount_var.trace_add("write", update_result)
    mode_var.trace_add("write", update_result)
    amount_entry.focus_set()

    def save():
        try:
            added = units_to_add()
        except ValueError:
            messagebox.showerror("Invalid Amount",
                                 "Enter a whole number greater than 0.",
                                 parent=dialog)
            return

        new_cost = None
        if cost_var.get().strip():
            try:
                new_cost = money(parse_number(cost_var.get().strip()))
            except ValueError:
                messagebox.showerror("Invalid Price",
                                     "Ordering price must be a valid non-negative number.",
                                     parent=dialog)
                return

        cursor.execute("""
            UPDATE products
            SET quantity = quantity + ?,
                ordering_price = COALESCE(?, ordering_price),
                synced = 0
            WHERE product_id = ?
        """, (added, new_cost, pid))
        connection.commit()
        dialog.destroy()
        notify_products_changed()
        if _SYNC_AVAILABLE:
            _sync.push_now()

    sep = ttk.Separator(dialog, orient="horizontal")
    sep.pack(fill="x", padx=24, pady=(8, 0))

    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=12)

    ttk.Button(btn_frame, text="Add Stock",
               style="Success.TButton", command=save).pack(side="left", padx=8)
    ttk.Button(btn_frame, text="Cancel",
               command=dialog.destroy).pack(side="left", padx=8)

    dialog.bind("<Return>", lambda event: save())


def build_inventory(window):
    """Fill the Inventory page (a frame inside the main window)."""

    # ── Action bar — packed FIRST so it anchors to bottom correctly ───────
    act_bar = tk.Frame(window, bg=CLR_BG,
                       highlightbackground=CLR_BORDER, highlightthickness=1)
    act_bar.pack(fill="x", side="bottom")

    def export_inventory():
        export_table_csv(table, columns, "inventory.csv", window)

    # Buttons are added after table/helpers are defined (see end of function)

    # ── Header bar ────────────────────────────────────────────────────────
    hdr_bar = tk.Frame(window, bg=CLR_DARK)
    hdr_bar.pack(fill="x", side="top")
    tk.Label(hdr_bar, text="  📦  INVENTORY MANAGEMENT",
             font=(FONT_FAMILY, 15, "bold"),
             bg=CLR_DARK, fg="white").pack(side="left", pady=10)

    # ── Summary stat cards ────────────────────────────────────────────────
    cards_frame = tk.Frame(window, bg=CLR_BG)
    cards_frame.pack(fill="x", padx=14, pady=(10, 4), side="top")

    _stat_vars = {}

    def _make_stat(col, key, caption, icon, bg_accent):
        card = tk.Frame(cards_frame, bg=CLR_CARD,
                        highlightbackground=CLR_BORDER, highlightthickness=1)
        card.grid(row=0, column=col, sticky="nsew", padx=4)
        cards_frame.columnconfigure(col, weight=1, uniform="sc")

        tk.Frame(card, bg=bg_accent, width=5).pack(side="left", fill="y")

        inner = tk.Frame(card, bg=CLR_CARD)
        inner.pack(fill="both", expand=True, padx=8, pady=8)

        top_row = tk.Frame(inner, bg=CLR_CARD)
        top_row.pack(fill="x")
        tk.Label(top_row, text=icon, font=(FONT_FAMILY, 15),
                 bg=CLR_CARD, fg=bg_accent).pack(side="left")
        tk.Label(top_row, text=f"  {caption.upper()}",
                 font=(FONT_FAMILY, 8, "bold"),
                 fg=CLR_MUTED, bg=CLR_CARD).pack(side="left")

        val_var   = tk.StringVar(value="—")
        val_label = tk.Label(inner, textvariable=val_var,
                             font=(FONT_FAMILY, 18, "bold"),
                             fg=CLR_TEXT, bg=CLR_CARD, anchor="w")
        val_label.pack(fill="x", pady=(2, 0))

        sub_var = tk.StringVar(value="")
        tk.Label(inner, textvariable=sub_var,
                 font=(FONT_FAMILY, 9),
                 fg=CLR_MUTED, bg=CLR_CARD, anchor="w").pack(fill="x")

        _stat_vars[key] = (val_var, val_label, sub_var)

    def _set_stat(key, value, sub="", vclr=CLR_TEXT):
        vv, vl, sv = _stat_vars[key]
        vv.set(value)
        vl.config(fg=vclr)
        sv.set(sub)

    _make_stat(0, "total",   "Total Products",    "🗂",  CLR_BLUE)
    _make_stat(1, "instock", "In Stock",          "✅",  CLR_GREEN)
    _make_stat(2, "low",     "Low Stock",         "🟡",  "#d97706")
    _make_stat(3, "out",     "Out of Stock",      "🔴",  CLR_RED)
    _make_stat(4, "value",   "Stock Value (Cost)","💰",  "#7c3aed")
    _make_stat(5, "profit",  "Potential Profit",  "📈",  "#0f766e")

    # ── Legend ────────────────────────────────────────────────────────────
    legend = tk.Frame(window, bg=CLR_BG)
    legend.pack(fill="x", padx=16, pady=(0, 4), side="top")
    for colour, text in (
        ("#fff4cc", f"Yellow = low stock  (≤ {LOW_STOCK_LEVEL} units)"),
        ("#fde2e2", "Pink = out of stock"),
    ):
        swatch = tk.Frame(legend, bg=colour, width=16, height=16,
                          highlightbackground="#d1d5db", highlightthickness=1)
        swatch.pack(side="left", padx=(0, 4))
        tk.Label(legend, text=text, font=(FONT_FAMILY, 9),
                 fg=CLR_MUTED, bg=CLR_BG).pack(side="left", padx=(0, 16))
    tk.Label(legend, text="Red text = selling below cost    — = cost not set",
             font=(FONT_FAMILY, 9), fg=CLR_MUTED, bg=CLR_BG).pack(side="left")

    # ── Search / filter bar ───────────────────────────────────────────────
    search_frame = tk.Frame(window, bg=CLR_BG,
                            highlightbackground=CLR_BORDER,
                            highlightthickness=1)
    search_frame.pack(fill="x", padx=14, pady=(0, 6), side="top")

    tk.Label(search_frame, text="  🔍  Search:",
             font=FONT_NORMAL, bg=CLR_BG, fg=CLR_DARK).pack(
        side="left", padx=(4, 0), pady=7)
    search_var   = tk.StringVar()
    search_entry = ttk.Entry(search_frame, textvariable=search_var, width=28)
    search_entry.pack(side="left", padx=6, pady=7)

    tk.Label(search_frame, text="Filter:",
             font=FONT_NORMAL, bg=CLR_BG, fg=CLR_DARK).pack(
        side="left", padx=(12, 2))
    filter_var   = tk.StringVar(value="All products")
    filter_combo = ttk.Combobox(
        search_frame, textvariable=filter_var, state="readonly", width=22,
        values=("All products", "Low stock", "Out of stock",
                "Selling below cost", "Ordering price missing")
    )
    filter_combo.pack(side="left", padx=4)

    ttk.Button(
        search_frame, text="Clear",
        command=lambda: (search_var.set(""),
                         filter_var.set("All products"), refresh())
    ).pack(side="left", padx=6)

    shown_var = tk.StringVar()
    tk.Label(search_frame, textvariable=shown_var,
             font=(FONT_FAMILY, 10), bg=CLR_BG,
             fg=CLR_MUTED).pack(side="right", padx=12)

    # ── Product table — expands to fill remaining space ───────────────────
    columns = (
        "ID", "Product", "Stock", "Units/Pack",
        "Pack Cost", "Unit Cost", "Sell Price", "Profit/Unit", "Margin"
    )
    table_frame, table = make_table(
        window, columns,
        (48, 215, 72, 85, 90, 90, 90, 90, 72),
        numeric=("ID", "Stock", "Units/Pack", "Pack Cost",
                 "Unit Cost", "Sell Price", "Profit/Unit", "Margin"),
        left=("Product",), stripe=True, sortable=True
    )
    table_frame.pack(fill="both", expand=True, padx=14, pady=2)

    table.tag_configure("low",  background="#fff4cc")
    table.tag_configure("out",  background="#fde2e2")
    table.tag_configure("loss", foreground="#b91c1c")

    # ── Data / refresh ────────────────────────────────────────────────────
    def refresh(*_):
        clear_table(table)
        cursor.execute(f"""
            SELECT {PRODUCT_COLUMNS} FROM products
            ORDER BY product_name COLLATE NOCASE
        """)
        rows = cursor.fetchall()

        term = search_var.get().strip().lower()
        low = out = missing_cost = shown_count = in_stock = 0
        stock_cost = stock_retail = 0.0

        for pid, name, quantity, price, pack_cost, units in rows:
            unit_cost = unit_cost_of(pack_cost, units)

            if quantity == 0:
                out += 1
            elif quantity <= LOW_STOCK_LEVEL:
                low += 1
                in_stock += 1
            else:
                in_stock += 1

            if unit_cost is None:
                missing_cost += 1
            else:
                stock_cost   += quantity * unit_cost
                stock_retail += quantity * price

            if term and term not in name.lower() and term != str(pid):
                continue

            unit_profit = None if unit_cost is None else price - unit_cost

            wanted = filter_var.get()
            if wanted == "Low stock" and not 0 < quantity <= LOW_STOCK_LEVEL:
                continue
            if wanted == "Out of stock" and quantity != 0:
                continue
            if wanted == "Selling below cost" and not (
                unit_profit is not None and unit_profit < 0
            ):
                continue
            if wanted == "Ordering price missing" and unit_cost is not None:
                continue

            shown_count += 1
            tags = []
            if quantity == 0:
                tags.append("out")
            elif quantity <= LOW_STOCK_LEVEL:
                tags.append("low")
            if unit_profit is not None and unit_profit < 0:
                tags.append("loss")

            table.insert(
                "", "end",
                values=(
                    pid, name, quantity, units,
                    fmt_or_dash(pack_cost),
                    fmt_or_dash(unit_cost, unit=True),
                    f"{price:.2f}",
                    fmt_or_dash(unit_profit, unit=True),
                    fmt_percent(unit_profit, price)
                ),
                tags=tuple(tags)
            )

        table.refresh_view()
        shown_var.set(f"Showing {shown_count} of {len(rows)}")

        total     = len(rows)
        potential = stock_retail - stock_cost

        _set_stat("total",   str(total),
                  f"{missing_cost} need a cost price" if missing_cost else
                  "All costs entered ✓")
        _set_stat("instock", str(in_stock),
                  f"across {total} products")
        _set_stat("low",     str(low),
                  f"at or below {LOW_STOCK_LEVEL} units",
                  vclr="#d97706" if low else CLR_TEXT)
        _set_stat("out",     str(out),
                  "need restocking now",
                  vclr=CLR_RED if out else CLR_TEXT)
        _set_stat("value",   fmt_money(stock_cost),
                  "total cost of all stock")
        _set_stat("profit",  fmt_money(potential),
                  "if all stock sells at list price",
                  vclr=CLR_GREEN if potential >= 0 else CLR_RED)

    def get_selected():
        selection = table.selection()
        if not selection:
            messagebox.showinfo("Select Product",
                                "Select a product in the table first.",
                                parent=window)
            return None
        pid = int(table.item(selection[0], "values")[0])
        cursor.execute(f"""
            SELECT {PRODUCT_COLUMNS} FROM products WHERE product_id = ?
        """, (pid,))
        return cursor.fetchone()

    def edit_product(event=None):
        product = get_selected()
        if product:
            open_product_form(window, product)

    def restock_product():
        product = get_selected()
        if product:
            open_restock_dialog(window, product)

    def delete_product():
        product = get_selected()
        if not product:
            return
        pid, name = product[0], product[1]
        cursor.execute(
            "SELECT COUNT(*) FROM sale_items WHERE product_id = ?", (pid,)
        )
        if cursor.fetchone()[0] > 0:
            messagebox.showinfo(
                "Cannot Delete",
                f"'{name}' appears in past sales and cannot be deleted.\n\n"
                "Set its quantity to 0 instead.",
                parent=window
            )
            return
        if not messagebox.askyesno(
            "Delete Product", f"Permanently delete '{name}'?", parent=window
        ):
            return
        cursor.execute("DELETE FROM products WHERE product_id = ?", (pid,))
        # Track deletion so sync.py can remove it from Supabase
        cursor.execute("""
            INSERT OR REPLACE INTO deleted_products (product_id, deleted_at, synced)
            VALUES (?, datetime('now'), 0)
        """, (pid,))
        connection.commit()
        notify_products_changed()
        if _SYNC_AVAILABLE:
            _sync.push_now()

    def sell_selected():
        product = get_selected()
        if not product:
            return
        if product[2] <= 0:
            messagebox.showinfo(
                "Out of Stock",
                f"'{product[1]}' has no stock to sell.\n"
                "Use Restock Selected first.",
                parent=window
            )
            return
        go_to("pos", product_id=product[0])

    def focus_product(pid, action=None):
        search_var.set("")
        refresh()
        for item in table.get_children():
            if int(table.item(item, "values")[0]) == pid:
                table.selection_set(item)
                table.focus(item)
                table.see(item)
                break
        if action == "restock":
            restock_product()

    # ── Populate action bar buttons ───────────────────────────────────────
    # Lay out in two rows on tablet so nothing gets cut off.
    if IS_TABLET:
        row1 = tk.Frame(act_bar, bg=CLR_BG)
        row1.pack(fill="x", padx=6, pady=(6, 2))
        row2 = tk.Frame(act_bar, bg=CLR_BG)
        row2.pack(fill="x", padx=6, pady=(0, 6))
        btn_rows = [
            [   # top row — most-used actions
                ("➕  Add New Product", "Accent.TButton",  lambda: open_product_form(window)),
                ("✏  Edit Selected",   "TButton",          edit_product),
                ("📦  Restock Selected","TButton",          restock_product),
            ],
            [   # bottom row
                ("🗑  Delete Selected", "Danger.TButton",  delete_product),
                ("💳  Sell (POS)",      "Success.TButton", sell_selected),
                ("📄  Export to CSV",  "TButton",          export_inventory),
            ],
        ]
        for row_frame, defs in zip((row1, row2), btn_rows):
            for text, style, cmd in defs:
                ttk.Button(row_frame, text=text, style=style,
                           command=cmd).pack(side="left", padx=5)
    else:
        for text, style, cmd in [
            ("➕  Add New Product",  "Accent.TButton",  lambda: open_product_form(window)),
            ("✏  Edit Selected",    "TButton",          edit_product),
            ("📦  Restock Selected","TButton",          restock_product),
            ("🗑  Delete Selected", "Danger.TButton",   delete_product),
            ("💳  Sell (POS)",      "Success.TButton",  sell_selected),
            ("📄  Export to CSV",   "TButton",          export_inventory),
        ]:
            ttk.Button(act_bar, text=text, style=style,
                       command=cmd).pack(side="left", padx=6, pady=6)

    table.bind("<Double-1>", edit_product)
    table.bind("<Delete>",   lambda event: delete_product())
    filter_combo.bind("<<ComboboxSelected>>", refresh)
    search_var.trace_add("write", refresh)
    register_listener(window, refresh)
    register_module(
        "inventory", window,
        on_show=lambda: (refresh(), search_entry.focus_set()),
        on_product=focus_product
    )
    refresh()
    search_entry.focus_set()


# -------------------- POINT OF SALE --------------------
def build_point_of_sale(pos_window):
    """Fill the Point of Sale page (a frame inside the main window)."""

    # ── Pack bottom frames FIRST so expand=True on the cart doesn't hide them
    # Bottom action bar — Checkout + cart controls
    act_bar = tk.Frame(pos_window, bg=CLR_BG,
                       highlightbackground=CLR_BORDER, highlightthickness=1)
    act_bar.pack(fill="x", side="bottom")

    # Stock shortcuts row — just above the action bar
    stock_bar = tk.Frame(pos_window, bg=CLR_BG)
    stock_bar.pack(fill="x", side="bottom", padx=16, pady=(0, 2))

    # Total / summary bar — just above stock shortcuts
    total_bar = tk.Frame(pos_window, bg=CLR_DARK)
    total_bar.pack(fill="x", side="bottom")

    # ── Header ────────────────────────────────────────────────────────────
    header = tk.Frame(pos_window, bg=CLR_DARK)
    header.pack(fill="x", side="top")
    tk.Label(header, text="  🛒  POINT OF SALE",
             font=(FONT_FAMILY, 15, "bold"),
             bg=CLR_DARK, fg="white").pack(side="left", pady=10)
    today_var = tk.StringVar()
    tk.Label(header, textvariable=today_var,
             font=(FONT_FAMILY, 10, "bold"),
             bg=CLR_DARK, fg="#6ee7b7").pack(side="right", padx=16)

    # ── Product search / add row ───────────────────────────────────────────
    top_frame = tk.Frame(pos_window, bg=CLR_BG,
                         highlightbackground=CLR_BORDER, highlightthickness=1)
    top_frame.pack(fill="x", side="top", padx=14, pady=(8, 2))

    # Row 1: Search
    tk.Label(top_frame, text="  Search:", font=FONT_NORMAL,
             bg=CLR_BG, fg=CLR_MUTED).grid(
        row=0, column=0, padx=(8, 4), pady=(8, 4), sticky="w")
    search_var   = tk.StringVar()
    search_entry = ttk.Entry(top_frame, textvariable=search_var, width=32)
    search_entry.grid(row=0, column=1, padx=4, pady=(8, 4), sticky="w")
    tk.Label(top_frame, text="(type to filter)",
             font=FONT_SMALL, bg=CLR_BG, fg=CLR_MUTED).grid(
        row=0, column=2, padx=4, sticky="w")

    # Row 2: Product + Qty + Add
    tk.Label(top_frame, text="  Product:", font=FONT_NORMAL,
             bg=CLR_BG, fg=CLR_DARK).grid(
        row=1, column=0, padx=(8, 4), pady=(0, 8), sticky="w")
    product_var   = tk.StringVar()
    product_combo = ttk.Combobox(
        top_frame, textvariable=product_var, state="readonly", width=52)
    product_combo.grid(row=1, column=1, padx=4, pady=(0, 8), sticky="w")

    tk.Label(top_frame, text="Qty:", font=FONT_NORMAL,
             bg=CLR_BG, fg=CLR_DARK).grid(
        row=1, column=2, padx=(12, 4), pady=(0, 8))
    quantity_entry = ttk.Entry(top_frame, width=8)
    quantity_entry.insert(0, "1")
    quantity_entry.grid(row=1, column=3, padx=4, pady=(0, 8))

    ttk.Button(
        top_frame, text="➕  Add to Cart", style="Accent.TButton",
        command=lambda: add_to_cart()
    ).grid(row=1, column=4, padx=(10, 8), pady=(0, 8))

    # Flash status
    flash_var = tk.StringVar()
    tk.Label(pos_window, textvariable=flash_var,
             font=FONT_SMALL, fg=CLR_AMBER, bg=CLR_BG,
             anchor="w").pack(side="top", fill="x", padx=18)

    # ── Cart table — expands to fill remaining space ──────────────────────
    cart_columns = ("Product ID", "Product", "Qty", "Unit Price", "Line Total")
    cart_frame, cart_table = make_table(
        pos_window, cart_columns,
        (80, 330, 80, 120, 130),
        numeric=("Product ID", "Qty", "Unit Price", "Line Total")
    )
    cart_frame.pack(fill="both", expand=True,
                    side="top", padx=14, pady=(2, 0))

    # ── Total bar (packed bottom, already reserved) ───────────────────────
    total_var = tk.StringVar(value="Cart is empty")
    tk.Label(total_bar, textvariable=total_var,
             font=(FONT_FAMILY, 13, "bold"),
             bg=CLR_DARK, fg="white",
             anchor="w").pack(side="left", padx=16, pady=8)

    checkout_btn = tk.Button(
        total_bar,
        text="  ✅  CHECKOUT  (F9)  ",
        font=(FONT_FAMILY, 14, "bold"),
        bg=CLR_GREEN, fg="white",
        activebackground="#15803d", activeforeground="white",
        relief="flat", bd=0,
        cursor="hand2",
        command=lambda: checkout()
    )
    checkout_btn.pack(side="right", padx=12, pady=6, ipadx=10, ipady=6)

    # ── Cart controls (action bar, already reserved) ──────────────────────
    left_btns = tk.Frame(act_bar, bg=CLR_BG)
    left_btns.pack(side="left", padx=4, pady=6)

    for text, style, cmd in [
        ("+1",          "TButton",        lambda: nudge(1)),
        ("−1",          "TButton",        lambda: nudge(-1)),
        ("Change Qty",  "TButton",        lambda: change_quantity()),
        ("Remove Item", "Danger.TButton", lambda: remove_selected()),
        ("Clear Cart",  "Danger.TButton", lambda: clear_cart()),
    ]:
        ttk.Button(left_btns, text=text, style=style,
                   command=cmd).pack(side="left", padx=3)

    right_btns = tk.Frame(act_bar, bg=CLR_BG)
    right_btns.pack(side="right", padx=4, pady=6)

    for text, cmd in [
        ("🔄 Refresh",          lambda: load_products()),
        ("↩  Cancel a Sale",    lambda: open_sales_history(pos_window)),
    ]:
        ttk.Button(right_btns, text=text,
                   command=cmd).pack(side="left", padx=3)

    # ── Stock shortcuts (bar already reserved) ────────────────────────────
    tk.Label(stock_bar, text="Stock:",
             font=FONT_SMALL, fg=CLR_MUTED, bg=CLR_BG).pack(
        side="left", padx=(0, 6))

    # (commands filled in after helpers are defined)

    # ── All shared state ──────────────────────────────────────────────────
    cart         = {}
    all_products = []
    product_data = {}

    def cart_total():
        return money(sum(
            money(item["qty"] * item["price"]) for item in cart.values()
        ))

    def label_for(row):
        return (f"{row[0]} | {row[1]} | "
                f"Stock: {row[2]} | Price: {row[3]:.2f}")

    def apply_filter(*_):
        term = search_var.get().strip().lower()
        matches = [
            row for row in all_products
            if not term or term in row[1].lower() or term == str(row[0])
        ]
        product_data.clear()
        product_data.update({label_for(row): row for row in matches})
        product_combo["values"] = list(product_data)
        if len(matches) == 1:
            product_combo.set(label_for(matches[0]))
        elif product_var.get() not in product_data:
            product_combo.set("")

    def update_today():
        today = datetime.now().strftime(DATE_FORMAT)
        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(total_amount), 0) FROM sales
            WHERE sale_date >= ? AND status = 'completed'
        """, (today + " 00:00:00",))
        count, amount = cursor.fetchone()
        today_var.set(f"Today: {count} sales  |  {fmt_money(amount)}")

    def redraw_cart():
        clear_table(cart_table)
        for pid, item in cart.items():
            cart_table.insert(
                "", "end",
                values=(
                    pid, item["name"], item["qty"],
                    f'{item["price"]:.2f}',
                    f'{money(item["qty"] * item["price"]):.2f}'
                )
            )
        lines = len(cart)
        units = sum(item["qty"] for item in cart.values())
        if cart:
            total_var.set(
                f"  {lines} line(s)   {units} unit(s)   "
                f"Total:  {cart_total():,.2f}"
            )
        else:
            total_var.set("  Cart is empty")
        set_nav_badge("pos", f"{units} in cart" if cart else "")

    def load_products(*_):
        cursor.execute("""
            SELECT product_id, product_name, quantity, selling_price
            FROM products WHERE quantity > 0
            ORDER BY product_name COLLATE NOCASE
        """)
        all_products[:] = cursor.fetchall()
        for row in all_products:
            if row[0] in cart:
                cart[row[0]]["name"]  = row[1]
                cart[row[0]]["price"] = row[3]
        for pid in list(cart):
            if get_stock(pid) < cart[pid]["qty"]:
                cart[pid]["qty"] = max(get_stock(pid), 0)
                if cart[pid]["qty"] == 0:
                    del cart[pid]
        apply_filter()
        redraw_cart()
        update_today()

    def add_to_cart(event=None):
        selected = product_var.get()
        if selected not in product_data:
            messagebox.showerror("Select Product",
                                 "Please search for and select a product first.",
                                 parent=pos_window)
            return
        try:
            qty = parse_number(quantity_entry.get().strip(), whole=True)
            if qty <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Quantity",
                                 "Quantity must be a positive whole number.",
                                 parent=pos_window)
            return

        pid, name, _, price = product_data[selected]
        stock           = get_stock(pid)
        already_in_cart = cart.get(pid, {}).get("qty", 0)

        if qty + already_in_cart > stock:
            if messagebox.askyesno(
                "Insufficient Stock",
                f"Only {stock} units available "
                f"({already_in_cart} already in cart).\n\n"
                "Open Inventory to restock?",
                parent=pos_window
            ):
                go_to("inventory", product_id=pid, action="restock")
            return

        if pid in cart:
            cart[pid]["qty"] += qty
        else:
            cart[pid] = {"name": name, "qty": qty, "price": price}

        redraw_cart()

        remaining = stock - cart[pid]["qty"]
        if remaining <= LOW_STOCK_LEVEL:
            flash_var.set(
                f"⚠  Only {remaining} of '{name}' will remain after this sale.")
        else:
            flash_var.set(f"✓  Added {qty} × {name}")

        quantity_entry.delete(0, "end")
        quantity_entry.insert(0, "1")
        search_var.set("")
        product_combo.set("")
        search_entry.focus_set()

    def selected_cart_pid():
        sel = cart_table.selection()
        if not sel:
            messagebox.showinfo("Select Item",
                                "Select a row in the cart first.",
                                parent=pos_window)
            return None
        return int(cart_table.item(sel[0], "values")[0])

    def remove_selected():
        pid = selected_cart_pid()
        if pid is not None:
            cart.pop(pid, None)
            redraw_cart()

    def change_quantity(event=None):
        pid = selected_cart_pid()
        if pid is None:
            return
        item    = cart[pid]
        new_qty = simpledialog.askinteger(
            "Change Quantity",
            f"New quantity for '{item['name']}':",
            parent=pos_window,
            initialvalue=item["qty"], minvalue=1, maxvalue=1000000
        )
        if new_qty is None:
            return
        stock = get_stock(pid)
        if new_qty > stock:
            messagebox.showerror("Insufficient Stock",
                                 f"Only {stock} units available.",
                                 parent=pos_window)
            return
        item["qty"] = new_qty
        redraw_cart()

    def nudge(delta):
        sel = cart_table.selection()
        if not sel:
            messagebox.showinfo("Select Item", "Select a cart row first.",
                                parent=pos_window)
            return
        pid     = int(cart_table.item(sel[0], "values")[0])
        new_qty = cart[pid]["qty"] + delta
        if new_qty < 1:
            cart.pop(pid, None)
        elif new_qty > get_stock(pid):
            flash_var.set(f"⚠  Only {get_stock(pid)} in stock.")
            return
        else:
            cart[pid]["qty"] = new_qty
        redraw_cart()
        for item in cart_table.get_children():
            if int(cart_table.item(item, "values")[0]) == pid:
                cart_table.selection_set(item)

    def clear_cart():
        if cart and messagebox.askyesno(
            "Clear Cart", "Remove all items from the cart?",
            parent=pos_window
        ):
            cart.clear()
            redraw_cart()

    # ── CHECKOUT ──────────────────────────────────────────────────────────
    def checkout():
        if not cart:
            messagebox.showerror("Empty Cart",
                                 "Add products to the cart first.",
                                 parent=pos_window)
            return

        total = cart_total()

        pw = tk.Toplevel(pos_window)
        pw.title("Customer Payment")
        pw.geometry("480x560")
        pw.resizable(False, False)
        pw.transient(pos_window.winfo_toplevel())
        pw.grab_set()

        # Header
        tk.Frame(pw, bg=CLR_GREEN).pack(fill="x")
        hdr = tk.Frame(pw, bg=CLR_GREEN)
        hdr.pack(fill="x")
        tk.Label(hdr, text="CUSTOMER PAYMENT",
                 font=(FONT_FAMILY, 15, "bold"),
                 bg=CLR_GREEN, fg="white").pack(pady=12)

        # Amount due card
        due_card = tk.Frame(pw, bg="#f0fdf4",
                            highlightbackground="#bbf7d0",
                            highlightthickness=2)
        due_card.pack(fill="x", padx=24, pady=12)
        tk.Label(due_card, text="AMOUNT DUE",
                 font=(FONT_FAMILY, 9, "bold"),
                 fg=CLR_MUTED, bg="#f0fdf4").pack(pady=(10, 0))
        tk.Label(due_card, text=f"{total:,.2f}",
                 font=(FONT_FAMILY, 34, "bold"),
                 fg=CLR_GREEN, bg="#f0fdf4").pack(pady=(0, 10))

        body = ttk.Frame(pw)
        body.pack(fill="x", padx=24)

        ttk.Label(body, text="Amount received from customer:",
                  font=FONT_BOLD).pack(anchor="w", pady=(4, 2))
        amount_entry = ttk.Entry(body, width=28,
                                 font=(FONT_FAMILY, 15))
        amount_entry.pack(anchor="w", pady=(0, 4))
        amount_entry.focus_set()

        # Quick cash buttons
        notes = sorted({
            float(n) for n in (5, 10, 20, 50, 100, 200, 500, 1000)
            if n >= total
        })[:6]
        if notes:
            ttk.Label(body, text="Quick cash:",
                      font=FONT_SMALL,
                      foreground=CLR_MUTED).pack(anchor="w", pady=(4, 2))
            note_row = ttk.Frame(body)
            note_row.pack(anchor="w", pady=(0, 6))
            for note in notes:
                ttk.Button(
                    note_row, text=f"{note:,.0f}", width=9,
                    command=lambda n=note: quick_cash(n)
                ).pack(side="left", padx=2)

        # Change display
        change_var   = tk.StringVar(value="Change:  —")
        change_label = tk.Label(
            pw, textvariable=change_var,
            font=(FONT_FAMILY, 18, "bold"),
            fg=CLR_GREEN, bg=CLR_BG
        )
        change_label.pack(pady=6)

        def get_payment():
            try:
                received = money(parse_number(amount_entry.get().strip()))
            except ValueError:
                messagebox.showerror("Invalid Amount",
                                     "Enter a valid amount.",
                                     parent=pw)
                return None
            if received < total:
                messagebox.showerror("Insufficient Payment",
                                     "Amount received is less than the total.",
                                     parent=pw)
                return None
            return received, money(received - total)

        def calculate_change(event=None):
            p = get_payment()
            if p is not None:
                change_var.set(f"Change:  {p[1]:,.2f}")
                change_label.config(fg=CLR_GREEN if p[1] >= 0 else CLR_RED)

        def exact_amount():
            amount_entry.delete(0, "end")
            amount_entry.insert(0, f"{total:.2f}")
            calculate_change()

        def complete_sale():
            p = get_payment()
            if p is None:
                return
            amount_received, change_given = p

            if not messagebox.askyesno(
                "Confirm Sale",
                f"Total:     {total:.2f}\n"
                f"Received:  {amount_received:.2f}\n"
                f"Change:    {change_given:.2f}\n\n"
                "Complete this sale?",
                parent=pw
            ):
                return

            sale_date     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            receipt_items = []
            costs         = {}

            try:
                for pid, item in cart.items():
                    cursor.execute("""
                        SELECT quantity, ordering_price, units_per_pack
                        FROM products WHERE product_id = ?
                    """, (pid,))
                    row = cursor.fetchone()
                    if row is None or row[0] < item["qty"]:
                        raise ValueError(
                            f"Insufficient stock for {item['name']}.")
                    costs[pid] = unit_cost_of(row[1], row[2])

                cursor.execute("""
                    INSERT INTO sales
                        (sale_date, total_amount,
                         amount_received, change_given)
                    VALUES (?, ?, ?, ?)
                """, (sale_date, total, amount_received, change_given))

                sale_id = cursor.lastrowid

                for pid, item in cart.items():
                    line_total = money(item["qty"] * item["price"])
                    # Use quantity >= ? in the WHERE clause so the update
                    # fails (rowcount=0) if stock was concurrently depleted
                    cursor.execute("""
                        UPDATE products SET quantity = quantity - ?,
                                           synced = 0
                        WHERE product_id = ? AND quantity >= ?
                    """, (item["qty"], pid, item["qty"]))
                    if cursor.rowcount != 1:
                        raise ValueError(
                            f"Insufficient stock for {item['name']} "
                            "(stock changed during checkout).")
                    cursor.execute("""
                        INSERT INTO sale_items
                            (sale_id, product_id, product_name,
                             quantity, unit_price, line_total, unit_cost, synced)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """, (sale_id, pid, item["name"], item["qty"],
                          item["price"], line_total, costs[pid]))
                    receipt_items.append(
                        (item["name"], item["qty"],
                         item["price"], line_total)
                    )

                connection.commit()
                if _SYNC_AVAILABLE:
                    _sync.push_now()

            except Exception as error:
                connection.rollback()
                messagebox.showerror("Checkout Failed", str(error), parent=pw)
                return

            pw.destroy()
            cart.clear()
            redraw_cart()
            notify_products_changed()
            show_receipt(sale_id, sale_date, receipt_items,
                         total, amount_received, change_given)

        def quick_cash(amount):
            amount_entry.delete(0, "end")
            amount_entry.insert(0, f"{amount:.2f}")
            calculate_change()

        ttk.Separator(pw, orient="horizontal").pack(fill="x", padx=24, pady=4)

        ctrl_row = ttk.Frame(pw)
        ctrl_row.pack(pady=2)
        ttk.Button(ctrl_row, text="Exact Amount",
                   command=exact_amount).pack(side="left", padx=6)
        ttk.Button(ctrl_row, text="Calculate Change",
                   command=calculate_change).pack(side="left", padx=6)

        tk.Button(
            pw, text="  ✅  COMPLETE SALE  ",
            font=(FONT_FAMILY, 14, "bold"),
            bg=CLR_GREEN, fg="white",
            activebackground="#15803d", activeforeground="white",
            relief="flat", bd=0, cursor="hand2",
            command=complete_sale
        ).pack(pady=12, ipadx=16, ipady=8)

        amount_entry.bind("<Return>", calculate_change)

    # ── Stock shortcuts (bar already reserved) ────────────────────────────
    def chosen_product():
        row = product_data.get(product_var.get())
        if row is None:
            messagebox.showinfo("Select Product",
                                "Search for and select a product first.",
                                parent=pos_window)
        return row

    def restock_chosen():
        row = chosen_product()
        if row:
            go_to("inventory", product_id=row[0], action="restock")

    def show_chosen_in_inventory():
        row = chosen_product()
        if row:
            go_to("inventory", product_id=row[0])

    def focus_product(pid, action=None):
        load_products()
        row = next((r for r in all_products if r[0] == pid), None)
        if row is None:
            messagebox.showinfo(
                "Out of Stock",
                "That product has no stock. Restock it in Inventory first.",
                parent=pos_window)
            return
        search_var.set("")
        apply_filter()
        product_combo.set(label_for(row))
        quantity_entry.focus_set()
        quantity_entry.select_range(0, "end")

    for text, cmd in (
        ("📦 Restock Product",   restock_chosen),
        ("🔍 Show in Inventory", show_chosen_in_inventory),
        ("➕ Add New Product",   lambda: open_product_form(pos_window)),
        ("Inventory (F3)",       lambda: go_to("inventory")),
    ):
        ttk.Button(stock_bar, text=text,
                   command=cmd).pack(side="left", padx=4)

    # ── Key bindings ──────────────────────────────────────────────────────
    search_var.trace_add("write", apply_filter)
    search_entry.bind("<Return>",  lambda e: quantity_entry.focus_set())
    product_combo.bind("<<ComboboxSelected>>",
                       lambda e: quantity_entry.focus_set())
    quantity_entry.bind("<Return>", add_to_cart)
    cart_table.bind("<Double-1>",   change_quantity)
    cart_table.bind("<Delete>",     lambda e: remove_selected())
    cart_table.bind("<plus>",       lambda e: nudge(1))
    cart_table.bind("<minus>",      lambda e: nudge(-1))
    search_entry.bind("<Escape>",
                      lambda e: (search_var.set(""), product_combo.set("")))
    pos_window.winfo_toplevel().bind(
        "<F9>",
        lambda e: (checkout() if current_page["key"] == "pos" else None)
    )

    register_listener(pos_window, load_products)
    register_module(
        "pos", pos_window,
        on_show=lambda: (load_products(), search_entry.focus_set()),
        on_product=focus_product,
        unsaved=lambda: bool(cart)
    )
    load_products()
    search_entry.focus_set()


# -------------------- DELETE SALES DATA --------------------
def backup_database():
    os.makedirs(BACKUP_FOLDER, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path  = os.path.join(BACKUP_FOLDER, f"pos_backup_{stamp}.db")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(BACKUP_FOLDER, f"pos_backup_{stamp}_{counter}.db")
        counter += 1
    target = sqlite3.connect(path)
    try:
        connection.backup(target)
    finally:
        target.close()
    return path


def ask_manager_pin(parent):
    if not MANAGER_PIN:
        return True
    entered = simpledialog.askstring(
        "Manager PIN",
        "Enter the manager PIN to continue:",
        parent=parent, show="*"
    )
    if entered is None:
        return False
    if entered != MANAGER_PIN:
        messagebox.showerror("Wrong PIN", "Incorrect PIN.", parent=parent)
        return False
    return True


def ask_restore_stock(parent, description):
    return messagebox.askyesnocancel(
        "Return Items to Stock?",
        f"{description}\n\n"
        "Should the items be returned to inventory?\n\n"
        "YES  = return them to stock (test or mistaken sales)\n"
        "NO   = leave stock as it is (the goods really left the shop)\n"
        "CANCEL = do not delete anything",
        parent=parent
    )


def delete_sales(sale_ids, restore_stock):
    ids = list(sale_ids)
    if not ids:
        return
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        if not chunk:
            continue
        marks = ",".join("?" * len(chunk))
        if restore_stock:
            cursor.execute(f"""
                SELECT si.product_id, SUM(si.quantity)
                FROM sale_items si
                JOIN sales s ON s.sale_id = si.sale_id
                WHERE s.sale_id IN ({marks}) AND s.status = 'completed'
                GROUP BY si.product_id
            """, chunk)
            for product_id, quantity in cursor.fetchall():
                cursor.execute("""
                    UPDATE products SET quantity = quantity + ?,
                                       synced = 0
                    WHERE product_id = ?
                """, (quantity, product_id))
        cursor.execute(
            f"DELETE FROM sale_items WHERE sale_id IN ({marks})", chunk
        )
        cursor.execute(
            f"DELETE FROM sales WHERE sale_id IN ({marks})", chunk
        )


def run_deletion(sale_ids, restore_stock, parent):
    try:
        backup_path = backup_database()
    except Exception as error:
        messagebox.showerror(
            "Backup Failed",
            "Nothing was deleted, because a safety backup could not be "
            f"made:\n{error}",
            parent=parent
        )
        return False
    try:
        delete_sales(sale_ids, restore_stock)
        connection.commit()
    except Exception as error:
        connection.rollback()
        messagebox.showerror("Delete Failed", str(error), parent=parent)
        return False
    notify_products_changed()
    if _SYNC_AVAILABLE:
        _sync.push_now()
    messagebox.showinfo(
        "Deleted",
        f"{len(sale_ids)} sale(s) permanently deleted.\n\n"
        "A backup was saved to:\n"
        f"{backup_path}",
        parent=parent
    )
    return True


def delete_sale_record(sale_id, parent):
    cursor.execute("""
        SELECT sale_date, total_amount, status FROM sales WHERE sale_id = ?
    """, (sale_id,))
    sale = cursor.fetchone()
    if sale is None:
        messagebox.showerror("Not Found",
                             f"Sale #{sale_id} could not be found.",
                             parent=parent)
        return False
    sale_date, total, status = sale
    if not ask_manager_pin(parent):
        return False
    if status == "completed":
        restore = ask_restore_stock(
            parent,
            f"Permanently delete completed sale #{sale_id} "
            f"({sale_date}, {total:.2f})?"
        )
        if restore is None:
            return False
    else:
        if not messagebox.askyesno(
            "Delete Reversed Sale",
            f"Permanently delete the record of reversed sale #{sale_id}?\n\n"
            "Its stock was already returned when it was reversed.",
            parent=parent
        ):
            return False
        restore = False
    return run_deletion([sale_id], restore, parent)


def delete_sales_in_range(start_date, end_date, parent):
    span = (start_date + " 00:00:00", end_date + " 23:59:59")
    cursor.execute("""
        SELECT sale_id, status, total_amount FROM sales
        WHERE sale_date BETWEEN ? AND ?
    """, span)
    rows = cursor.fetchall()
    if not rows:
        messagebox.showinfo(
            "Nothing to Delete",
            f"There are no sales from {start_date} to {end_date}.",
            parent=parent
        )
        return False
    completed      = [row for row in rows if row[1] == "completed"]
    reversed_count = len(rows) - len(completed)
    amount         = money(sum(row[2] for row in completed))
    if not ask_manager_pin(parent):
        return False
    description = (
        f"You are about to PERMANENTLY delete {len(rows)} sale(s) from "
        f"{start_date} to {end_date}:\n"
        f"  - {len(completed)} completed sale(s) worth {amount:.2f}\n"
        f"  - {reversed_count} reversed sale(s)"
    )
    if completed:
        restore = ask_restore_stock(parent, description)
        if restore is None:
            return False
    else:
        if not messagebox.askyesno(
            "Delete Sales", f"{description}\n\nContinue?", parent=parent
        ):
            return False
        restore = False
    typed = simpledialog.askstring(
        "Final Confirmation",
        "This cannot be undone (a backup is saved first).\n\n"
        "Type DELETE in capital letters to confirm:",
        parent=parent
    )
    if typed is None or typed.strip() != "DELETE":
        messagebox.showinfo("Cancelled", "Nothing was deleted.", parent=parent)
        return False
    return run_deletion([row[0] for row in rows], restore, parent)


# -------------------- SALES REPORTS --------------------
PERIODS = {
    "Daily":   ("substr(s.sale_date, 1, 10)",                         "Date"),
    "Weekly":  ("date(substr(s.sale_date, 1, 10), 'weekday 1', '-6 days')",
                "Week Starting (Mon)"),
    "Monthly": ("substr(s.sale_date, 1, 7)",                          "Month"),
}

COSTED_COST    = "CASE WHEN si.unit_cost IS NOT NULL THEN si.quantity * si.unit_cost END"
COSTED_REVENUE = "CASE WHEN si.unit_cost IS NOT NULL THEN si.line_total END"
PRODUCTS_SOLD  = "GROUP_CONCAT(si.product_name || ' (' || si.quantity || ')', ', ')"


def build_reports(window):
    """Sales Reports page — clean, structured layout."""

    # ── 1. OUTER STRUCTURE ────────────────────────────────────────────────
    # window is divided into three rows (pack order):
    #   top_bar   – dark header  (fixed height)
    #   ctrl_bar  – date/filter controls  (fixed height)
    #   body      – everything else, expands to fill remaining space
    #   act_bar   – action buttons  (fixed height, packed last/bottom)

    # ── Header ────────────────────────────────────────────────────────────
    top_bar = tk.Frame(window, bg=CLR_DARK)
    top_bar.pack(fill="x", side="top")

    tk.Label(top_bar, text="  📊  SALES REPORTS",
             font=(FONT_FAMILY, 15, "bold"),
             bg=CLR_DARK, fg="white").pack(side="left", pady=10, padx=4)

    live_var = tk.StringVar(value="")
    tk.Label(top_bar, textvariable=live_var,
             font=(FONT_FAMILY, 10, "bold"),
             bg=CLR_DARK, fg="#6ee7b7").pack(side="right", padx=16)

    # ── Control / filter bar ─────────────────────────────────────────────
    ctrl_bar = tk.Frame(window, bg="#e8eaed",
                        highlightbackground=CLR_BORDER, highlightthickness=1)
    ctrl_bar.pack(fill="x", side="top")

    # left side – preset quick buttons
    preset_row = tk.Frame(ctrl_bar, bg="#e8eaed")
    preset_row.pack(side="left", padx=6, pady=4)

    # right side – custom date range + group
    date_row = tk.Frame(ctrl_bar, bg="#e8eaed")
    date_row.pack(side="right", padx=8, pady=4)

    tk.Label(date_row, text="From:", font=FONT_SMALL,
             bg="#e8eaed", fg=CLR_MUTED).pack(side="left")
    from_entry = ttk.Entry(date_row, width=12)
    from_entry.pack(side="left", padx=(2, 8))

    tk.Label(date_row, text="To:", font=FONT_SMALL,
             bg="#e8eaed", fg=CLR_MUTED).pack(side="left")
    to_entry = ttk.Entry(date_row, width=12)
    to_entry.pack(side="left", padx=(2, 8))

    ttk.Button(date_row, text="Apply", style="Accent.TButton",
               command=lambda: apply_custom_dates()).pack(side="left", padx=4)

    tk.Label(date_row, text="Group:", font=FONT_SMALL,
             bg="#e8eaed", fg=CLR_MUTED).pack(side="left", padx=(10, 2))
    group_var   = tk.StringVar(value="Daily")
    group_combo = ttk.Combobox(date_row, textvariable=group_var,
                               values=list(PERIODS), state="readonly", width=9)
    group_combo.pack(side="left")

    # ── Action bar (bottom) ───────────────────────────────────────────────
    # Pack BEFORE the body so it anchors to the bottom correctly.
    act_bar = tk.Frame(window, bg=CLR_BG,
                       highlightbackground=CLR_BORDER, highlightthickness=1)
    act_bar.pack(fill="x", side="bottom")

    # ── Body ─────────────────────────────────────────────────────────────
    # Everything between ctrl_bar and act_bar goes in this frame.
    body = tk.Frame(window, bg=CLR_BG)
    body.pack(fill="both", expand=True, side="top")

    # ── 2. KPI CARDS (inside body) ───────────────────────────────────────
    kpi_row = tk.Frame(body, bg=CLR_BG)
    kpi_row.pack(fill="x", padx=12, pady=(10, 6))

    cards = {}

    def make_card(col, key, caption, icon):
        card = tk.Frame(kpi_row, bg=CLR_CARD,
                        highlightbackground=CLR_BORDER, highlightthickness=1)
        card.grid(row=0, column=col, sticky="nsew", padx=4)
        kpi_row.columnconfigure(col, weight=1, uniform="kpi")

        head = tk.Frame(card, bg=CLR_CARD)
        head.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(head, text=icon, font=(FONT_FAMILY, 13),
                 bg=CLR_CARD, fg=CLR_MUTED).pack(side="left")
        tk.Label(head, text=f"  {caption.upper()}",
                 font=(FONT_FAMILY, 8, "bold"),
                 fg=CLR_MUTED, bg=CLR_CARD).pack(side="left")

        val_var   = tk.StringVar(value="—")
        val_label = tk.Label(card, textvariable=val_var,
                             font=(FONT_FAMILY, 18, "bold"),
                             fg=CLR_TEXT, bg=CLR_CARD, anchor="w")
        val_label.pack(fill="x", padx=10, pady=(2, 0))

        sub_var = tk.StringVar(value="")
        sub_lbl = tk.Label(card, textvariable=sub_var,
                           font=(FONT_FAMILY, 9),
                           fg=CLR_MUTED, bg=CLR_CARD, anchor="w")
        sub_lbl.pack(fill="x", padx=10, pady=(0, 8))

        cards[key] = (val_var, val_label, sub_var, sub_lbl)

    def set_card(key, value, sub="", sub_color=CLR_MUTED, value_color=CLR_TEXT):
        vv, vl, sv, sl = cards[key]
        vv.set(value)
        vl.config(fg=value_color)
        sv.set(sub)
        sl.config(fg=sub_color)

    for _c, (_k, _cap, _ico) in enumerate((
        ("revenue",  "Revenue",      "💰"),
        ("profit",   "Profit",       "📈"),
        ("sales",    "Transactions", "🧾"),
        ("average",  "Avg Sale",     "📐"),
        ("items",    "Items Sold",   "📦"),
        ("reversed", "Reversals",    "↩️"),
    )):
        make_card(_c, _k, _cap, _ico)

    note_var = tk.StringVar()
    tk.Label(body, textvariable=note_var,
             font=(FONT_FAMILY, 9, "italic"),
             fg=CLR_AMBER, bg=CLR_BG, anchor="w").pack(
        fill="x", padx=16, pady=(0, 2))

    # ── 3. NOTEBOOK (tabs fill the rest of the body) ──────────────────────
    notebook = ttk.Notebook(body)
    notebook.pack(fill="both", expand=True, padx=12, pady=(0, 6))

    # ─────────────────────────────────────────────
    # TAB 1: Sales
    # ─────────────────────────────────────────────
    sales_tab = ttk.Frame(notebook)
    notebook.add(sales_tab, text="  Sales  ")

    # search/filter toolbar inside Sales tab
    stool = tk.Frame(sales_tab, bg=CLR_CARD,
                     highlightbackground=CLR_BORDER, highlightthickness=1)
    stool.pack(fill="x", side="top", padx=4, pady=(4, 2))

    tk.Label(stool, text="Search:", font=FONT_SMALL,
             bg=CLR_CARD, fg=CLR_MUTED).pack(side="left", padx=(8, 2), pady=6)
    sale_search_var = tk.StringVar()
    ttk.Entry(stool, textvariable=sale_search_var, width=24).pack(
        side="left", padx=(0, 4))
    tk.Label(stool, text="(receipt # or product)",
             font=FONT_SMALL, bg=CLR_CARD, fg=CLR_MUTED).pack(side="left")

    tk.Label(stool, text="Status:", font=FONT_SMALL,
             bg=CLR_CARD, fg=CLR_MUTED).pack(side="left", padx=(16, 2))
    status_var   = tk.StringVar(value="All")
    status_combo = ttk.Combobox(stool, textvariable=status_var,
                                state="readonly", width=11,
                                values=("All", "Completed", "Reversed"))
    status_combo.pack(side="left")

    # footer summary (packed bottom before pane so it stays anchored)
    sales_footer_var = tk.StringVar()
    tk.Label(sales_tab, textvariable=sales_footer_var,
             font=FONT_BOLD, bg=CLR_BG, fg=CLR_DARK,
             anchor="w").pack(side="bottom", fill="x", padx=8, pady=(2, 4))

    # sales area: list on top (expands), detail strip at the bottom (fixed)
    # Using a simple pack layout instead of PanedWindow for reliability.
    sales_list_frame = tk.Frame(sales_tab, bg=CLR_BG)
    sales_list_frame.pack(fill="both", expand=True, padx=4, pady=(2, 0))

    sales_tf, sales_table = make_table(
        sales_list_frame,
        ("Sale #", "Date", "Time", "Products",
         "Items", "Total", "Profit", "Status"),
        (65, 95, 60, 300, 55, 100, 95, 100),
        numeric=("Sale #", "Items", "Total", "Profit"),
        left=("Products",),
        stripe=True, sortable=True
    )
    sales_tf.pack(fill="both", expand=True)
    sales_table.tag_configure("voided",    foreground="#9ca3af")
    sales_table.tag_configure("completed", foreground=CLR_TEXT)

    # detail panel — fixed height strip below the list
    detail_outer = tk.Frame(sales_tab, bg=CLR_CARD,
                            highlightbackground=CLR_BORDER,
                            highlightthickness=1,
                            height=160)
    detail_outer.pack(fill="x", padx=4, pady=(2, 4))
    detail_outer.pack_propagate(False)   # keep the fixed height

    detail_hdr = tk.Frame(detail_outer, bg=CLR_BG)
    detail_hdr.pack(fill="x")

    detail_title_var = tk.StringVar()
    tk.Label(detail_hdr, textvariable=detail_title_var,
             font=FONT_BOLD, bg=CLR_BG, fg=CLR_DARK,
             anchor="w").pack(side="left", padx=10, pady=5)

    detail_pay_var = tk.StringVar()
    tk.Label(detail_hdr, textvariable=detail_pay_var,
             font=FONT_SMALL, bg=CLR_BG, fg=CLR_MUTED,
             anchor="e").pack(side="right", padx=10, pady=5)

    detail_tf, detail_table = make_table(
        detail_outer,
        ("Product", "Qty", "Unit Price", "Line Total", "Profit"),
        (280, 70, 110, 120, 110),
        numeric=("Qty", "Unit Price", "Line Total", "Profit"),
        left=("Product",), stripe=True
    )
    detail_tf.pack(fill="both", expand=True, padx=4, pady=(0, 4))

    # ─────────────────────────────────────────────
    # TAB 2: Trend chart
    # ─────────────────────────────────────────────
    trend_tab = ttk.Frame(notebook)
    notebook.add(trend_tab, text="  Trend  ")

    trend_toolbar = tk.Frame(trend_tab, bg=CLR_CARD,
                             highlightbackground=CLR_BORDER,
                             highlightthickness=1)
    trend_toolbar.pack(fill="x", side="top")

    trend_info_var = tk.StringVar(
        value="Hover over a bar to see figures for that period.")
    tk.Label(trend_toolbar, textvariable=trend_info_var,
             font=FONT_BOLD, bg=CLR_CARD, fg=CLR_DARK,
             anchor="w").pack(side="left", padx=10, pady=6)

    leg = tk.Frame(trend_toolbar, bg=CLR_CARD)
    leg.pack(side="right", padx=12)
    tk.Frame(leg, bg=CLR_BLUE,  width=14, height=14).pack(side="left")
    tk.Label(leg, text=" Revenue", font=FONT_SMALL,
             bg=CLR_CARD, fg=CLR_MUTED).pack(side="left", padx=(0, 12))
    tk.Frame(leg, bg=CLR_GREEN, width=14, height=14).pack(side="left")
    tk.Label(leg, text=" Profit", font=FONT_SMALL,
             bg=CLR_CARD, fg=CLR_MUTED).pack(side="left")

    trend_canvas = tk.Canvas(trend_tab, bg=CLR_CARD, highlightthickness=0)
    trend_canvas.pack(fill="both", expand=True, padx=6, pady=6)

    # ─────────────────────────────────────────────
    # TAB 3: Profit by Period
    # ─────────────────────────────────────────────
    profit_tab = ttk.Frame(notebook)
    notebook.add(profit_tab, text="  Profit by Period  ")

    tk.Label(profit_tab,
             text="Double-click any row to see the full product list for that period.",
             font=FONT_SMALL, fg=CLR_MUTED, bg=CLR_BG, anchor="w").pack(
        fill="x", padx=8, pady=(4, 0))

    profit_frame, profit_table = make_table(
        profit_tab,
        ("Period", "Sales", "Revenue", "Cost", "Profit", "Margin",
         "Top Products"),
        (115, 60, 105, 105, 105, 72, 320),
        numeric=("Sales", "Revenue", "Cost", "Profit", "Margin"),
        left=("Period", "Top Products"),
        stripe=True, sortable=True
    )
    profit_frame.pack(fill="both", expand=True, padx=4, pady=4)
    profit_table.tag_configure("loss", foreground="#b91c1c")

    # ─────────────────────────────────────────────
    # TAB 4: Top Products
    # ─────────────────────────────────────────────
    top_tab = ttk.Frame(notebook)
    notebook.add(top_tab, text="  Top Products  ")

    tk.Label(top_tab,
             text="Top 20 products for the selected period — ranked by units sold.",
             font=FONT_SMALL, fg=CLR_MUTED, bg=CLR_BG, anchor="w").pack(
        fill="x", padx=8, pady=(4, 0))

    top_frame_, top_table = make_table(
        top_tab,
        ("Rank", "Product", "Units Sold", "Revenue", "Revenue Share", "Profit"),
        (55, 280, 100, 120, 110, 120),
        numeric=("Rank", "Units Sold", "Revenue", "Revenue Share", "Profit"),
        left=("Product",), stripe=True, sortable=True
    )
    top_frame_.pack(fill="both", expand=True, padx=4, pady=4)

    # ─────────────────────────────────────────────
    # TAB 5: Low Stock
    # ─────────────────────────────────────────────
    low_tab = ttk.Frame(notebook)
    notebook.add(low_tab, text="  Low Stock ⚠  ")

    tk.Label(low_tab,
             text="Products that need restocking.  Double-click a row to go straight to Restock.",
             font=FONT_SMALL, fg=CLR_MUTED, bg=CLR_BG, anchor="w").pack(
        fill="x", padx=8, pady=(4, 0))

    low_frame, low_table = make_table(
        low_tab,
        ("ID", "Product", "Qty Left", "Status"),
        (55, 400, 90, 130),
        numeric=("ID", "Qty Left"),
        left=("Product",),
        sortable=True
    )
    low_frame.pack(fill="both", expand=True, padx=4, pady=4)
    low_table.tag_configure("low", background="#fff4cc")
    low_table.tag_configure("out", background="#fde2e2")

    # ── 4. SHARED STATE ───────────────────────────────────────────────────
    state = {
        "preset": None, "signature": None,
        "last_sale_id": None, "after_id": None, "sale_id": None
    }
    period_products = {}
    top_ids         = {}
    sales_rows      = []
    trend_points    = []

    # ── Period-products popup ─────────────────────────────────────────────
    def show_period_products(event=None):
        sel = profit_table.selection()
        if not sel:
            return
        lines  = period_products.get(sel[0], [])
        period = profit_table.item(sel[0], "values")[0]
        win = tk.Toplevel(window)
        win.title(f"Products sold — {period}")
        win.geometry("440x420")
        win.resizable(False, True)
        win.transient(window.winfo_toplevel())
        hdr2 = tk.Frame(win, bg=CLR_DARK, pady=8)
        hdr2.pack(fill="x")
        tk.Label(hdr2, text=f"Products sold  —  {period}",
                 font=FONT_BOLD, bg=CLR_DARK, fg="white").pack(padx=12)
        txt = tk.Text(win, font=FONT_NORMAL, relief="flat",
                      bg=CLR_CARD, padx=12, pady=8, wrap="word")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        sb2 = ttk.Scrollbar(win, command=txt.yview)
        txt.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        if lines:
            for i, line in enumerate(lines, 1):
                txt.insert("end", f"{i:>3}.  {line}\n")
        else:
            txt.insert("end", "No products recorded for this period.")
        txt.config(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)

    profit_table.bind("<Double-1>", show_period_products)

    # ── Sale detail ───────────────────────────────────────────────────────
    def clear_details():
        state["sale_id"] = None
        clear_table(detail_table)
        detail_title_var.set(
            "Select a sale in the list above to see its items here.")
        detail_pay_var.set("")

    def show_sale_details(sale_id):
        cursor.execute("""
            SELECT sale_date, total_amount, amount_received, change_given,
                   status, voided_at, void_reason
            FROM sales WHERE sale_id = ?
        """, (sale_id,))
        sale = cursor.fetchone()
        if sale is None:
            clear_details()
            return
        date, total, received, change, status, voided_at, reason = sale
        clear_table(detail_table)
        cursor.execute("""
            SELECT product_name, quantity, unit_price, line_total, unit_cost
            FROM sale_items WHERE sale_id = ?
            ORDER BY sale_item_id
        """, (sale_id,))
        for name, qty, price, line_total, unit_cost in cursor.fetchall():
            lp = None if unit_cost is None else line_total - qty * unit_cost
            detail_table.insert(
                "", "end",
                values=(name, qty, fmt_money(price),
                        fmt_money(line_total), money_or_dash(lp))
            )
        detail_table.refresh_view()
        is_void = status == "voided"
        badge   = "  ⚠ REVERSED" if is_void else "  ✓ Completed"
        detail_title_var.set(
            f"Sale #{sale_id}  •  {date[:10]}  {date[11:16]}{badge}"
        )
        pay_text = (
            f"Total: {fmt_money(total)}     "
            f"Received: {fmt_money(received)}     "
            f"Change: {fmt_money(change)}"
        )
        if is_void:
            pay_text += f"     |  Reversed {voided_at}: {reason or 'no reason'}"
        detail_pay_var.set(pay_text)

    def on_sale_select(event=None):
        sel = sales_table.selection()
        if not sel:
            return
        sid = int(sales_table.item(sel[0], "values")[0])
        state["sale_id"] = sid
        show_sale_details(sid)

    sales_table.bind("<<TreeviewSelect>>", on_sale_select)
    clear_details()

    # ── Sales list renderer ───────────────────────────────────────────────
    def render_sales(*_):
        term   = sale_search_var.get().strip().lower()
        wanted = status_var.get()
        shown  = [
            r for r in sales_rows
            if (wanted != "Completed" or r["status"] == "completed")
            and (wanted != "Reversed"  or r["status"] == "voided")
            and (not term or term == str(r["id"]) or term in r["names"])
        ]

        clear_table(sales_table)
        revenue = profit = 0.0
        counted = 0
        keep    = None

        for row in shown:
            is_void = row["status"] == "voided"
            item = sales_table.insert(
                "", "end",
                values=(
                    row["id"], row["date"], row["time"], row["summary"],
                    row["items"], fmt_money(row["total"]),
                    "—" if is_void else money_or_dash(row["profit"]),
                    "↩ Reversed" if is_void else "✓ Completed",
                ),
                tags=("voided",) if is_void else ("completed",)
            )
            if row["id"] == state["sale_id"]:
                keep = item
            if not is_void:
                counted += 1
                revenue += row["total"]
                profit  += row["profit"] or 0.0

        sales_table.refresh_view()
        sales_footer_var.set(
            f"Showing {len(shown)} sale(s)   •   "
            f"{counted} completed   •   "
            f"Revenue: {fmt_money(revenue)}   •   "
            f"Profit: {fmt_money(profit)}"
        )
        if keep is not None:
            sales_table.selection_set(keep)
            sales_table.see(keep)
        elif state["sale_id"] is not None:
            clear_details()

    sale_search_var.trace_add("write", render_sales)
    status_combo.bind("<<ComboboxSelected>>", render_sales)

    # ── Trend chart ───────────────────────────────────────────────────────
    chart_state = {"left": 72, "slot": 1, "count": 0}

    def draw_trend(event=None):
        c = trend_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 150 or h < 120:
            return
        if not trend_points:
            c.create_text(w // 2, h // 2, fill=CLR_MUTED,
                          font=(FONT_FAMILY, 12),
                          text="No sales in the selected date range.")
            chart_state["count"] = 0
            return
        L, R, T, B = 72, 24, 36, 46
        pw, ph = w - L - R, h - T - B
        peak   = nice_ceiling(max(p["revenue"] for p in trend_points))
        base   = T + ph
        for step in range(6):
            y   = base - ph * step / 5
            val = peak * step / 5
            lbl = (f"{val/1000:.1f}k" if peak >= 1000
                   else f"{val:,.0f}" if peak >= 10 else f"{val:.2f}")
            c.create_line(L, y, w - R, y, fill="#e5e7eb", dash=(4, 4))
            c.create_text(L - 6, y, anchor="e", fill=CLR_MUTED,
                          font=(FONT_FAMILY, 8), text=lbl)
        c.create_line(L, T,    L,      base, fill=CLR_BORDER)
        c.create_line(L, base, w - R,  base, fill=CLR_BORDER)
        cnt   = len(trend_points)
        slot  = pw / cnt
        bar_w = max(3.0, slot * 0.65)
        every = max(1, math.ceil(cnt / max(1, pw // 60)))
        for idx, pt in enumerate(trend_points):
            x0    = L + slot * idx + (slot - bar_w) / 2
            rev_h = ph * pt["revenue"] / peak if peak > 0 else 0
            c.create_rectangle(x0, base - rev_h, x0 + bar_w, base,
                               fill=CLR_BLUE, outline="")
            if pt["profit"] is not None and pt["profit"] > 0:
                pro_h = ph * pt["profit"] / peak
                xi    = x0 + bar_w * 0.24
                c.create_rectangle(xi, base - pro_h,
                                   xi + bar_w * 0.52, base,
                                   fill=CLR_GREEN, outline="")
            if rev_h > 22 and idx % every == 0:
                lbl2 = (f"{pt['revenue']/1000:.1f}k"
                        if pt["revenue"] >= 1000
                        else f"{pt['revenue']:,.0f}")
                c.create_text(x0 + bar_w / 2, base - rev_h - 8,
                              fill=CLR_DARK, font=(FONT_FAMILY, 8, "bold"),
                              text=lbl2)
            if idx % every == 0:
                pl = pt["label"]
                c.create_text(x0 + bar_w / 2, base + 14,
                              fill=CLR_MUTED, font=(FONT_FAMILY, 8),
                              text=pl[5:] if len(pl) == 10 else pl)
        chart_state.update(left=L, slot=slot, count=cnt)

    def trend_hover(event):
        if not chart_state["count"]:
            return
        idx = int((event.x - chart_state["left"]) //
                  max(chart_state["slot"], 1))
        if 0 <= idx < chart_state["count"]:
            pt = trend_points[idx]
            trend_info_var.set(
                f"{pt['label']}  —  "
                f"Sales: {pt['sales']}   "
                f"Revenue: {fmt_money(pt['revenue'])}   "
                f"Profit: {money_or_dash(pt['profit'])}"
            )

    trend_canvas.bind("<Configure>", draw_trend)
    trend_canvas.bind("<Motion>",    trend_hover)

    # ── Data helpers ──────────────────────────────────────────────────────
    def sales_signature():
        cursor.execute("""
            SELECT COALESCE(MAX(sale_id),0), COUNT(*),
                   COALESCE(SUM(status='voided'),0),
                   COALESCE(SUM(total_amount),0)
            FROM sales
        """)
        sp = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM products")
        return sp + cursor.fetchone()

    def day_span(start, end):
        return (start.strftime(DATE_FORMAT) + " 00:00:00",
                end.strftime(DATE_FORMAT)   + " 23:59:59")

    def totals_for(start, end):
        cursor.execute(f"""
            SELECT COUNT(DISTINCT s.sale_id),
                   COALESCE(SUM(si.quantity),0),
                   COALESCE(SUM(si.line_total),0),
                   COALESCE(SUM({COSTED_COST}),0),
                   COALESCE(SUM({COSTED_REVENUE}),0),
                   COUNT(CASE WHEN si.unit_cost IS NULL THEN 1 END)
            FROM sales s
            JOIN sale_items si ON si.sale_id = s.sale_id
            WHERE s.sale_date BETWEEN ? AND ?
              AND s.status = 'completed'
        """, day_span(start, end))
        cnt, items, rev, cost, crev, uncosted = cursor.fetchone()
        rev = money(rev)
        return {
            "count": cnt, "items": items, "revenue": rev,
            "cost": money(cost), "costed_revenue": crev,
            "profit": money(crev - cost),
            "average": money(rev / cnt) if cnt else 0.0,
            "uncosted": uncosted,
        }

    # ── Main load ─────────────────────────────────────────────────────────
    def load(*_, silent=False):
        if state["preset"]:
            ps, pe = state["preset"]()
            for ent, val in ((from_entry, ps), (to_entry, pe)):
                ent.delete(0, "end")
                ent.insert(0, val.strftime(DATE_FORMAT))

        sig = sales_signature()
        state["signature"] = sig

        try:
            start = datetime.strptime(from_entry.get().strip(), DATE_FORMAT)
            end   = datetime.strptime(to_entry.get().strip(),   DATE_FORMAT)
            if start > end:
                raise ValueError("'From' date is after the 'To' date.")
        except ValueError as err:
            if not silent:
                msg = str(err)
                if "does not match" in msg or "unconverted" in msg:
                    msg = "Dates must be in YYYY-MM-DD format, e.g. 2026-11-09."
                messagebox.showerror("Invalid Date", msg, parent=window)
            return

        span = day_span(start, end)
        now  = totals_for(start, end)
        days = (end - start).days + 1
        b_end, b_st = (start - timedelta(days=1),
                       start - timedelta(days=days))
        prev = totals_for(b_st, b_end)
        vs   = "yesterday" if days == 1 else f"prev {days} days"

        set_card("revenue", fmt_money(now["revenue"]),
                 *change_text(now["revenue"], prev["revenue"], vs))

        margin = fmt_percent(now["profit"], now["costed_revenue"])
        pch, pcol = change_text(now["profit"], prev["profit"], vs)
        set_card("profit", fmt_money(now["profit"]),
                 f"Margin {margin}   {pch}", pcol,
                 value_color="#b91c1c" if now["profit"] < 0 else CLR_GREEN)

        set_card("sales",   str(now["count"]),
                 *change_text(now["count"], prev["count"], vs))
        set_card("average", fmt_money(now["average"]),
                 *change_text(now["average"], prev["average"], vs))
        set_card("items",   str(now["items"]),
                 *change_text(now["items"], prev["items"], vs))

        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(total_amount),0)
            FROM sales WHERE sale_date BETWEEN ? AND ? AND status='voided'
        """, span)
        vc, va = cursor.fetchone()
        set_card("reversed", str(vc),
                 f"worth {fmt_money(va)} — not counted" if vc else "none",
                 value_color=CLR_MUTED if not vc else "#b45309")

        notes = []
        if now["uncosted"]:
            notes.append(
                f"⚠  {now['uncosted']} line(s) have no cost and are "
                "excluded from profit figures"
            )
        note_var.set("  ".join(notes))

        # Profit by period
        psql, plabel = PERIODS[group_var.get()]
        profit_table.set_heading("Period", plabel)
        clear_table(profit_table)

        cursor.execute(f"""
            SELECT period, product_name, quantity FROM (
                SELECT {psql} AS period,
                       MAX(si.product_name) AS product_name,
                       SUM(si.quantity)     AS quantity
                FROM sales s
                JOIN sale_items si ON si.sale_id = s.sale_id
                WHERE s.sale_date BETWEEN ? AND ?
                  AND s.status = 'completed'
                GROUP BY period, si.product_id
            ) ORDER BY period, quantity DESC, product_name
        """, span)
        pbp = {}
        for period, pname, qty in cursor.fetchall():
            pbp.setdefault(period, []).append(f"{pname} ({qty})")

        period_products.clear()
        trend_points.clear()

        cursor.execute(f"""
            SELECT {psql} AS period,
                   COUNT(DISTINCT s.sale_id),
                   SUM(si.line_total),
                   SUM({COSTED_COST}),
                   SUM({COSTED_REVENUE})
            FROM sales s
            JOIN sale_items si ON si.sale_id = s.sale_id
            WHERE s.sale_date BETWEEN ? AND ?
              AND s.status = 'completed'
            GROUP BY period ORDER BY period DESC
        """, span)

        for period, scnt, rev, pcost, pcrev in cursor.fetchall():
            pp   = None if pcrev is None else pcrev - pcost
            top3 = ", ".join(pbp.get(period, [])[:3])
            if len(pbp.get(period, [])) > 3:
                top3 += f"  +{len(pbp[period])-3} more"
            rid = profit_table.insert(
                "", "end",
                values=(period, scnt, fmt_money(rev),
                        money_or_dash(pcost), money_or_dash(pp),
                        fmt_percent(pp, pcrev), top3),
                tags=(("loss",) if pp is not None and pp < 0 else ())
            )
            period_products[rid] = pbp.get(period, [])
            trend_points.append({"label": period, "revenue": rev,
                                  "profit": pp, "sales": scnt})

        profit_table.refresh_view()
        trend_points.reverse()
        del trend_points[:-60]
        draw_trend()

        # Sales list
        cursor.execute("""
            SELECT s.sale_id, si.product_name, si.quantity
            FROM sales s
            JOIN sale_items si ON si.sale_id = s.sale_id
            WHERE s.sale_date BETWEEN ? AND ?
            ORDER BY s.sale_id, si.sale_item_id
        """, span)
        lbs = {}
        for sid, pname, qty in cursor.fetchall():
            lbs.setdefault(sid, []).append((pname, qty))

        cursor.execute(f"""
            SELECT s.sale_id, s.sale_date,
                   COALESCE(SUM(si.quantity),0),
                   s.total_amount,
                   SUM({COSTED_REVENUE}) - SUM({COSTED_COST}),
                   s.status
            FROM sales s
            LEFT JOIN sale_items si ON si.sale_id = s.sale_id
            WHERE s.sale_date BETWEEN ? AND ?
            GROUP BY s.sale_id ORDER BY s.sale_id DESC
        """, span)

        sales_rows.clear()
        for sid, date, items, total, sprofit, status in cursor.fetchall():
            lines   = lbs.get(sid, [])
            summary = ",  ".join(f"{q} × {n}" for n, q in lines[:2])
            if len(lines) > 2:
                summary += f"   +{len(lines)-2} more"
            sales_rows.append({
                "id": sid, "date": date[:10], "time": date[11:16],
                "summary": summary,
                "names": " ".join(n.lower() for n, _ in lines),
                "items": items, "total": total,
                "profit": sprofit, "status": status,
            })
        render_sales()

        # Top products
        clear_table(top_table)
        top_ids.clear()
        cursor.execute(f"""
            SELECT MAX(si.product_name), SUM(si.quantity), SUM(si.line_total),
                   SUM({COSTED_REVENUE}) - SUM({COSTED_COST}), si.product_id
            FROM sale_items si
            JOIN sales s ON s.sale_id = si.sale_id
            WHERE s.sale_date BETWEEN ? AND ? AND s.status = 'completed'
            GROUP BY si.product_id ORDER BY 2 DESC, 3 DESC LIMIT 20
        """, span)
        for rank, (name, units, amount, ppro, pid) in enumerate(
            cursor.fetchall(), 1
        ):
            rid = top_table.insert(
                "", "end",
                values=(rank, name, units, fmt_money(amount),
                        fmt_percent(amount, now["revenue"]),
                        money_or_dash(ppro))
            )
            top_ids[rid] = pid
        top_table.refresh_view()

        # Low stock
        clear_table(low_table)
        cursor.execute("""
            SELECT product_id, product_name, quantity FROM products
            WHERE quantity <= ?
            ORDER BY quantity, product_name COLLATE NOCASE
        """, (LOW_STOCK_LEVEL,))
        for pid, name, qty in cursor.fetchall():
            out = qty == 0
            low_table.insert(
                "", "end",
                values=(pid, name, qty,
                        "🔴  Out of stock" if out else "🟡  Low stock"),
                tags=("out",) if out else ("low",)
            )
        low_table.refresh_view()

        # Low-stock tab badge
        cursor.execute(
            "SELECT COUNT(*) FROM products WHERE quantity <= ?",
            (LOW_STOCK_LEVEL,))
        an = cursor.fetchone()[0]
        notebook.tab(4, text=(
            f"  Low Stock ⚠ ({an})  " if an else "  Low Stock  "))

        # Live status
        newest = sig[0]
        msg    = f"● LIVE  {datetime.now().strftime('%H:%M:%S')}"
        if (state["last_sale_id"] is not None
                and newest > state["last_sale_id"]):
            msg += f"  |  new sale #{newest}"
        state["last_sale_id"] = newest
        live_var.set(msg)

    # ── Preset helpers ────────────────────────────────────────────────────
    def use_preset(preset, group):
        state["preset"] = preset
        group_var.set(group)
        load()

    def apply_custom_dates():
        state["preset"] = None
        load()

    def preset_today():
        d = datetime.now().date(); return d, d

    def preset_yesterday():
        d = datetime.now().date() - timedelta(days=1); return d, d

    def preset_week():
        d = datetime.now().date(); return d - timedelta(days=6), d

    def preset_30():
        d = datetime.now().date(); return d - timedelta(days=29), d

    def preset_month():
        d = datetime.now().date(); return d.replace(day=1), d

    def preset_year():
        d = datetime.now().date(); return d.replace(month=1, day=1), d

    # Build preset buttons — the active one turns blue
    _active_btn = {"b": None}
    def _pbtn(label, preset, group):
        def cmd():
            if _active_btn["b"]:
                _active_btn["b"].config(bg="#e8eaed", fg=CLR_DARK,
                                        font=FONT_NORMAL)
            btn.config(bg=CLR_BLUE, fg="white", font=FONT_BOLD)
            _active_btn["b"] = btn
            use_preset(preset, group)
        btn = tk.Button(preset_row, text=label, command=cmd,
                        relief="flat", bd=0, padx=10, pady=6,
                        bg="#e8eaed", fg=CLR_DARK,
                        activebackground=CLR_BLUE, activeforeground="white",
                        font=FONT_NORMAL, cursor="hand2")
        btn.pack(side="left", padx=1)
        return btn

    _pbtn("Today",        preset_today,     "Daily")
    _pbtn("Yesterday",    preset_yesterday, "Daily")
    _pbtn("Last 7 Days",  preset_week,      "Daily")
    _pbtn("Last 30 Days", preset_30,        "Daily")
    _pbtn("This Month",   preset_month,     "Weekly")
    _pbtn("This Year",    preset_year,      "Monthly")

    tk.Button(preset_row, text="🔄 Refresh", command=load,
              relief="flat", bd=0, padx=10, pady=6,
              bg="#e8eaed", fg=CLR_DARK,
              activebackground="#d1d5db",
              font=FONT_NORMAL, cursor="hand2").pack(side="left", padx=1)

    group_combo.bind("<<ComboboxSelected>>", load)

    # ── Action bar contents ───────────────────────────────────────────────
    def _abtn(text, style, cmd):
        ttk.Button(act_bar, text=text, style=style,
                   command=cmd).pack(side="left", padx=6, pady=6)

    def selected_sale_id():
        sel = sales_table.selection()
        if not sel:
            messagebox.showinfo(
                "Select a Sale",
                "Open the Sales tab and click on a sale row first.",
                parent=window)
            return None
        return int(sales_table.item(sel[0], "values")[0])

    def view_receipt(event=None):
        sid = selected_sale_id()
        if sid is not None:
            show_saved_receipt(sid, window)

    def reverse_selected():
        sid = selected_sale_id()
        if sid is not None:
            void_sale(sid, window)

    def delete_selected():
        sid = selected_sale_id()
        if sid is not None:
            delete_sale_record(sid, window)

    def delete_range():
        try:
            start = datetime.strptime(from_entry.get().strip(), DATE_FORMAT)
            end   = datetime.strptime(to_entry.get().strip(),   DATE_FORMAT)
            if start > end:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Date",
                                 "Set a valid From / To date first.",
                                 parent=window)
            return
        delete_sales_in_range(
            start.strftime(DATE_FORMAT), end.strftime(DATE_FORMAT), window)

    def export_current_tab():
        tab = notebook.tab(notebook.select(), "text").strip()
        tmap = {"Sales": sales_table, "Profit by Period": profit_table,
                "Top Products": top_table, "Low Stock": low_table}
        tbl = next((v for k, v in tmap.items()
                    if k.lower() in tab.lower()), None)
        if tbl is None:
            messagebox.showinfo(
                "Export",
                "Open Sales, Profit by Period, Top Products or Low Stock "
                "tab first.",
                parent=window)
            return
        cols = [tbl.heading(c, "text").rstrip(" ▲▼")
                for c in tbl["columns"]]
        name = tab.lower().replace(" ", "_").replace("⚠", "").strip("_")
        export_table_csv(
            tbl, cols,
            f"{name}_{from_entry.get().strip()}"
            f"_to_{to_entry.get().strip()}.csv",
            window)

    _abtn("🖨  View / Print Receipt", "Accent.TButton", view_receipt)
    _abtn("↩  Reverse Selected",      "Danger.TButton", reverse_selected)
    _abtn("📄  Export Tab → CSV",     "TButton",        export_current_tab)

    tk.Frame(act_bar, bg=CLR_BORDER, width=1).pack(
        side="left", fill="y", padx=6, pady=6)

    _abtn("🗑  Delete Selected",         "Danger.TButton", delete_selected)
    _abtn("🗑  Delete All in Date Range…","Danger.TButton", delete_range)

    # jump shortcuts
    def product_from_tab():
        tab = notebook.tab(notebook.select(), "text").strip()
        if "low" in tab.lower():
            sel = low_table.selection()
            if sel:
                return int(low_table.item(sel[0], "values")[0])
        elif "top" in tab.lower():
            sel = top_table.selection()
            if sel:
                return top_ids.get(sel[0])
        messagebox.showinfo(
            "Select a Product",
            "Open the Top Products or Low Stock tab and select a row first.",
            parent=window)
        return None

    def open_in_inventory(event=None):
        pid = product_from_tab()
        if pid is not None:
            go_to("inventory", product_id=pid)

    def restock_from_report(event=None):
        pid = product_from_tab()
        if pid is not None:
            go_to("inventory", product_id=pid, action="restock")

    def sell_from_report():
        pid = product_from_tab()
        if pid is not None:
            go_to("pos", product_id=pid)

    tk.Frame(act_bar, bg=CLR_BORDER, width=1).pack(
        side="left", fill="y", padx=6, pady=6)
    tk.Label(act_bar, text="Selected product →",
             font=FONT_SMALL, fg=CLR_MUTED,
             bg=CLR_BG).pack(side="left", padx=(4, 2))
    _abtn("📦 Restock",       "TButton",          restock_from_report)
    _abtn("🔍 In Inventory",  "TButton",          open_in_inventory)
    _abtn("💳 Sell in POS",   "Success.TButton",  sell_from_report)

    sales_table.bind("<Double-1>", view_receipt)
    low_table.bind("<Double-1>",   restock_from_report)
    top_table.bind("<Double-1>",   open_in_inventory)

    # ── Live polling ──────────────────────────────────────────────────────
    register_listener(window, lambda: load(silent=True))

    def poll():
        try:
            changed = sales_signature() != state["signature"]
            rolled  = False
            if state["preset"]:
                exp   = tuple(d.strftime(DATE_FORMAT)
                              for d in state["preset"]())
                shown = (from_entry.get().strip(), to_entry.get().strip())
                rolled = exp != shown
            if (changed or rolled) and window.winfo_ismapped():
                load(silent=True)
        except tk.TclError:
            return
        except Exception:
            traceback.print_exc()
        try:
            state["after_id"] = window.after(REPORT_REFRESH_MS, poll)
        except tk.TclError:
            pass

    def stop_poll(event):
        if event.widget is window and state["after_id"]:
            try:
                window.after_cancel(state["after_id"])
            except tk.TclError:
                pass

    window.bind("<Destroy>", stop_poll, add="+")
    register_module("reports", window, on_show=lambda: load())
    use_preset(preset_today, "Daily")
    state["after_id"] = window.after(REPORT_REFRESH_MS, poll)

def build_home():
    """Live 'today at a glance' dashboard on the Home page."""

    top = tk.Frame(home_page, bg=CLR_BG)
    top.pack(fill="x", padx=24, pady=(16, 4))

    tk.Label(top, text=STORE_NAME, font=FONT_GIANT,
             bg=CLR_BG, fg=CLR_DARK).pack(side="left")
    clock_var = tk.StringVar()
    tk.Label(top, textvariable=clock_var, font=FONT_NORMAL,
             bg=CLR_BG, fg=CLR_MUTED).pack(side="right")

    greet_var = tk.StringVar()
    tk.Label(home_page, textvariable=greet_var, font=FONT_NORMAL,
             bg=CLR_BG, fg=CLR_TEXT, anchor="w").pack(fill="x", padx=26)

    ttk.Separator(home_page, orient="horizontal").pack(
        fill="x", padx=20, pady=8)

    # ----- Quick action tiles -----
    actions = tk.Frame(home_page, bg=CLR_BG)
    actions.pack(fill="x", padx=20, pady=4)

    def action(column, text, sub, colour, command):
        box = tk.Button(
            actions, text=f"{text}\n{sub}", command=command,
            bg=colour, fg="white",
            activebackground=colour, activeforeground="white",
            relief="flat", bd=0, font=(FONT_FAMILY, 12, "bold"),
            pady=16, cursor="hand2"
        )
        box.grid(row=0, column=column, sticky="nsew", padx=5)
        actions.columnconfigure(column, weight=1, uniform="act")

    action(0, "🛒  NEW SALE",    "Point of Sale  (F2)",   "#2563eb",
           lambda: go_to("pos"))
    action(1, "📦  INVENTORY",   "Products & stock  (F3)", "#0f766e",
           lambda: go_to("inventory"))
    action(2, "📊  REPORTS",     "Sales & profit  (F4)",   "#7c3aed",
           lambda: go_to("reports"))
    action(3, "➕  ADD PRODUCT", "New item in stock",       "#b45309",
           lambda: open_product_form(home_page))

    # ----- KPI cards -----
    kpis = tk.Frame(home_page, bg=CLR_BG)
    kpis.pack(fill="x", padx=20, pady=8)
    h_cards = {}

    def card(column, key, caption):
        frame = tk.Frame(kpis, bg=CLR_CARD,
                         highlightbackground=CLR_BORDER, highlightthickness=1)
        frame.grid(row=0, column=column, sticky="nsew", padx=5)
        kpis.columnconfigure(column, weight=1, uniform="hk")
        tk.Label(frame, text=caption.upper(), font=(FONT_FAMILY, 8, "bold"),
                 fg=CLR_MUTED, bg=CLR_CARD, anchor="w").pack(
            fill="x", padx=12, pady=(8, 0))
        value = tk.StringVar(value="—")
        value_label = tk.Label(frame, textvariable=value,
                               font=FONT_KPI, fg=CLR_TEXT, bg=CLR_CARD,
                               anchor="w")
        value_label.pack(fill="x", padx=12)
        sub   = tk.StringVar()
        sub_label = tk.Label(frame, textvariable=sub, font=FONT_SMALL,
                             fg=CLR_MUTED, bg=CLR_CARD, anchor="w")
        sub_label.pack(fill="x", padx=12, pady=(0, 8))
        h_cards[key] = (value, value_label, sub, sub_label)

    def set_card(key, value, sub="", sub_colour=CLR_MUTED, colour=CLR_TEXT):
        value_var, value_label, sub_var, sub_label = h_cards[key]
        value_var.set(value)
        value_label.config(fg=colour)
        sub_var.set(sub)
        sub_label.config(fg=sub_colour)

    for column, (key, caption) in enumerate((
        ("revenue", "Today's sales"),
        ("profit",  "Today's profit"),
        ("count",   "Transactions"),
        ("week",    "Last 7 days"),
        ("stock",   "Stock value (cost)"),
        ("alerts",  "Stock alerts"),
    )):
        card(column, key, caption)

    # ----- Lower area: chart + two lists -----
    lower = tk.Frame(home_page, bg=CLR_BG)
    lower.pack(fill="both", expand=True, padx=20, pady=6)
    lower.columnconfigure(0, weight=3, uniform="low")
    lower.columnconfigure(1, weight=2, uniform="low")
    lower.rowconfigure(0, weight=1)

    chart_box = tk.Frame(lower, bg=CLR_CARD,
                         highlightbackground=CLR_BORDER, highlightthickness=1)
    chart_box.grid(row=0, column=0, sticky="nsew", padx=5)
    tk.Label(chart_box, text="SALES – LAST 7 DAYS", font=(FONT_FAMILY, 9, "bold"),
             fg=CLR_MUTED, bg=CLR_CARD, anchor="w").pack(
        fill="x", padx=12, pady=(8, 0))
    canvas = tk.Canvas(chart_box, bg=CLR_CARD,
                       highlightthickness=0, height=170)
    canvas.pack(fill="both", expand=True, padx=6, pady=6)

    right = tk.Frame(lower, bg=CLR_BG)
    right.grid(row=0, column=1, sticky="nsew", padx=5)
    right.rowconfigure(0, weight=1, uniform="r")
    right.rowconfigure(1, weight=1, uniform="r")
    right.columnconfigure(0, weight=1)

    def list_box(row, title):
        box = tk.Frame(right, bg=CLR_CARD,
                       highlightbackground=CLR_BORDER, highlightthickness=1)
        box.grid(row=row, column=0, sticky="nsew",
                 pady=(0 if row == 0 else 6, 0))
        tk.Label(box, text=title, font=(FONT_FAMILY, 9, "bold"),
                 fg=CLR_MUTED, bg=CLR_CARD, anchor="w").pack(
            fill="x", padx=12, pady=(8, 2))
        return box

    recent_box = list_box(0, "RECENT SALES  (double-click for receipt)")
    recent_frame, recent_table = make_table(
        recent_box, ("#", "Time", "Items", "Total"), (45, 90, 55, 90),
        numeric=("#", "Items", "Total"), stripe=True
    )
    recent_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    recent_table.tag_configure("voided", foreground="#9ca3af")

    alert_box = list_box(1, "NEEDS RESTOCKING  (double-click to restock)")
    alert_frame, alert_table = make_table(
        alert_box, ("ID", "Product", "Stock"), (45, 180, 60),
        numeric=("ID", "Stock"), left=("Product",)
    )
    alert_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    alert_table.tag_configure("low", background="#fff4cc")
    alert_table.tag_configure("out", background="#fde2e2")

    def open_recent(event=None):
        selection = recent_table.selection()
        if selection:
            show_saved_receipt(
                int(recent_table.item(selection[0], "values")[0]), root
            )

    def restock_alert(event=None):
        selection = alert_table.selection()
        if selection:
            go_to("inventory",
                  product_id=int(alert_table.item(selection[0], "values")[0]),
                  action="restock")

    recent_table.bind("<Double-1>", open_recent)
    alert_table.bind("<Double-1>",  restock_alert)

    # ----- 7-day mini chart -----
    week = []

    def draw_week(event=None):
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 120 or height < 80 or not week:
            return
        left, right_pad, top_pad, bottom_pad = 50, 12, 14, 24
        plot_w, plot_h = (width - left - right_pad,
                          height - top_pad - bottom_pad)
        peak     = nice_ceiling(max(value for _, value in week) or 1)
        baseline = top_pad + plot_h

        for step in range(5):
            y = baseline - plot_h * step / 4
            canvas.create_line(left, y, width - right_pad, y, fill="#e5e7eb")
            canvas.create_text(left - 6, y, anchor="e", fill=CLR_MUTED,
                               font=(FONT_FAMILY, 8),
                               text=f"{peak * step / 4:,.0f}")

        slot = plot_w / len(week)
        bar  = slot * 0.55

        for index, (label, value) in enumerate(week):
            x0      = left + slot * index + (slot - bar) / 2
            top_y   = baseline - plot_h * value / peak
            is_today = index == len(week) - 1
            canvas.create_rectangle(
                x0, top_y, x0 + bar, baseline,
                fill=CLR_BLUE if is_today else "#93c5fd", outline=""
            )
            if value:
                canvas.create_text(x0 + bar / 2, top_y - 8, fill="#374151",
                                   font=(FONT_FAMILY, 8, "bold"),
                                   text=f"{value:,.0f}")
            canvas.create_text(x0 + bar / 2, baseline + 12, fill=CLR_MUTED,
                               font=(FONT_FAMILY, 8), text=label)

    canvas.bind("<Configure>", draw_week)

    def totals(start, end):
        cursor.execute(f"""
            SELECT COUNT(DISTINCT s.sale_id),
                   COALESCE(SUM(si.line_total), 0),
                   COALESCE(SUM({COSTED_REVENUE}), 0) -
                   COALESCE(SUM({COSTED_COST}), 0)
            FROM sales s
            JOIN sale_items si ON si.sale_id = s.sale_id
            WHERE s.sale_date BETWEEN ? AND ? AND s.status = 'completed'
        """, (start + " 00:00:00", end + " 23:59:59"))
        return cursor.fetchone()

    def refresh(*_):
        now       = datetime.now()
        today     = now.strftime(DATE_FORMAT)
        yesterday = (now - timedelta(days=1)).strftime(DATE_FORMAT)
        week_start = (now - timedelta(days=6)).strftime(DATE_FORMAT)

        hour = now.hour
        part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
        greet_var.set(f"Good {part}. Here is how the shop is doing today.")
        clock_var.set(now.strftime("%A, %d %B %Y"))

        count, revenue, profit = totals(today, today)
        _, y_revenue, y_profit = totals(yesterday, yesterday)
        week_count, week_revenue, _ = totals(week_start, today)

        set_card("revenue", fmt_money(revenue),
                 *change_text(revenue, y_revenue, "yesterday"))
        set_card("profit", fmt_money(profit),
                 *change_text(profit, y_profit, "yesterday"),
                 colour="#b91c1c" if profit < 0 else CLR_GREEN)
        set_card("count", str(count),
                 f"avg {fmt_money(revenue / count if count else 0)} per sale")
        set_card("week", fmt_money(week_revenue),
                 f"{week_count} sales")

        cursor.execute("""
            SELECT COALESCE(SUM(quantity * ordering_price /
                                MAX(units_per_pack, 1)), 0),
                   COUNT(CASE WHEN quantity = 0 THEN 1 END),
                   COUNT(CASE WHEN quantity > 0 AND quantity <= ? THEN 1 END)
            FROM products
        """, (LOW_STOCK_LEVEL,))
        stock_cost, out, low = cursor.fetchone()

        set_card("stock", fmt_money(stock_cost), "at cost price")
        set_card(
            "alerts", str(out + low),
            f"{out} out  |  {low} low" if out + low else "all stocked up",
            "#b91c1c" if out else CLR_AMBER if low else CLR_GREEN,
            colour="#b91c1c" if out else CLR_TEXT
        )

        # 7-day chart
        cursor.execute("""
            SELECT substr(s.sale_date, 1, 10), SUM(si.line_total)
            FROM sales s JOIN sale_items si ON si.sale_id = s.sale_id
            WHERE s.sale_date >= ? AND s.status = 'completed'
            GROUP BY 1
        """, (week_start + " 00:00:00",))
        per_day = dict(cursor.fetchall())
        week[:] = []
        for back in range(6, -1, -1):
            day = now - timedelta(days=back)
            week.append((day.strftime("%a"),
                         per_day.get(day.strftime(DATE_FORMAT), 0) or 0))
        draw_week()

        clear_table(recent_table)
        cursor.execute("""
            SELECT s.sale_id, substr(s.sale_date, 12, 5),
                   COALESCE(SUM(si.quantity), 0), s.total_amount, s.status
            FROM sales s LEFT JOIN sale_items si ON si.sale_id = s.sale_id
            GROUP BY s.sale_id ORDER BY s.sale_id DESC LIMIT 12
        """)
        for sale_id, clock, items, total, status in cursor.fetchall():
            recent_table.insert(
                "", "end",
                values=(sale_id, clock, items, fmt_money(total)),
                tags=("voided",) if status == "voided" else ()
            )
        recent_table.refresh_view()

        clear_table(alert_table)
        cursor.execute("""
            SELECT product_id, product_name, quantity FROM products
            WHERE quantity <= ?
            ORDER BY quantity, product_name COLLATE NOCASE LIMIT 30
        """, (LOW_STOCK_LEVEL,))
        for pid, name, quantity in cursor.fetchall():
            alert_table.insert("", "end", values=(pid, name, quantity),
                               tags=("out" if quantity == 0 else "low",))

    register_listener(home_page, refresh)
    pages["home"]["on_show"] = refresh

    def tick():
        try:
            if current_page["key"] == "home":
                refresh()
            root.after(30000, tick)
        except tk.TclError:
            pass

    root.after(30000, tick)


# ── Automatic daily backup ────────────────────────────────────────────────────
def _auto_backup():
    """Run a backup once per day. Keeps only the last 30 backups."""
    try:
        last = get_setting("last_auto_backup", "")
        today = datetime.now().strftime("%Y-%m-%d")
        if last == today:
            return  # already backed up today
        path = backup_database()
        save_setting("last_auto_backup", today)
        # Prune: keep only the 30 most recent backups
        try:
            files = sorted([
                os.path.join(BACKUP_FOLDER, f)
                for f in os.listdir(BACKUP_FOLDER)
                if f.startswith("pos_backup_") and f.endswith(".db")
            ])
            for old in files[:-30]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        except OSError:
            pass
    except Exception:
        pass


def _schedule_daily_backup():
    """Check every hour whether a daily backup is due."""
    _auto_backup()
    root.after(3_600_000, _schedule_daily_backup)  # 1 hour in ms


def close_application(parent=None):
    parent = parent or root
    for entry in list(pages.values()):
        check = entry.get("unsaved")
        if check and check():
            if not messagebox.askyesno(
                "Cart Not Empty",
                "There are items in the cart that have not been sold.\n"
                "Exit anyway and lose the cart?",
                parent=parent
            ):
                return
            break
    else:
        if not messagebox.askyesno(
            "Exit", "Close the POS system?", parent=parent
        ):
            return

    # Stop the sync thread before closing the DB
    if _SYNC_AVAILABLE:
        try:
            _sync.stop()
        except Exception:
            pass

    connection.close()
    root.destroy()


build_home()


# -------------------- START APPLICATION --------------------
root.protocol("WM_DELETE_WINDOW", close_application)
bind_navigation_keys(root)

# Start cloud sync (runs in background thread; harmless if not configured)
if _SYNC_AVAILABLE:
    _sync.on_status_change(_on_sync_status_change)
    _sync.start()
    _update_sync_indicator()   # set initial label state

go_to("home")
# Start daily backup scheduler (runs first backup 30s after launch)
root.after(30_000, _schedule_daily_backup)
root.mainloop()
