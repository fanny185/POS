# ============================================================
#  RetailPOS  –  One-time ID alignment fix
#
#  Run this ONCE on the new machine, BEFORE making any sale.
#  It pushes the local SQLite auto-increment counters above
#  the current Supabase maximums, so future sale IDs and
#  product IDs coming from this machine can never collide
#  with (and silently overwrite) rows from the other machine.
#
#  Usage:  python fix_ids.py
# ============================================================

import os, sys, sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pos.db")

# Current maximum IDs in Supabase (checked on 2026-11-26).
# We add a safety gap of 1000 on top so both machines have
# plenty of room.
SB_MAX = {
    "sales":       20,
    "products":    8,
    "sale_items":  34,
}
GAP = 1000

# Bump each table's AUTOINCREMENT sequence
conn = sqlite3.connect(DB)
cur = conn.cursor()

for table, sb_max in SB_MAX.items():
    # What does the local table already use?
    cur.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {table}")
    local_max = cur.fetchone()[0]

    target = sb_max + GAP
    if local_max >= target:
        print(f"  {table:12s}  already at {local_max}, no change needed")
        continue

    # sqlite_sequence may or may not have a row for this table yet
    cur.execute("SELECT COUNT(*) FROM sqlite_sequence WHERE name = ?", (table,))
    if cur.fetchone()[0]:
        cur.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (target, table))
    else:
        cur.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (table, target))
    print(f"  {table:12s}  counter moved {local_max} -> {target}")

conn.commit()

# Verify
print("\nVerification (new next-IDs):")
for table in SB_MAX:
    cur.execute(f"SELECT seq FROM sqlite_sequence WHERE name = ?", (table,))
    row = cur.fetchone()
    print(f"  {table:12s}  next id = {(row[0] + 1) if row else '?'}")

conn.close()

print("\nDone. This machine will now generate sale IDs above the other machine's.")
print("You can make sales normally — no conflicts.")
