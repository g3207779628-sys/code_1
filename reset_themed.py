"""完整重置主题数据：办公用品 + 老板茶水/酒柜 仓储。

清理 → 重建仓库/存储区/库位 → 重建批次 → 生成 30 天 demo 流水（分布到多库位）。

入口：python reset_themed.py  或  POST /backups (action=reset_themed)
"""
import random
from datetime import date, datetime, timedelta

import database

# ============ 主题数据定义 ============

WAREHOUSES = [
    ("金山主仓", "北京市海淀区中关村东路 1 号"),
    ("研发中心仓", "杭州市余杭区文一西路 969 号"),
]

STORAGE_AREAS = [
    ("酒柜区", "存放白酒、红酒等酒类（恒温）"),
    ("文具区", "纸张、笔类、办公耗材"),
    ("茶水区", "茶叶、咖啡、矿泉水"),
    ("隔离区", "不合格品/待报损品暂存"),
]

# (库位code, 所属仓库name, 类型, 所属存储区name)
LOCATIONS = [
    # 金山主仓 — 6 个库位
    ("酒柜-A1",  "金山主仓",   "normal", "酒柜区"),
    ("酒柜-A2",  "金山主仓",   "normal", "酒柜区"),
    ("文具架-B1", "金山主仓",   "normal", "文具区"),
    ("文具架-B2", "金山主仓",   "normal", "文具区"),
    ("茶水柜-C1", "金山主仓",   "normal", "茶水区"),
    ("隔离-D1",  "金山主仓",   "isolation", "隔离区"),
    # 研发中心仓 — 3 个库位
    ("分仓-酒柜",   "研发中心仓", "normal", "酒柜区"),
    ("分仓-文具",   "研发中心仓", "normal", "文具区"),
    ("分仓-茶水",   "研发中心仓", "normal", "茶水区"),
]

# 每个 SKU 默认分布到哪些库位（按主题/类别）
SKU_DEFAULT_LOCATIONS = {
    # 原 5 个
    "MT-FW-500":  ["酒柜-A1", "酒柜-A2", "分仓-酒柜"],     # 茅台
    "WLY-PJ-500": ["酒柜-A1", "酒柜-A2"],                  # 五粮液
    "A4-COPY":    ["文具架-B1", "文具架-B2", "分仓-文具"],  # A4 纸
    "PEN-SIGN":   ["文具架-B1", "文具架-B2"],              # 签字笔
    "TEA-LJ":     ["茶水柜-C1", "分仓-茶水"],              # 龙井
    # v12 新增 15 个
    "STAPLER":     ["文具架-B1", "文具架-B2"],
    "ENVELOPE-A4": ["文具架-B2"],                          # 单库位，易低库存
    "FOLDER-CLR":  ["文具架-B1", "分仓-文具"],
    "STICKY-NOTE": ["文具架-B1", "文具架-B2"],
    "NOTEBOOK-B5": ["文具架-B2", "分仓-文具"],
    "PEN-BALL":    ["文具架-B1", "文具架-B2"],
    "CALC-12":     ["文具架-B1"],
    "TAPE-CLR":    ["文具架-B1", "文具架-B2"],
    "CLIP-PAPER":  ["文具架-B1"],
    "WATER-555":   ["茶水柜-C1", "分仓-茶水"],
    "COFFEE-BEAN": ["茶水柜-C1"],
    "CUP-PAPER":   ["茶水柜-C1", "分仓-茶水"],
    "BATTERY-AA":  ["文具架-B1"],
    "PRINT-INK":   ["文具架-B2"],
    "HIGHLIGHTER": ["文具架-B1", "文具架-B2"],
}


def reset():
    """完整重置：清业务流水 + 重建仓库/存储区/库位 + 重新塞 demo。"""
    with database.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        # 1. 清空业务流水
        tables = [
            "stock_log", "inventory",
            "transfer_item", "transfer_order",
            "outbound_item", "outbound_order",
            "inbound_item", "inbound_order",
            "damage_log",
            "stocktake_item", "stocktake_order",
            "batch",
            "alert_event",
        ]
        for t in tables:
            try:
                n = conn.execute(f"DELETE FROM {t}").rowcount
                if n: print(f"  清空 {t}: {n} 行")
            except Exception:
                pass

        # 2. 清旧的 location / warehouse（storage_area 已在 v16 砸表）
        conn.execute("DELETE FROM location")
        try: conn.execute("DELETE FROM storage_area")
        except Exception: pass
        conn.execute("DELETE FROM warehouse")
        print("  已清空 location / warehouse")

        # 3. 清旧 SKU（非新主题的）
        new_codes = [s[0] for s in database.SEED_SKUS]
        conn.execute(
            f"DELETE FROM sku WHERE code NOT IN ({','.join('?' * len(new_codes))})", new_codes
        )
        conn.execute("PRAGMA foreign_keys = ON")

    # 4. 重新建 SKU/供应商（如果上面 SQL DELETE 了所有）
    database.init_db()
    print("  init_db 完成")

    # 5. 建仓库 / 存储区 / 库位
    with database.get_conn() as conn:
        wh_map = {}
        for name, addr in WAREHOUSES:
            cur = conn.execute("INSERT INTO warehouse (name, address) VALUES (?, ?)", (name, addr))
            wh_map[name] = cur.lastrowid
        print(f"  建 {len(WAREHOUSES)} 个仓库")

        area_map = {}
        for name, note in STORAGE_AREAS:
            cur = conn.execute("INSERT INTO storage_area (name, note) VALUES (?, ?)", (name, note))
            area_map[name] = cur.lastrowid
        print(f"  建 {len(STORAGE_AREAS)} 个存储区")

        loc_map = {}
        for code, wh_name, _zone_type, _area_name in LOCATIONS:
            cur = conn.execute(
                "INSERT INTO location (warehouse_id, code) VALUES (?, ?)",
                (wh_map[wh_name], code),
            )
            loc_map[code] = cur.lastrowid
        print(f"  建 {len(LOCATIONS)} 个库位")

        # 6. 给每个 SKU 在它的默认库位上各建一个 demo 批次
        skus = conn.execute("SELECT id, code, name FROM sku WHERE code IN ({})".format(
            ",".join("?" * len(SKU_DEFAULT_LOCATIONS))
        ), list(SKU_DEFAULT_LOCATIONS.keys())).fetchall()
        sku_batches = {}  # {sku_id: [(batch_id, loc_id), ...]}
        today = date.today()
        for sku in skus:
            distrib = SKU_DEFAULT_LOCATIONS[sku["code"]]
            sku_batches[sku["id"]] = []
            for loc_code in distrib:
                loc_id = loc_map[loc_code]
                cur = conn.execute(
                    "INSERT INTO batch (batch_no, sku_id) VALUES (?, ?)",
                    (f"DEMO-{sku['code']}-{loc_code}-{today.strftime('%Y%m%d')}", sku["id"]),
                )
                sku_batches[sku["id"]].append((cur.lastrowid, loc_id))

        # 7. 生成 30 天 demo 流水：每天每个 SKU 在它的某个库位上有 1-2 笔出入库
        random.seed(42)
        running = {}  # {(sku_id, loc_id, batch_id): on_hand}
        count_in = 0
        count_out = 0
        for d_offset in range(30, 0, -1):
            day = today - timedelta(days=d_offset)
            for sku in skus:
                n_today = random.randint(1, 2)
                for _ in range(n_today):
                    # 随机选这个 SKU 的某个库位
                    batch_id, loc_id = random.choice(sku_batches[sku["id"]])
                    key = (sku["id"], loc_id, batch_id)
                    current = running.get(key, 0)
                    # 决定入/出（前 20 天偏入库）
                    weight_in = 0.7 if d_offset > 20 else 0.5
                    is_in = random.random() < weight_in or current < 5
                    if is_in:
                        qty = random.randint(20, 80)
                        delta = qty
                        event = "inbound"
                        running[key] = current + qty
                        count_in += 1
                    else:
                        max_out = min(30, current)
                        if max_out < 5:
                            qty = random.randint(20, 50)
                            delta = qty
                            event = "inbound"
                            running[key] = current + qty
                            count_in += 1
                        else:
                            qty = random.randint(5, max_out)
                            delta = -qty
                            event = "outbound"
                            running[key] = current - qty
                            count_out += 1

                    hour = random.randint(9, 17)
                    minute = random.randint(0, 59)
                    occurred = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
                    conn.execute(
                        """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc,
                                                  event_type, occurred_at, note)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sku["id"], batch_id, loc_id, delta, f"DEMO-{day.isoformat()}",
                         event, occurred.isoformat(sep=" "), "演示数据"),
                    )

        # 8. 同步 inventory 表
        for (sku_id, loc_id, batch_id), on_hand in running.items():
            if on_hand <= 0: continue
            conn.execute(
                "INSERT INTO inventory (sku_id, batch_id, location_id, on_hand) VALUES (?, ?, ?, ?)",
                (sku_id, batch_id, loc_id, on_hand),
            )

        print(f"  {count_in} 笔入库 + {count_out} 笔出库")
        print(f"  生成 {len(running)} 行 inventory")

        # 9. 几张待审入库 + 待审报损（让工作台数据非 0）
        admin = conn.execute("SELECT id FROM user WHERE username='admin'").fetchone()
        if admin:
            for i in range(3):
                order_no = database.gen_order_no(conn, "RKD", "inbound_order")
                conn.execute(
                    "INSERT INTO inbound_order (order_no, operator_id, status, creator_id, note) VALUES (?, ?, 'draft', ?, ?)",
                    (order_no, admin["id"], admin["id"], f"演示待审入库单 #{i+1}"),
                )
                sku = random.choice(skus)
                loc_id = random.choice(SKU_DEFAULT_LOCATIONS[sku["code"]])
                conn.execute(
                    "INSERT INTO inbound_item (order_no, sku_id, batch_no, location_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?, ?)",
                    (order_no, sku["id"],
                     f"{sku['code']}-NEW-{datetime.now().strftime('%H%M%S')}-{i}",
                     loc_map[loc_id], random.randint(10, 50), 100.0),
                )
            print("  生成 3 张待审入库单")

            invs = conn.execute(
                "SELECT id, sku_id, batch_id, location_id, on_hand FROM inventory WHERE on_hand > 5 LIMIT 5"
            ).fetchall()
            for inv in invs:
                conn.execute(
                    """INSERT INTO damage_log (sku_id, batch_id, location_id, quantity, reason_type, reason_note,
                                              applicant_id, status)
                       VALUES (?, ?, ?, ?, 'broken', '演示数据', ?, 'pending')""",
                    (inv["sku_id"], inv["batch_id"], inv["location_id"],
                     random.randint(1, min(5, inv["on_hand"])), admin["id"]),
                )
            print(f"  生成 {len(invs)} 条待审报损")

        # 10. 丰满业务数据（v10）：多人审批 / 已审批入库 / 出入库 / 调拨 / 已审报损 / 盘点
        try:
            _seed_richer_business_data(conn, skus, sku_batches, loc_map, wh_map)
        except Exception as e:
            print(f"  _seed_richer FAIL: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()

    print("完成。")


def _seed_richer_business_data(conn, skus, sku_batches, loc_map, wh_map):
    """v10 — 生成丰满业务单据让工作台 / 流水 / 团队工作量都有数据可看。

    - 已审批入库单：多 approver_id（含今日 + 一周内）
    - 出库单：completed + pending 混合，多 picker_id + receiver_desc
    - 调拨单：completed + draft 混合，多 creator_id
    - 已审批报损：多 approver_id
    - 盘点单：closed + open 各 1 张
    """
    users = conn.execute("SELECT id, username FROM user").fetchall()
    user_lookup = {u["username"]: u["id"] for u in users}
    admin_id = user_lookup.get("admin")
    manager_id = user_lookup.get("manager", admin_id)
    purchaser_id = user_lookup.get("purchaser1", admin_id)
    receiver_id = user_lookup.get("receiver1", admin_id)
    putaway_id = user_lookup.get("putaway1", admin_id)
    cs_id = user_lookup.get("cs1", admin_id)
    packer_id = user_lookup.get("packer1", admin_id)
    shipping_id = user_lookup.get("shipping1", admin_id)

    inventory_rows = conn.execute(
        "SELECT id, sku_id, batch_id, location_id, on_hand FROM inventory WHERE on_hand > 10"
    ).fetchall()
    if not inventory_rows:
        return

    sku_list = list(skus)

    # === 已审批入库单 12 张（多 approver_id + 一周内 approved_at） ===
    approvers = [admin_id, manager_id, purchaser_id]
    creators = [receiver_id, purchaser_id, putaway_id]
    approved_inbounds = 0
    for i in range(12):
        order_no = database.gen_order_no(conn, "RKD", "inbound_order")
        approver = random.choice(approvers)
        creator = random.choice(creators)
        days_ago = random.randint(0, 7)
        ts = (datetime.now() - timedelta(days=days_ago, hours=random.randint(0, 10))).isoformat(sep=" ", timespec="seconds")
        conn.execute(
            "INSERT INTO inbound_order (order_no, operator_id, status, creator_id, approver_id, note, created_at, approved_at) "
            "VALUES (?, ?, 'approved', ?, ?, ?, ?, ?)",
            (order_no, creator, creator, approver, "演示已审批入库", ts, ts),
        )
        for _ in range(random.randint(1, 2)):
            sku = random.choice(sku_list)
            loc_code = random.choice(SKU_DEFAULT_LOCATIONS[sku["code"]])
            conn.execute(
                "INSERT INTO inbound_item (order_no, sku_id, batch_no, location_id, quantity, unit_price) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (order_no, sku["id"], f"{sku['code']}-{order_no}-{random.randint(1, 99)}",
                 loc_map[loc_code], random.randint(20, 100), round(random.uniform(50, 200), 2)),
            )
        approved_inbounds += 1
    print(f"  生成 {approved_inbounds} 张已审批入库单")

    # === 出库单 10 张（7 completed + 3 pending，多 picker_id） ===
    pickers = [admin_id, cs_id, packer_id, shipping_id]
    receivers = ["市场部 张总", "财务部 李总", "行政部 王主管", "研发部 周博士", "客户招待 - 茅台", "前台 接待用茶"]
    completed_outbounds = 0
    pending_outbounds = 0
    for i in range(10):
        order_no = database.gen_order_no(conn, "CKD", "outbound_order")
        days_ago = random.randint(0, 7)
        ts = (datetime.now() - timedelta(days=days_ago, hours=random.randint(0, 6))).isoformat(sep=" ", timespec="seconds")
        receiver = random.choice(receivers)
        if i < 7:  # 7 张已完成
            picker = random.choice(pickers)
            conn.execute(
                "INSERT INTO outbound_order (order_no, status, picker_id, created_at, completed_at, receiver_desc) "
                "VALUES (?, 'completed', ?, ?, ?, ?)",
                (order_no, picker, ts, ts, receiver),
            )
            completed_outbounds += 1
        else:
            conn.execute(
                "INSERT INTO outbound_order (order_no, status, created_at, receiver_desc) "
                "VALUES (?, 'pending', ?, ?)",
                (order_no, ts, receiver),
            )
            pending_outbounds += 1
        for _ in range(random.randint(1, 2)):
            inv = random.choice(inventory_rows)
            qty = max(1, random.randint(1, min(5, inv["on_hand"])))
            conn.execute(
                "INSERT INTO outbound_item (order_no, sku_id, batch_id, location_id, quantity) "
                "VALUES (?, ?, ?, ?, ?)",
                (order_no, inv["sku_id"], inv["batch_id"], inv["location_id"], qty),
            )
    print(f"  生成 {completed_outbounds} 张已完成出库 + {pending_outbounds} 张待拣")

    # === 调拨单 5 张（3 completed + 2 draft，多 creator_id） ===
    completed_transfers = 0
    draft_transfers = 0
    wh_ids = list(wh_map.values())
    if len(wh_ids) >= 2:
        creator_pool = [admin_id, manager_id, purchaser_id, putaway_id]
        for i in range(5):
            order_no = database.gen_order_no(conn, "DBD", "transfer_order")
            from_wh = wh_ids[0]
            to_wh = wh_ids[1]
            from_locs = conn.execute("SELECT id FROM location WHERE warehouse_id=?", (from_wh,)).fetchall()
            to_locs = conn.execute("SELECT id FROM location WHERE warehouse_id=?", (to_wh,)).fetchall()
            if not from_locs or not to_locs:
                continue
            from_loc = random.choice(from_locs)["id"]
            to_loc = random.choice(to_locs)["id"]
            creator = random.choice(creator_pool)
            days_ago = random.randint(0, 14)
            tdate = (date.today() - timedelta(days=days_ago)).isoformat()
            ts = (datetime.now() - timedelta(days=days_ago)).isoformat(sep=" ", timespec="seconds")
            if i < 3:
                conn.execute(
                    "INSERT INTO transfer_order (order_no, transfer_date, from_warehouse_id, from_location_id, "
                    "to_warehouse_id, to_location_id, status, creator_id, note, created_at, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
                    (order_no, tdate, from_wh, from_loc, to_wh, to_loc, creator, "分仓补货", ts, ts),
                )
                completed_transfers += 1
            else:
                conn.execute(
                    "INSERT INTO transfer_order (order_no, transfer_date, from_warehouse_id, from_location_id, "
                    "to_warehouse_id, to_location_id, status, creator_id, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
                    (order_no, tdate, from_wh, from_loc, to_wh, to_loc, creator, "草稿调拨", ts),
                )
                draft_transfers += 1
            sku = random.choice(sku_list)
            conn.execute(
                "INSERT INTO transfer_item (order_no, sku_id, quantity) VALUES (?, ?, ?)",
                (order_no, sku["id"], random.randint(5, 30)),
            )
    print(f"  生成 {completed_transfers} 张完成调拨 + {draft_transfers} 张草稿调拨")

    # === 已审批报损 5 条（多 approver_id） ===
    approved_damages = 0
    for inv in random.sample(list(inventory_rows), min(5, len(inventory_rows))):
        approver = random.choice([admin_id, manager_id])
        applicant = random.choice([receiver_id, putaway_id, packer_id])
        days_ago = random.randint(0, 5)
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat(sep=" ", timespec="seconds")
        conn.execute(
            "INSERT INTO damage_log (sku_id, batch_id, location_id, quantity, reason_type, reason_note, "
            "applicant_id, approver_id, status, created_at, approved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
            (inv["sku_id"], inv["batch_id"], inv["location_id"], random.randint(1, 3),
             random.choice(["broken", "expired", "lost"]), "已审批演示数据",
             applicant, approver, ts, ts),
        )
        approved_damages += 1
    print(f"  生成 {approved_damages} 条已审批报损")

    # === 盘点单 2 张（1 closed + 1 open） ===
    for i in range(2):
        order_no = database.gen_order_no(conn, "PD", "stocktake_order")
        status = "closed" if i == 0 else "open"
        creator = random.choice([admin_id, manager_id])
        days_ago = i * 14
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat(sep=" ", timespec="seconds")
        try:
            conn.execute(
                "INSERT INTO stocktake_order (order_no, scope, status, creator_id, created_at, closer_id, closed_at) "
                "VALUES (?, 'all', ?, ?, ?, ?, ?)",
                (order_no, status, creator,
                 ts,
                 creator if status == "closed" else None,
                 ts if status == "closed" else None),
            )
        except Exception:
            continue
    print(f"  生成 2 张盘点单（1 已盘 + 1 进行中）")

    # === v12: 给已完成的业务单据补 stock_log 流水（让流水页能看到调拨/报损/盘点）===
    _seed_log_diversity(conn, user_lookup)


def _seed_log_diversity(conn, user_lookup):
    """v12: 给已完成业务单据补对应的 stock_log 流水。

    - 已完成 transfer_order → 每张 2 条 stock_log（from -delta + to +delta）
    - 已审批 damage_log → 每条 1 条 stock_log（-quantity）
    - 已关闭 stocktake_order → 每张 2-3 条 stock_log（盘盈/盘亏调整）
    - 同时把原有 NULL operator_id 的 stock_log 分散给 11 个用户（团队工作量）
    """
    admin_id = user_lookup.get("admin")
    user_ids = [uid for uid in user_lookup.values() if uid]
    inventory_rows = conn.execute(
        "SELECT sku_id, batch_id, location_id, on_hand FROM inventory WHERE on_hand > 5"
    ).fetchall()
    if not inventory_rows:
        return

    # 1) 调拨流水
    transfers = conn.execute(
        "SELECT order_no, from_location_id, to_location_id, creator_id, completed_at "
        "FROM transfer_order WHERE status='completed'"
    ).fetchall()
    transfer_logs = 0
    for t in transfers:
        item = conn.execute(
            "SELECT sku_id, quantity FROM transfer_item WHERE order_no=? LIMIT 1",
            (t["order_no"],),
        ).fetchone()
        if not item:
            continue
        bid_row = conn.execute(
            "SELECT batch_id FROM inventory WHERE sku_id=? AND location_id=? LIMIT 1",
            (item["sku_id"], t["from_location_id"]),
        ).fetchone()
        if not bid_row:
            bid_row = conn.execute(
                "SELECT id AS batch_id FROM batch WHERE sku_id=? LIMIT 1", (item["sku_id"],)
            ).fetchone()
        if not bid_row:
            continue
        bid = bid_row["batch_id"]
        ts = t["completed_at"] or datetime.now().isoformat(sep=" ", timespec="seconds")
        conn.execute(
            "INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, occurred_at, operator_id, note) "
            "VALUES (?, ?, ?, ?, ?, 'transfer', ?, ?, '调拨出库')",
            (item["sku_id"], bid, t["from_location_id"], -item["quantity"], t["order_no"], ts, t["creator_id"]),
        )
        conn.execute(
            "INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, occurred_at, operator_id, note) "
            "VALUES (?, ?, ?, ?, ?, 'transfer', ?, ?, '调拨入库')",
            (item["sku_id"], bid, t["to_location_id"], item["quantity"], t["order_no"], ts, t["creator_id"]),
        )
        transfer_logs += 2

    # 2) 报损流水
    damages = conn.execute(
        "SELECT id, sku_id, batch_id, location_id, quantity, approver_id, approved_at "
        "FROM damage_log WHERE status='approved'"
    ).fetchall()
    damage_logs = 0
    for d in damages:
        ts = d["approved_at"] or datetime.now().isoformat(sep=" ", timespec="seconds")
        conn.execute(
            "INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, occurred_at, operator_id, note) "
            "VALUES (?, ?, ?, ?, ?, 'damage', ?, ?, '已审批报损')",
            (d["sku_id"], d["batch_id"], d["location_id"], -d["quantity"], f"BSD-{d['id']:04d}",
             ts, d["approver_id"] or admin_id),
        )
        damage_logs += 1

    # 3) 盘点流水（每张 closed stocktake 生成 2-3 条调整）
    stocktakes = conn.execute(
        "SELECT order_no, closer_id, closed_at FROM stocktake_order WHERE status='closed'"
    ).fetchall()
    adjust_logs = 0
    for st in stocktakes:
        ts = st["closed_at"] or datetime.now().isoformat(sep=" ", timespec="seconds")
        sample_n = min(3, len(inventory_rows))
        for inv in random.sample(list(inventory_rows), sample_n):
            delta = random.choice([-3, -2, -1, 1, 2])
            note = "盘亏调整" if delta < 0 else "盘盈调整"
            conn.execute(
                "INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, occurred_at, operator_id, note) "
                "VALUES (?, ?, ?, ?, ?, 'adjust', ?, ?, ?)",
                (inv["sku_id"], inv["batch_id"], inv["location_id"], delta,
                 st["order_no"], ts, st["closer_id"] or admin_id, note),
            )
            adjust_logs += 1

    # 4) 把原有 NULL operator_id 的 stock_log 分散给 11 个用户（让团队工作量统计有数据）
    null_ops = conn.execute("SELECT id FROM stock_log WHERE operator_id IS NULL").fetchall()
    for log in null_ops:
        conn.execute(
            "UPDATE stock_log SET operator_id=? WHERE id=?",
            (random.choice(user_ids), log["id"]),
        )

    print(f"  补 stock_log 流水：调拨 {transfer_logs} + 报损 {damage_logs} + 盘点 {adjust_logs} 条")
    print(f"  分散 {len(null_ops)} 条原 stock_log 的 operator_id 到 {len(user_ids)} 个用户")

    # 5) 故意制造低库存场景（5-6 个 SKU on_hand 调到 safety_stock 的 30%-60%）
    low_stock_targets = ["ENVELOPE-A4", "FOLDER-CLR", "NOTEBOOK-B5", "CUP-PAPER", "PRINT-INK", "TEA-LJ"]
    low_count = 0
    for code in low_stock_targets:
        sku = conn.execute("SELECT id, safety_stock FROM sku WHERE code=?", (code,)).fetchone()
        if not sku or sku["safety_stock"] <= 0:
            continue
        target_qty = max(1, int(sku["safety_stock"] * random.uniform(0.2, 0.6)))
        # 把该 SKU 所有 inventory 行的 on_hand 平均分配总量 = target_qty
        rows = conn.execute(
            "SELECT id FROM inventory WHERE sku_id=? ORDER BY id", (sku["id"],)
        ).fetchall()
        if not rows:
            continue
        per_row = max(1, target_qty // len(rows))
        for row in rows:
            conn.execute("UPDATE inventory SET on_hand=? WHERE id=?", (per_row, row["id"]))
        low_count += 1
    print(f"  强制制造 {low_count} 个 SKU 的低库存场景")


if __name__ == "__main__":
    reset()
