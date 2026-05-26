"""库存历史快照：v3 简化版（不再按存储区分组）。

- snapshot_inventory(): 把当前 inventory 按 (snapshot_date, sku) 写入 inventory_snapshot
- backfill_from_stock_log(months): 用 stock_log 累计反推历史月末快照
"""
from datetime import date, timedelta

import database


def snapshot_inventory(snapshot_date=None):
    snapshot_date = snapshot_date or date.today().isoformat()
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id AS sku_id, COALESCE(SUM(i.on_hand), 0) AS on_hand
               FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.id"""
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO inventory_snapshot (snapshot_date, sku_id, on_hand) VALUES (?, ?, ?)",
                (snapshot_date, r["sku_id"], r["on_hand"]),
            )
    return len(rows)


def backfill_from_stock_log(months=12):
    """用 stock_log 累计反推过去 N 个月每月末的库存快照（按 SKU）。"""
    today = date.today()
    with database.get_conn() as conn:
        skus = conn.execute("SELECT id FROM sku").fetchall()
        if not skus:
            return 0
        month_ends = _month_ends(today, months)
        wrote = 0
        for m_end in month_ends:
            for s in skus:
                total = conn.execute(
                    "SELECT COALESCE(SUM(delta), 0) FROM stock_log WHERE sku_id = ? AND date(occurred_at) <= ?",
                    (s["id"], m_end.isoformat()),
                ).fetchone()[0]
                on_hand = max(0, total)
                conn.execute(
                    "INSERT OR REPLACE INTO inventory_snapshot (snapshot_date, sku_id, on_hand) VALUES (?, ?, ?)",
                    (m_end.isoformat(), s["id"], on_hand),
                )
                wrote += 1
    return wrote


def _month_ends(today, months):
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        if m == 12:
            ny, nm = y + 1, 1
        else:
            ny, nm = y, m + 1
        end = date(ny, nm, 1) - timedelta(days=1)
        out.append(end)
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    return sorted(out)
