"""
临时种子脚本：在初始种子数据上注入更多商品、客户、入库单、销售单，让界面看起来"满"。
关键：不动 YCY-350-N（留给用户做 FIFO demo）。
跑一次即可，幂等（重复跑不会重复插入）。
"""
from datetime import date, timedelta
import database


def days(n):
    return (date.today() + timedelta(days=n)).isoformat()


NEW_SKUS = [
    ("MGZ-280-N", "蘑菇炸预制菜", "280g·正常", "份", "cold", 30, 15.0, 26.8),
    ("BHJ-450-N", "白菜煎饺",     "450g",       "份", "freeze", 20, 22.0, 38.0),
    ("KLR-300-N", "咖喱牛肉饭",   "300g·正常", "份", "cold", 35, 20.0, 34.5),
    ("DDXR-200-N", "宫保鸡丁",   "200g·小份", "份", "cold", 40, 12.0, 22.0),
]

NEW_CUSTOMERS = [
    ("13911100001", "王芳", "北京市朝阳区国贸 1 号"),
    ("13911100002", "刘强", "上海市浦东新区世纪大道 88 号"),
    ("13922200003", "陈晨", "广州市天河区珠江新城 26 号"),
    ("13933300004", "杨柳", "深圳市南山区科技园 18 号"),
    ("13944400005", "周敏", "成都市高新区天府大道 200 号"),
    ("13955500006", "吴磊", "武汉市江汉区解放大道 56 号"),
    ("13966600007", "孙莉", "西安市雁塔区高新路 99 号"),
    ("13977700008", "钱涛", "南京市鼓楼区中山路 188 号"),
]

# 入库单：每条 = (supplier_id, sku_code, batch_no, days_to_expire, location_code, qty, unit_price)
# 不动 YCY-350-N
INBOUNDS = [
    (1, "FQNF-500",   "FQNF-A-20260517",   60,  "B-01-01", 100, 25.0),
    (1, "FQNF-500",   "FQNF-B-20260517",   90,  "B-01-02",  80, 25.0),
    (2, "MLXJ-400",   "MLXJ-A-20260517",   10,  "A-01-02", 150, 20.0),
    (2, "MLXJ-400",   "MLXJ-B-20260517",   25,  "A-02-01", 200, 20.0),
    (1, "MGZ-280-N",  "MGZ-A-20260517",    12,  "A-01-01",  80, 15.0),
    (2, "BHJ-450-N",  "BHJ-A-20260517",   120,  "B-01-01", 120, 22.0),
    (1, "KLR-300-N",  "KLR-A-20260517",    20,  "A-01-02",  90, 20.0),
    (2, "YCY-700-N",  "YCY700-A-20260517", 14,  "A-01-01",  50, 33.0),
    (1, "DDXR-200-N", "DDXR-A-20260517",   18,  "A-02-01", 180, 12.0),
]

# 销售单：(customer_phone, channel, status, [(sku_code, qty, price)])
SALES = [
    ("13900001111", "tmall",  "completed", [("FQNF-500", 5, 42.0), ("MGZ-280-N", 3, 26.8)]),
    ("13900002222", "jd",     "completed", [("MLXJ-400", 8, 35.0)]),
    ("13911100001", "douyin", "completed", [("BHJ-450-N", 4, 38.0), ("FQNF-500", 2, 42.0)]),
    ("13911100002", "applet", "completed", [("KLR-300-N", 6, 34.5)]),
    ("13922200003", "tmall",  "completed", [("YCY-700-N", 2, 55.0), ("MLXJ-400", 3, 35.0)]),
    ("13933300004", "jd",     "reserved",  [("FQNF-500", 4, 42.0), ("KLR-300-N", 2, 34.5)]),
    ("13944400005", "tmall",  "reserved",  [("MLXJ-400", 10, 35.0)]),
    ("13955500006", "douyin", "completed", [("DDXR-200-N", 8, 22.0)]),
    ("13966600007", "applet", "completed", [("MGZ-280-N", 5, 26.8), ("DDXR-200-N", 3, 22.0)]),
]


def main():
    with database.get_conn() as conn:
        admin = conn.execute("SELECT id FROM user WHERE username='admin'").fetchone()
        manager = conn.execute("SELECT id FROM user WHERE username='manager'").fetchone()
        admin_id = admin["id"]
        manager_id = manager["id"]

        for s in NEW_SKUS:
            conn.execute(
                "INSERT OR IGNORE INTO sku (code,name,spec,unit,storage_zone,safety_stock,cost_price,sale_price) VALUES (?,?,?,?,?,?,?,?)",
                s,
            )

        for c in NEW_CUSTOMERS:
            conn.execute(
                "INSERT OR IGNORE INTO customer (phone,name,default_address) VALUES (?,?,?)",
                c,
            )

        # ---- 入库单（已审核）----
        today_str = date.today().strftime("%Y%m%d")
        for idx, (sup_id, sku_code, batch_no, exp_days, loc_code, qty, price) in enumerate(INBOUNDS, start=1):
            order_no = f"RKD{today_str}{idx:03d}"
            if conn.execute("SELECT id FROM inbound_order WHERE order_no=?", (order_no,)).fetchone():
                continue  # 已存在，跳过
            sku = conn.execute("SELECT id FROM sku WHERE code=?", (sku_code,)).fetchone()
            loc = conn.execute("SELECT id FROM location WHERE code=?", (loc_code,)).fetchone()
            if not sku or not loc:
                print(f"  skip {order_no}: sku={sku_code} or loc={loc_code} not found")
                continue

            prod_date = days(-1)
            exp_date = days(exp_days)

            conn.execute(
                """INSERT INTO inbound_order (order_no, supplier_id, status, creator_id, approver_id, note, approved_at)
                   VALUES (?, ?, 'approved', ?, ?, '种子丰富数据', CURRENT_TIMESTAMP)""",
                (order_no, sup_id, manager_id, admin_id),
            )
            existing_batch = conn.execute("SELECT id FROM batch WHERE batch_no=?", (batch_no,)).fetchone()
            if existing_batch:
                batch_id = existing_batch["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO batch (batch_no, sku_id, production_date, expiry_date, supplier_id, inbound_order_no)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (batch_no, sku["id"], prod_date, exp_date, sup_id, order_no),
                )
                batch_id = cur.lastrowid

            conn.execute(
                """INSERT INTO inbound_item (order_no, sku_id, batch_no, production_date, expiry_date, location_id, quantity, unit_price, batch_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_no, sku["id"], batch_no, prod_date, exp_date, loc["id"], qty, price, batch_id),
            )
            conn.execute(
                """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id)
                   VALUES (?, ?, ?, ?, ?, 'inbound', ?)""",
                (sku["id"], batch_id, loc["id"], qty, order_no, admin_id),
            )
            ex_inv = conn.execute(
                "SELECT id FROM inventory WHERE sku_id=? AND location_id=? AND batch_id=?",
                (sku["id"], loc["id"], batch_id),
            ).fetchone()
            if ex_inv:
                conn.execute(
                    "UPDATE inventory SET on_hand = on_hand + ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (qty, ex_inv["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO inventory (sku_id, location_id, batch_id, on_hand) VALUES (?, ?, ?, ?)",
                    (sku["id"], loc["id"], batch_id, qty),
                )

        # ---- 销售单 + 出库单 ----
        for idx, (phone, channel, status, items) in enumerate(SALES, start=1):
            sales_no = f"XSD{today_str}{idx:03d}"
            if conn.execute("SELECT id FROM sales_order WHERE order_no=?", (sales_no,)).fetchone():
                continue
            cust = conn.execute("SELECT * FROM customer WHERE phone=?", (phone,)).fetchone()
            if not cust:
                continue

            total = sum(q * p for _, q, p in items)
            conn.execute(
                """INSERT INTO sales_order (order_no, channel, customer_id, total_amount, status,
                                            receiver_name, receiver_phone, receiver_addr, creator_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sales_no, channel, cust["id"], total, status,
                 cust["name"], cust["phone"], cust["default_address"], admin_id),
            )
            for sku_code, qty, price in items:
                sku = conn.execute("SELECT id FROM sku WHERE code=?", (sku_code,)).fetchone()
                conn.execute(
                    "INSERT INTO sales_order_item (order_no, sku_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
                    (sales_no, sku["id"], qty, price),
                )

            ob_no = f"CKD{today_str}{idx:03d}"
            ob_status = "completed" if status == "completed" else "pending"
            if ob_status == "completed":
                conn.execute(
                    """INSERT INTO outbound_order (order_no, sales_order_no, status, picker_id, completed_at)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (ob_no, sales_no, ob_status, admin_id),
                )
            else:
                conn.execute(
                    "INSERT INTO outbound_order (order_no, sales_order_no, status) VALUES (?, ?, ?)",
                    (ob_no, sales_no, ob_status),
                )

            for sku_code, qty, _ in items:
                sku = conn.execute("SELECT id FROM sku WHERE code=?", (sku_code,)).fetchone()
                remaining = qty
                batches = conn.execute(
                    """SELECT inv.id AS inv_id, inv.batch_id, inv.location_id, inv.on_hand, inv.reserved, b.expiry_date
                       FROM inventory inv JOIN batch b ON b.id=inv.batch_id
                       WHERE inv.sku_id=? AND (inv.on_hand - inv.reserved) > 0
                       ORDER BY b.expiry_date ASC""",
                    (sku["id"],),
                ).fetchall()
                for batch in batches:
                    if remaining <= 0:
                        break
                    avail = batch["on_hand"] - batch["reserved"]
                    take = min(remaining, avail)
                    conn.execute(
                        "INSERT INTO outbound_item (order_no, sku_id, batch_id, location_id, quantity) VALUES (?, ?, ?, ?, ?)",
                        (ob_no, sku["id"], batch["batch_id"], batch["location_id"], take),
                    )
                    if ob_status == "completed":
                        conn.execute(
                            """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id)
                               VALUES (?, ?, ?, ?, ?, 'outbound', ?)""",
                            (sku["id"], batch["batch_id"], batch["location_id"], -take, ob_no, admin_id),
                        )
                        conn.execute(
                            "UPDATE inventory SET on_hand = on_hand - ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (take, batch["inv_id"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE inventory SET reserved = reserved + ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (take, batch["inv_id"]),
                        )
                    remaining -= take

        # 汇报
        def c(sql, *args):
            return conn.execute(sql, args).fetchone()[0]
        sku_n = c("SELECT COUNT(*) FROM sku")
        cust_n = c("SELECT COUNT(*) FROM customer")
        inb_n = c("SELECT COUNT(*) FROM inbound_order")
        batch_n = c("SELECT COUNT(*) FROM batch")
        inv_n = c("SELECT COUNT(*) FROM inventory")
        oh_sum = c("SELECT COALESCE(SUM(on_hand),0) FROM inventory")
        rv_sum = c("SELECT COALESCE(SUM(reserved),0) FROM inventory")
        so_n = c("SELECT COUNT(*) FROM sales_order")
        so_reserved = c("SELECT COUNT(*) FROM sales_order WHERE status=?", "reserved")
        so_done = c("SELECT COUNT(*) FROM sales_order WHERE status=?", "completed")
        log_n = c("SELECT COUNT(*) FROM stock_log")
        ycy_stock = c("SELECT COALESCE(SUM(on_hand),0) FROM inventory inv JOIN sku ON sku.id=inv.sku_id WHERE sku.code=?", "YCY-350-N")
        print(f"SKU:        {sku_n}")
        print(f"customer:   {cust_n}")
        print(f"inbound:    {inb_n}")
        print(f"batch:      {batch_n}")
        print(f"inventory:  {inv_n}")
        print(f"on_hand:    {oh_sum}")
        print(f"reserved:   {rv_sum}")
        print(f"sales:      {so_n}  (reserved={so_reserved}, completed={so_done})")
        print(f"stock_log:  {log_n}")
        print(f"YCY-350-N on_hand: {ycy_stock}  (expect 0 -- reserved for user's FIFO demo)")


if __name__ == "__main__":
    main()
