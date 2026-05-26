"""重置演示数据：清空业务流水 + 旧主题 SKU，重新 SEED + 生成 demo。

入口：python reset_demo.py  或  POST /reset-demo（admin）

危险操作：会清空所有业务数据（库存/流水/单据）。SKU 按新 SEED 重置。
用户表、岗位、权限、通知规则不动。
"""
import database
import seed_demo as _seed_demo


def reset():
    new_sku_codes = [s[0] for s in database.SEED_SKUS]

    with database.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")

        # 1. 清空业务流水 + 单据明细 + 单据
        tables = [
            "stock_log",
            "inventory",
            "transfer_item", "transfer_order",
            "outbound_item", "outbound_order",
            "inbound_item", "inbound_order",
            "damage_log",
            "stocktake_item", "stocktake_order",
            "batch",
        ]
        for t in tables:
            try:
                n = conn.execute(f"DELETE FROM {t}").rowcount
                if n: print(f"  清空 {t}: {n} 行")
            except Exception as e:
                print(f"  跳过 {t}: {e}")

        # 2. 删旧主题的 SKU（新 SEED 之外的）
        placeholders = ",".join("?" * len(new_sku_codes))
        n_sku = conn.execute(
            f"DELETE FROM sku WHERE code NOT IN ({placeholders})", new_sku_codes
        ).rowcount
        print(f"  清掉旧 SKU: {n_sku} 行")

        # 3. 清掉相关历史预警事件
        conn.execute("DELETE FROM alert_event")

        conn.execute("PRAGMA foreign_keys = ON")

    # 4. 重新跑 init_db：会 INSERT OR IGNORE 新 SEED_SKUS（之前删过，现在会插入）
    database.init_db()
    print("  init_db 完成（新 SEED 已就位）")

    # 5. 跑 seed_demo 生成 30 天 demo 流水
    print("  生成 30 天 demo 流水...")
    _seed_demo.main()

    # 6. 额外生成几张待审入库单 + 待审报损
    _seed_pending_approvals()
    print("  完成。")


def _seed_pending_approvals():
    """生成 3 张待审入库 + 5 条待审报损，让工作台『待审批』数字非 0。"""
    import random
    from datetime import datetime
    random.seed(7)
    with database.get_conn() as conn:
        skus = conn.execute("SELECT id, code FROM sku ORDER BY id").fetchall()
        location = conn.execute("SELECT id FROM location ORDER BY id LIMIT 1").fetchone()
        admin = conn.execute("SELECT id FROM user WHERE username='admin'").fetchone()
        if not (skus and location and admin):
            return

        # 3 张待审入库（draft）
        for i in range(3):
            order_no = database.gen_order_no(conn, "RKD", "inbound_order")
            conn.execute(
                "INSERT INTO inbound_order (order_no, operator_id, status, creator_id, note) VALUES (?, ?, 'draft', ?, ?)",
                (order_no, admin["id"], admin["id"], f"演示待审入库单 #{i+1}"),
            )
            n_items = random.randint(1, 3)
            for _ in range(n_items):
                sku = random.choice(skus)
                conn.execute(
                    "INSERT INTO inbound_item (order_no, sku_id, batch_no, location_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?, ?)",
                    (order_no, sku["id"],
                     f"{sku['code']}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{_}",
                     location["id"],
                     random.randint(10, 50),
                     random.choice([20.0, 100.0, 500.0])),
                )
        print(f"  生成 3 张待审入库单")

        # 5 条待审报损（基于现有 inventory 行）
        invs = conn.execute(
            "SELECT id, sku_id, batch_id, location_id, on_hand FROM inventory WHERE on_hand > 5 LIMIT 5"
        ).fetchall()
        n_dmg = 0
        for inv in invs:
            qty = random.randint(1, min(5, inv["on_hand"]))
            conn.execute(
                """INSERT INTO damage_log (sku_id, batch_id, location_id, quantity, reason_type, reason_note,
                                          applicant_id, status)
                   VALUES (?, ?, ?, ?, 'broken', '演示数据', ?, 'pending')""",
                (inv["sku_id"], inv["batch_id"], inv["location_id"], qty, admin["id"]),
            )
            n_dmg += 1
        print(f"  生成 {n_dmg} 条待审报损")


if __name__ == "__main__":
    reset()
