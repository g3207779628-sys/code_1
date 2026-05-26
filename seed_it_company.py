# -*- coding: utf-8 -*-
"""
一次性脚本：往现成的 warehouse.db 里新增一批「IT 公司」语境的轻量演示数据。

只 INSERT，绝不 UPDATE/DELETE 现有数据，绝不动 user 表。
字段名/约束以运行时 PRAGMA table_info 为准（schema 经过 20+ 次迁移）。

涉及 4 类新增数据 + 必要的前置依赖（warehouse / wh_type 若空则补）：
  1. wh_alloc_dept  分配部门
  2. wh_use_dept    使用部门
  3. sku            物品
  4. location       库位（owner_user_id 从现有 user 表真实 id 里挑）
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "warehouse.db"

# ---- 1. 分配部门（IT 公司语境，约 8 个）----
ALLOC_DEPTS = [
    ("研发部",     "负责产品与平台研发"),
    ("运维部",     "负责服务器与网络运维"),
    ("测试部",     "负责质量保障与测试"),
    ("产品部",     "负责产品规划与设计"),
    ("信息安全部", "负责安全合规与风控"),
    ("IT支持部",   "负责内部 IT 桌面支持"),
    ("行政部",     "负责行政与后勤"),
    ("人事部",     "负责招聘与人力资源"),
]

# ---- 2. 使用部门（IT 公司语境，约 8 个）----
USE_DEPTS = [
    ("研发部",     "终端设备与开发资源使用方"),
    ("运维部",     "服务器与网络设备使用方"),
    ("测试部",     "测试设备使用方"),
    ("产品部",     "办公设备使用方"),
    ("数据中心",   "机房资源使用方"),
    ("客户成功部", "演示与外勤设备使用方"),
    ("市场部",     "展会与办公设备使用方"),
    ("财务部",     "办公耗材使用方"),
]

# ---- 3. 物品 sku（约 20 个，IT 公司物资）----
# (code, name, unit, safety_stock, brand, category, category_major, usage_purpose, in_contract)
SKUS = [
    ("IT-NB-001",  "笔记本电脑",   "台", 10, "联想",       "笔记本",   "IT设备",   "研发与办公移动办公", 1),
    ("IT-PC-001",  "台式机",       "台", 8,  "戴尔",       "台式机",   "IT设备",   "固定工位办公",       1),
    ("IT-MON-001", "显示器",       "台", 15, "三星",       "外设",     "IT设备",   "工位显示扩展",       1),
    ("IT-KB-001",  "机械键盘",     "个", 20, "Cherry",     "外设",     "IT设备",   "研发输入设备",       0),
    ("IT-MS-001",  "鼠标",         "个", 25, "罗技",       "外设",     "IT设备",   "日常办公输入",       0),
    ("IT-SW-001",  "千兆交换机",   "台", 4,  "华为",       "网络设备", "网络设备", "局域网接入",         1),
    ("IT-RT-001",  "路由器",       "台", 4,  "华三",       "网络设备", "网络设备", "网络出口路由",       1),
    ("IT-SV-001",  "塔式服务器",   "台", 2,  "戴尔",       "服务器",   "IT设备",   "本地应用部署",       1),
    ("IT-SSD-001", "固态硬盘",     "块", 12, "三星",       "存储",     "IT设备",   "存储扩容与备件",     0),
    ("IT-RAM-001", "内存条",       "条", 16, "金士顿",     "存储",     "IT设备",   "内存升级备件",       0),
    ("IT-CBL-001", "网线",         "条", 50, "山泽",       "网络耗材", "网络设备", "综合布线",           0),
    ("IT-UPS-001", "UPS电源",      "台", 3,  "山特",       "电源设备", "IT设备",   "机房不间断供电",     1),
    ("IT-PRJ-001", "投影仪",       "台", 2,  "爱普生",     "外设",     "IT设备",   "会议室演示",         0),
    ("IT-PRT-001", "激光打印机",   "台", 3,  "惠普",       "打印设备", "办公耗材", "文档打印",           1),
    ("IT-TNR-001", "硒鼓",         "支", 10, "惠普",       "打印耗材", "办公耗材", "打印机耗材",         0),
    ("IT-SHR-001", "碎纸机",       "台", 2,  "得力",       "办公设备", "办公耗材", "保密文件销毁",       0),
    ("IT-A4-001",  "A4纸",         "包", 40, "得力",       "办公耗材", "办公耗材", "日常打印复印",       0),
    ("IT-BDG-001", "工牌",         "个", 60, "得力",       "办公耗材", "办公耗材", "员工身份标识",       0),
    ("IT-CARD-001","门禁卡",       "张", 80, "中控智慧",   "办公耗材", "办公耗材", "门禁出入授权",       0),
    ("IT-CAM-001", "网络摄像头",   "台", 6,  "海康威视",   "网络设备", "网络设备", "园区安防监控",       1),
]

# ---- 4. 库位 location（约 12 个）----
# (code, building, floor, alloc_dept_name, use_dept_name, wh_type_name, note)
# building/floor 填纯值（库里有清洗逻辑，别带"号楼/层"后缀）
LOCATIONS = [
    ("IT-A-01", "A座", "1", "IT支持部",   "研发部",     "机房",   "核心机房机柜区"),
    ("IT-A-02", "A座", "1", "运维部",     "数据中心",   "机房",   "服务器存放区"),
    ("IT-A-03", "A座", "2", "运维部",     "数据中心",   "机房",   "网络设备区"),
    ("IT-B-01", "B座", "3", "IT支持部",   "研发部",     "办公区", "研发终端暂存区"),
    ("IT-B-02", "B座", "3", "IT支持部",   "测试部",     "办公区", "测试设备暂存区"),
    ("IT-B-03", "B座", "4", "产品部",     "产品部",     "办公区", "产品部办公设备区"),
    ("IT-C-01", "C座", "1", "行政部",     "财务部",     "耗材库", "办公耗材主货架"),
    ("IT-C-02", "C座", "1", "行政部",     "市场部",     "耗材库", "打印耗材区"),
    ("IT-C-03", "C座", "2", "人事部",     "客户成功部", "耗材库", "工牌门禁卡存放区"),
    ("IT-D-01", "D座", "1", "信息安全部", "数据中心",   "机房",   "安全设备区"),
    ("IT-D-02", "D座", "2", "IT支持部",   "市场部",     "办公区", "演示与外勤设备区"),
    ("IT-D-03", "D座", "2", "运维部",     "客户成功部", "办公区", "备件周转区"),
]

# 若 wh_type 为空时需要补充的类型
WH_TYPE_SEED = ["机房", "办公区", "耗材库"]

# 若 warehouse 为空时需要补充的 IT 公司仓库
WH_SEED = ("IT资产仓", "公司总部园区 IT 资产仓库")


def main():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # ---- 前置：确保有 warehouse ----
    wh_row = cur.execute("SELECT id FROM warehouse ORDER BY id LIMIT 1").fetchone()
    if wh_row is None:
        cur.execute("INSERT OR IGNORE INTO warehouse (name, address) VALUES (?, ?)", WH_SEED)
        wh_row = cur.execute(
            "SELECT id FROM warehouse WHERE name = ?", (WH_SEED[0],)
        ).fetchone()
    warehouse_id = wh_row["id"]

    # ---- 前置：确保有 wh_type ----
    if cur.execute("SELECT 1 FROM wh_type LIMIT 1").fetchone() is None:
        for name in WH_TYPE_SEED:
            cur.execute("INSERT OR IGNORE INTO wh_type (name) VALUES (?)", (name,))
    # 也补齐 location 需要引用、但库里可能还没有的类型
    for name in WH_TYPE_SEED:
        cur.execute("INSERT OR IGNORE INTO wh_type (name) VALUES (?)", (name,))

    # ---- 1. 分配部门 ----
    for name, note in ALLOC_DEPTS:
        cur.execute(
            "INSERT OR IGNORE INTO wh_alloc_dept (name, note) VALUES (?, ?)", (name, note)
        )

    # ---- 2. 使用部门 ----
    for name, note in USE_DEPTS:
        cur.execute(
            "INSERT OR IGNORE INTO wh_use_dept (name, note) VALUES (?, ?)", (name, note)
        )

    # ---- 3. 物品 sku ----
    for (code, name, unit, ss, brand, category, category_major,
         usage_purpose, in_contract) in SKUS:
        cur.execute(
            """INSERT OR IGNORE INTO sku
               (code, name, unit, safety_stock, brand, category, category_major,
                usage_purpose, in_contract)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, name, unit, ss, brand, category, category_major,
             usage_purpose, in_contract),
        )

    # ---- 解析部门 / 类型 name -> id（含已存在的同名行）----
    alloc_map = {r["name"]: r["id"]
                 for r in cur.execute("SELECT id, name FROM wh_alloc_dept").fetchall()}
    use_map = {r["name"]: r["id"]
               for r in cur.execute("SELECT id, name FROM wh_use_dept").fetchall()}
    type_map = {r["name"]: r["id"]
                for r in cur.execute("SELECT id, name FROM wh_type").fetchall()}

    # ---- 库位责任人：从现有 user 表真实 id 里挑（轮流分配，绝不动 user 表）----
    user_ids = [r["id"] for r in
                cur.execute("SELECT id FROM user ORDER BY id").fetchall()]
    # 只挑有 position 的真实员工（排除 username 为 a/z/js 这类占位），保证语义合理
    staff_ids = [r["id"] for r in cur.execute(
        "SELECT id FROM user WHERE position IS NOT NULL AND position != '' ORDER BY id"
    ).fetchall()]
    owner_pool = staff_ids if staff_ids else user_ids

    used_owner_ids = set()

    # ---- 4. 库位 location ----
    for i, (code, building, floor, alloc_name, use_name, type_name, note) in enumerate(LOCATIONS):
        owner_id = owner_pool[i % len(owner_pool)]
        used_owner_ids.add(owner_id)
        cur.execute(
            """INSERT OR IGNORE INTO location
               (warehouse_id, code, building, floor,
                alloc_dept_id, use_dept_id, wh_type_id, owner_user_id, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (warehouse_id, code, building, floor,
             alloc_map.get(alloc_name), use_map.get(use_name),
             type_map.get(type_name), owner_id, note),
        )

    conn.commit()

    # ---- 验证 ----
    print("=== 插入后统计 ===")
    print("使用的 warehouse_id =", warehouse_id)
    print("库位责任人引用的现有 user id:", sorted(used_owner_ids))
    for t in ("wh_alloc_dept", "wh_use_dept", "sku", "location", "wh_type", "warehouse"):
        c = cur.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        print(f"  {t}: {c} 行")

    print("\n=== 各表前 3 行样例 ===")
    print("[wh_alloc_dept]")
    for r in cur.execute("SELECT id, name FROM wh_alloc_dept ORDER BY id DESC LIMIT 3"):
        print("  ", r["id"], r["name"])
    print("[wh_use_dept]")
    for r in cur.execute("SELECT id, name FROM wh_use_dept ORDER BY id DESC LIMIT 3"):
        print("  ", r["id"], r["name"])
    print("[sku] (本次新增)")
    for r in cur.execute(
        "SELECT code, name, unit, brand, category_major FROM sku WHERE code LIKE 'IT-%' ORDER BY code LIMIT 3"
    ):
        print("  ", r["code"], r["name"], r["unit"], r["brand"], r["category_major"])
    print("[location] (本次新增)")
    for r in cur.execute(
        "SELECT code, building, floor, owner_user_id, note FROM location WHERE code LIKE 'IT-%' ORDER BY code LIMIT 3"
    ):
        print("  ", r["code"], r["building"], r["floor"], "owner=", r["owner_user_id"], r["note"])

    conn.close()


if __name__ == "__main__":
    main()
