import sqlite3

conn = sqlite3.connect("warehouse.db")
conn.row_factory = sqlite3.Row

print("== menu_permission per user ==")
for u in conn.execute("SELECT id, username, role, position FROM user ORDER BY id").fetchall():
    keys = [r["menu_key"] for r in conn.execute(
        "SELECT menu_key FROM menu_permission WHERE user_id = ? ORDER BY menu_key", (u["id"],)
    ).fetchall()]
    print(f"  {u['username']:<12} [{u['role']:<7}] {u['position'] or '-':<18} -> {keys}")

print("\n== notification_channel ==")
for r in conn.execute("SELECT * FROM notification_channel ORDER BY code").fetchall():
    print(" ", dict(r))

print("\n== alert_rule ==")
for r in conn.execute("SELECT * FROM alert_rule ORDER BY id").fetchall():
    print(" ", dict(r))
