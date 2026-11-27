import sqlite3, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, requests

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pos.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=== LOCAL SQLite products ===")
prods = conn.execute("""
    SELECT product_id, product_name, quantity, selling_price, synced
    FROM products ORDER BY product_name COLLATE NOCASE
""").fetchall()
for p in prods:
    print(f"  #{p['product_id']:2}  {p['product_name']:<15}  qty={p['quantity']:>4}  synced={p['synced']}")

print("\n=== LOCAL SQLite recent sales with items ===")
recent = conn.execute("""
    SELECT s.sale_id, s.sale_date, s.total_amount,
           si.product_name, si.quantity
    FROM sales s JOIN sale_items si ON si.sale_id=s.sale_id
    ORDER BY s.sale_id DESC, si.sale_item_id
    LIMIT 20
""").fetchall()
for r in recent:
    print(f"  Sale #{r['sale_id']}  {r['sale_date'][:16]}  "
          f"{r['product_name']} x{r['quantity']}")

conn.close()

print("\n=== SUPABASE products ===")
if config.is_configured():
    headers = {"apikey": config.SUPABASE_KEY,
               "Authorization": f"Bearer {config.SUPABASE_KEY}"}
    r = requests.get(f"{config.SUPABASE_URL}/rest/v1/products",
                     headers=headers,
                     params={"select": "product_id,product_name,quantity",
                             "order": "product_name.asc"},
                     timeout=10)
    for p in r.json():
        print(f"  #{p['product_id']:2}  {p['product_name']:<15}  qty={p['quantity']}")
else:
    print("  Not configured")
