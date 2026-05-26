"""塞 demo 数据：30 天 × 5 个 SKU × 每天 1-2 笔出入库流水。

入口：python seed_demo.py
- 取前 5 个 SKU（按 ID 排序）
- 从 30 天前到今天，每天随机 1-2 笔 stock_log
- 50% 几率 inbound（+20~80），50% 出库 outbound（-5~30，确保不变负）
- 用第一个 location 作为默认库位
- 入库时同时生成 batch（每个 SKU 共用 1 个 demo 批次）
- 同步更新 inventory.on_hand
- 不创建 inbound_order / outbound_order，仅 stock_log
"""
import random
from datetime import date, datetime, timedelta

import database


def main():
    random.seed(42)
    with database.get_conn() as conn:
        # 优先选用最新主题的 SEED_SKUS 对应的 SKU；若不存在则取最新加入的 5 个
        seed_codes = [s[0] for s in database.SEED_SKUS]
        placeholders = ",".join("?" * len(seed_codes))
        skus = conn.execute(
            f"SELECT id, code, name FROM sku WHERE code IN ({placeholders}) ORDER BY id",
            seed_codes,
        ).fetchall()
        if not skus:
            # fallback: 取前 5 个
            skus = conn.execute("SELECT id, code, name FROM sku ORDER BY id LIMIT 5").fetchall()
        if not skus:
            print("没有 SKU，先跑 database.py 初始化")
            return
        location = conn.execute("SELECT id, code FROM location ORDER BY id LIMIT 1").fetchone()
        if not location:
            print("没有库位")
            return
        loc_id = location["id"]

        # 为每个 SKU 准备一个 demo 批次（如果还没有）
        sku_batches = {}
        for sku in skus:
            existing = conn.execute(
                "SELECT id FROM batch WHERE sku_id=? AND batch_no LIKE 'DEMO-%' LIMIT 1",
                (sku["id"],)
            ).fetchone()
            if existing:
                sku_batches[sku["id"]] = existing["id"]
            else:
                today = date.today()
                cur = conn.execute(
                    "INSERT INTO batch (batch_no, sku_id) VALUES (?, ?)",
                    (f"DEMO-{sku['code']}-{today.strftime('%Y%m%d')}", sku["id"]),
                )
                sku_batches[sku["id"]] = cur.lastrowid

        # 跟踪每个 SKU 的当前 on_hand（用于避免出库变负）
        running = {}
        for sku in skus:
            row = conn.execute(
                "SELECT COALESCE(SUM(on_hand), 0) AS h FROM inventory WHERE sku_id=?",
                (sku["id"],)
            ).fetchone()
            running[sku["id"]] = row["h"]

        # 30 天 × 每天 1-2 笔
        count_in = 0
        count_out = 0
        today = date.today()
        for d_offset in range(30, 0, -1):
            day = today - timedelta(days=d_offset)
            n_today = random.randint(1, 2)
            for _ in range(n_today):
                sku = random.choice(skus)
                sku_id = sku["id"]
                batch_id = sku_batches[sku_id]
                # 决定入/出（早期偏入库以保证后期出库不变负）
                weight_in = 0.7 if d_offset > 20 else 0.5
                is_inbound = random.random() < weight_in

                if is_inbound:
                    qty = random.randint(20, 80)
                    delta = qty
                    event = "inbound"
                    running[sku_id] += qty
                    count_in += 1
                else:
                    max_out = min(30, running[sku_id])
                    if max_out < 5:
                        # 库存不够出库就改为入库
                        qty = random.randint(20, 50)
                        delta = qty
                        event = "inbound"
                        running[sku_id] += qty
                        count_in += 1
                    else:
                        qty = random.randint(5, max_out)
                        delta = -qty
                        event = "outbound"
                        running[sku_id] -= qty
                        count_out += 1

                # 时间戳：当天上午 9:00~17:00 随机
                hour = random.randint(9, 17)
                minute = random.randint(0, 59)
                occurred = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)

                conn.execute(
                    """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc,
                                              event_type, occurred_at, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (sku_id, batch_id, loc_id, delta, f"DEMO-{day.isoformat()}",
                     event, occurred.isoformat(sep=" "), "演示数据"),
                )

                # 同步 inventory
                inv = conn.execute(
                    "SELECT id, on_hand FROM inventory WHERE sku_id=? AND batch_id=? AND location_id=?",
                    (sku_id, batch_id, loc_id)
                ).fetchone()
                if inv:
                    conn.execute(
                        "UPDATE inventory SET on_hand = on_hand + ?, updated_at = CURRENT_TIMESTAMP WHERE id=?",
                        (delta, inv["id"])
                    )
                else:
                    conn.execute(
                        "INSERT INTO inventory (sku_id, batch_id, location_id, on_hand) VALUES (?, ?, ?, ?)",
                        (sku_id, batch_id, loc_id, max(0, delta))
                    )

        print(f"塞入 demo: {count_in} 笔入库, {count_out} 笔出库")
        print("各 SKU 现在仓数：")
        for sku in skus:
            row = conn.execute(
                "SELECT COALESCE(SUM(on_hand), 0) AS h FROM inventory WHERE sku_id=?",
                (sku["id"],)
            ).fetchone()
            print(f"  {sku['code']} {sku['name']}: {row['h']}")


if __name__ == "__main__":
    main()
