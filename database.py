import sqlite3
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).parent / "warehouse.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def gen_order_no(conn, prefix, table):
    today = datetime.now().strftime("%Y%m%d")
    like_pattern = f"{prefix}{today}%"
    row = conn.execute(
        f"SELECT order_no FROM {table} WHERE order_no LIKE ? ORDER BY order_no DESC LIMIT 1",
        (like_pattern,),
    ).fetchone()
    if row:
        last_seq = int(row["order_no"][-3:])
        return f"{prefix}{today}{last_seq + 1:03d}"
    return f"{prefix}{today}001"


SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'staff')),
    display_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sku (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    spec TEXT,
    unit TEXT DEFAULT '份',
    storage_zone TEXT NOT NULL CHECK(storage_zone IN ('1', '2', '3', '4')),
    safety_stock INTEGER DEFAULT 0,
    cost_price REAL DEFAULT 0,
    sale_price REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- v13: 商品扩展字段
    owner_party_id INTEGER REFERENCES owner_party(id),     -- 物资所属方
    owner_unit_id INTEGER REFERENCES owner_unit(id),       -- 物资所属单位
    brand TEXT,                                            -- 品牌
    category TEXT,                                         -- 物品类别
    category_major TEXT,                                   -- 物品大类
    usage_purpose TEXT,                                    -- 用途
    in_contract INTEGER DEFAULT 0                          -- 是否在合同内（0/1）
);

CREATE TABLE IF NOT EXISTS owner_party (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS owner_unit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- v14: 6 张同构主数据表（generic admin 复用同一模板）
CREATE TABLE IF NOT EXISTS owner_admin (         -- 物品管理方
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS item_category (        -- 物品类别
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS item_category_major (  -- 物品大类
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wh_alloc_dept (        -- 仓库分配部门
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wh_use_dept (          -- 仓库使用部门
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wh_type (              -- 仓库类型
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wh_owner (             -- 责任人（v23: 独立轻量主数据，不再复用 user 表）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- v14 方案 B: 楼栋 / 楼层 作为 location 表的字段（不独立建表，见 _migrate 第 18 步）

CREATE TABLE IF NOT EXISTS warehouse (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    address TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS location (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
    code TEXT NOT NULL,
    zone_type TEXT NOT NULL CHECK(zone_type IN ('normal', 'cold', 'freeze', 'isolation', 'downgrade')),
    UNIQUE(warehouse_id, code)
);

CREATE TABLE IF NOT EXISTS customer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    name TEXT,
    default_address TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT UNIQUE NOT NULL,
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    inbound_order_no TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    location_id INTEGER NOT NULL REFERENCES location(id),
    batch_id INTEGER NOT NULL REFERENCES batch(id),
    on_hand INTEGER NOT NULL DEFAULT 0,
    reserved INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(sku_id, location_id, batch_id)
);

CREATE TABLE IF NOT EXISTS stock_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    batch_id INTEGER NOT NULL REFERENCES batch(id),
    location_id INTEGER NOT NULL REFERENCES location(id),
    delta INTEGER NOT NULL,
    source_doc TEXT,
    event_type TEXT NOT NULL CHECK(event_type IN ('inbound','outbound','damage','adjust','transfer','return_in')),
    operator_id INTEGER REFERENCES user(id),
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    note TEXT
);

CREATE TABLE IF NOT EXISTS inbound_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected')),
    creator_id INTEGER NOT NULL REFERENCES user(id),
    approver_id INTEGER REFERENCES user(id),
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    approved_at DATETIME
);

CREATE TABLE IF NOT EXISTS inbound_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL REFERENCES inbound_order(order_no),
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    batch_no TEXT NOT NULL,
    location_id INTEGER NOT NULL REFERENCES location(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL DEFAULT 0,
    batch_id INTEGER REFERENCES batch(id)
);

CREATE TABLE IF NOT EXISTS sales_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    channel TEXT NOT NULL DEFAULT 'manual' CHECK(channel IN ('manual','tmall','jd','douyin','applet')),
    platform_order_no TEXT,
    customer_id INTEGER REFERENCES customer(id),
    total_amount REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','reserved','completed','cancelled')),
    receiver_name TEXT,
    receiver_phone TEXT,
    receiver_addr TEXT,
    creator_id INTEGER REFERENCES user(id),
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales_order_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL REFERENCES sales_order(order_no),
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS outbound_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    sales_order_no TEXT NOT NULL REFERENCES sales_order(order_no),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled')),
    picker_id INTEGER REFERENCES user(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

CREATE TABLE IF NOT EXISTS outbound_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL REFERENCES outbound_order(order_no),
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    batch_id INTEGER NOT NULL REFERENCES batch(id),
    location_id INTEGER NOT NULL REFERENCES location(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0)
);

CREATE TABLE IF NOT EXISTS damage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    batch_id INTEGER NOT NULL REFERENCES batch(id),
    location_id INTEGER NOT NULL REFERENCES location(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    reason_type TEXT NOT NULL CHECK(reason_type IN ('broken','expired','lost','stolen','other')),
    reason_note TEXT,
    photo_note TEXT,
    applicant_id INTEGER NOT NULL REFERENCES user(id),
    approver_id INTEGER REFERENCES user(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    approved_at DATETIME
);

CREATE TABLE IF NOT EXISTS stocktake_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    scope TEXT NOT NULL DEFAULT 'all',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed','cancelled')),
    creator_id INTEGER NOT NULL REFERENCES user(id),
    closer_id INTEGER REFERENCES user(id),
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at DATETIME
);

CREATE TABLE IF NOT EXISTS stocktake_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL REFERENCES stocktake_order(order_no),
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    batch_id INTEGER NOT NULL REFERENCES batch(id),
    location_id INTEGER NOT NULL REFERENCES location(id),
    expected_qty INTEGER NOT NULL,
    actual_qty INTEGER,
    diff_handled INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS return_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    sales_order_no TEXT NOT NULL REFERENCES sales_order(order_no),
    customer_id INTEGER REFERENCES customer(id),
    reason TEXT CHECK(reason IN ('quality','wrong','dislike','damaged_in_transit','other')),
    reason_note TEXT,
    status TEXT NOT NULL DEFAULT 'approved' CHECK(status IN ('approved','received','qc_done','refunded','rejected')),
    refund_amount REAL DEFAULT 0,
    cs_user_id INTEGER REFERENCES user(id),
    qc_user_id INTEGER REFERENCES user(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    received_at DATETIME,
    qc_at DATETIME,
    refunded_at DATETIME,
    note TEXT
);

CREATE TABLE IF NOT EXISTS return_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL REFERENCES return_order(order_no),
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    batch_id INTEGER NOT NULL REFERENCES batch(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    qc_result TEXT CHECK(qc_result IN ('good','downgrade','damaged')),
    return_location_id INTEGER REFERENCES location(id),
    unit_price REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS menu_permission (
    user_id INTEGER NOT NULL REFERENCES user(id),
    menu_key TEXT NOT NULL,
    PRIMARY KEY(user_id, menu_key)
);

CREATE TABLE IF NOT EXISTS role_preset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    menu_keys TEXT NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notification_channel (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    config_json TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    channel_codes TEXT,
    recipient_user_ids TEXT,
    threshold_json TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type TEXT NOT NULL,
    payload_json TEXT,
    triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    notified_channels TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS inventory_snapshot (
    snapshot_date DATE NOT NULL,
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    on_hand INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(snapshot_date, sku_id)
);

CREATE TABLE IF NOT EXISTS position (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- v16: storage_area 表已砸（连同字段/路由/模板），不再 SCHEMA 创建
-- 老 db 的兼容清理见 _migrate 第 21 步

CREATE TABLE IF NOT EXISTS transfer_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    transfer_date DATE NOT NULL,
    from_warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
    from_location_id INTEGER NOT NULL REFERENCES location(id),
    to_warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
    to_location_id INTEGER NOT NULL REFERENCES location(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','completed','cancelled')),
    creator_id INTEGER REFERENCES user(id),
    note TEXT,
    attachments TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

CREATE TABLE IF NOT EXISTS transfer_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL REFERENCES transfer_order(order_no),
    sku_id INTEGER NOT NULL REFERENCES sku(id),
    batch_id INTEGER REFERENCES batch(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0)
);

-- 领用申请单（扫码免登录门户提交，管理员后台查看）
CREATE TABLE IF NOT EXISTS requisition_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,                -- LYD+日期+序号
    applicant TEXT NOT NULL,                      -- 申请人（手填文本）
    dept_id INTEGER REFERENCES wh_use_dept(id),   -- 所属部门（使用部门），可空
    dept_name TEXT,                               -- 部门名快照
    location_id INTEGER REFERENCES location(id),  -- 库位，可空
    location_code TEXT,                           -- 库位编码快照
    category_id INTEGER REFERENCES item_category_major(id),  -- 领用类别（物品大类），可空
    category_name TEXT,                           -- 类别名快照
    apply_date DATE,                              -- 申请日期
    note TEXT,                                    -- 申领说明
    status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','handled','cancelled','rejected')),
    source TEXT DEFAULT 'portal',                 -- 来源：portal=扫码门户
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS requisition_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL REFERENCES requisition_order(order_no),
    sku_id INTEGER REFERENCES sku(id),            -- 可空（容错：示例数据无真实 id）
    sku_code TEXT,                                -- 编码快照
    sku_name TEXT,                                -- 名称快照
    unit TEXT,
    quantity INTEGER NOT NULL CHECK(quantity > 0)
);
"""


POSITIONS = {
    "warehouse_manager": "仓库经理",
    "inventory_ctrl":    "库存控制员",
    "purchaser":         "采购员",
    "receiver":          "收货员",
    "putaway":           "上架员",
    "cs":                "客服",
    "picker":            "出库员",
    "packer":            "打包员",
    "shipping":          "出货协调员",
    "stocktaker":        "盘点员",
}

# (username, password, role, display_name, position)
SEED_USERS = [
    ("admin",       "admin123",   "admin",   "张经理", "warehouse_manager"),
    ("manager",     "manager123", "manager", "陈姐",   "inventory_ctrl"),
    ("staff",       "staff123",   "staff",   "张三",   "picker"),
    ("purchaser1",  "demo123",    "manager", "林师傅", "purchaser"),
    ("receiver1",   "demo123",    "staff",   "赵师傅", "receiver"),
    ("putaway1",    "demo123",    "staff",   "钱师傅", "putaway"),
    ("cs1",         "demo123",    "staff",   "孙姐",   "cs"),
    ("qc1",         "demo123",    "staff",   "周师傅", "picker"),
    ("packer1",     "demo123",    "staff",   "王师傅", "packer"),
    ("shipping1",   "demo123",    "staff",   "徐姐",   "shipping"),
    ("stocktaker1", "demo123",    "staff",   "郑老师", "stocktaker"),
]

SEED_WAREHOUSE = ("主仓", "杭州市西湖区文三路 100 号")

# (存放区域, 存放位置)
SEED_LOCATIONS = [
    ("A区", "01-01"),
    ("A区", "01-02"),
    ("A区", "02-01"),
    ("B区", "01-01"),
    ("B区", "01-02"),
    ("C区", "01-01"),
    ("隔离区", "ISO-01"),
    ("降级区", "DG-01"),
]

SEED_SKUS = [
    # 原 5 个（酒柜 + 茶水 + 基础办公）
    ("MT-FW-500",  "茅台",       "53°飞天 500ml",         "瓶",  6,  0, 0),
    ("WLY-PJ-500", "五粮液",     "52°普五 500ml",         "瓶",  6,  0, 0),
    ("A4-COPY",    "A4 复印纸",  "70g 500 张/包",         "包",  20, 0, 0),
    ("PEN-SIGN",   "签字笔",     "黑色 0.5mm 12 支/盒",   "盒",  30, 0, 0),
    ("TEA-LJ",     "龙井茶",     "明前 250g/罐",          "罐",  8,  0, 0),
    # v12 新增 15 个办公用品 / 招待用品 — 含部分高 safety_stock 故意低库存
    ("STAPLER",     "订书机",     "标准 24/6 型",            "把", 5,  0, 0),
    ("ENVELOPE-A4", "A4 信封",    "牛皮纸 100 个/包",         "包", 40, 0, 0),  # 高阈值，易低库存
    ("FOLDER-CLR",  "文件夹",     "透明 A4 单页",            "个", 50, 0, 0),  # 高阈值
    ("STICKY-NOTE", "便利贴",     "76×76mm 100 张/本",       "本", 25, 0, 0),
    ("NOTEBOOK-B5", "笔记本",     "B5 横线 80 页",           "本", 30, 0, 0),  # 高阈值
    ("PEN-BALL",    "圆珠笔",     "蓝色 0.7mm 10 支/盒",     "盒", 20, 0, 0),
    ("CALC-12",     "计算器",     "12 位标准型",             "台", 3,  0, 0),
    ("TAPE-CLR",    "透明胶带",   "宽 18mm 长 30m",          "卷", 25, 0, 0),
    ("CLIP-PAPER",  "回形针",     "28mm 100 枚/盒",          "盒", 15, 0, 0),
    ("WATER-555",   "矿泉水",     "555ml × 24 瓶/箱",        "箱", 8,  0, 0),
    ("COFFEE-BEAN", "咖啡豆",     "蓝山 250g/袋",            "袋", 6,  0, 0),
    ("CUP-PAPER",   "纸杯",       "9 盎司 50 个/包",         "包", 35, 0, 0),  # 高阈值
    ("BATTERY-AA",  "AA 电池",    "5 号 4 节/卡",            "卡", 10, 0, 0),
    ("PRINT-INK",   "打印机墨盒", "HP 黑色通用",             "支", 4,  0, 0),
    ("HIGHLIGHTER", "荧光笔",     "黄色 10 支/盒",           "盒", 12, 0, 0),
]

SEED_CUSTOMERS = [
    ("13900001111", "张三", "杭州市西湖区文三路 1 号"),
    ("13900002222", "李四", "杭州市余杭区未来科技城 88 号"),
]

# v23: 责任人主数据（IT 公司语境，独立于 user 表）。(name, note)
SEED_WH_OWNERS = [
    ("王伟",   "IT 运维负责人"),
    ("李娜",   "资产管理员"),
    ("张强",   "机房管理员"),
    ("刘洋",   "网络设备负责人"),
    ("陈静",   "终端设备负责人"),
    ("杨帆",   "服务器责任人"),
    ("赵磊",   "耗材库管"),
    ("孙琳",   "备件库责任人"),
    ("周杰",   "采购对接人"),
    ("吴敏",   "信息安全负责人"),
]


# 各岗位默认菜单权限（admin 角色直接拿全部，不走这张表）。
# password 菜单由 app.py 自动追加，这里不写。
DEFAULT_PERMISSIONS_BY_POSITION = {
    "warehouse_manager": ["report", "pending", "inbound", "pick",
                          "inventory", "transfer", "storage", "warehouse", "location",
                          "damage", "stocktake", "log", "forecast",
                          "sku", "user_admin", "channel_config", "backup"],
    "inventory_ctrl":    ["report", "pending", "inbound", "pick",
                          "inventory", "transfer", "storage", "warehouse", "location",
                          "damage", "stocktake", "log", "forecast",
                          "sku", "user_admin", "channel_config"],
    "purchaser":         ["report", "inbound", "inventory", "log",
                          "forecast", "sku"],
    "receiver":          ["inbound", "inventory", "log"],
    "putaway":           ["inbound", "inventory", "log"],
    "cs":                ["pick", "sku"],
    "picker":            ["pick", "inventory", "log"],
    "packer":            ["pick"],
    "shipping":          ["pick"],
    "stocktaker":        ["stocktake", "inventory", "log"],
}

# 4 个内置通知渠道（admin 在 UI 上启用/填凭证；短信/邮件按要求"只留接口"）
# webhook = 通用入口，飞书 / 钉钉 / 企微群机器人 / 自建系统全都走它
SEED_CHANNELS = [
    ("inapp",        "站内通知", 1, "{}"),
    ("email",        "邮件 SMTP", 0, "{}"),
    ("sms",          "短信（阿里云）", 0, "{}"),
    ("webhook",      "其他方式（飞书 / 钉钉 / 自定义 Webhook）", 0, "{}"),
]

# 4 条默认预警规则
SEED_ALERT_RULES = [
    ("low_stock",      1, "inapp", ""),
    ("damage_pending", 1, "inapp", ""),
]

# 4 个默认角色预设（admin 可在 UI 上增删改）
SEED_ROLE_PRESETS = [
    ("warehouse_manager", "仓库经理", "查看所有业务 + 审批权",
     "pending,inbound,pick,transfer,inventory,damage,stocktake,log,forecast,sku,channel_config"),
    ("purchaser",         "采购员",   "入库 + 商品",
     "inbound,inventory,sku"),
    ("outbound_staff",    "出库员",   "仅出库 + 库存查询",
     "pick,inventory"),
    ("normal_staff",      "普通员工", "仅修改密码",
     ""),
]


def init_db():
    from pathlib import Path
    wiped_marker = Path(__file__).parent / ".wiped"
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _seed_users(conn)
        # v13: wipe_all_data.py 跑过后会创建 .wiped 文件，跳过主数据 SEED，让用户自己新导入
        if not wiped_marker.exists():
            _seed_warehouse_and_locations(conn)
            _seed_skus(conn)
        _seed_customers(conn)
        _seed_wh_owners(conn)
        _seed_default_permissions(conn)
        _seed_notification_channels(conn)
        _seed_alert_rules(conn)
        _seed_role_presets(conn)


def _seed_role_presets(conn):
    for code, name, desc, keys in SEED_ROLE_PRESETS:
        conn.execute(
            "INSERT OR IGNORE INTO role_preset (code, name, description, menu_keys) VALUES (?, ?, ?, ?)",
            (code, name, desc, keys),
        )


def _seed_default_permissions(conn):
    """为尚无任何权限记录的用户，按 position 写入默认菜单 key。"""
    for u in conn.execute("SELECT id, position FROM user").fetchall():
        has_perm = conn.execute(
            "SELECT 1 FROM menu_permission WHERE user_id = ? LIMIT 1", (u["id"],)
        ).fetchone()
        if has_perm:
            continue
        keys = DEFAULT_PERMISSIONS_BY_POSITION.get(u["position"] or "", [])
        for mk in keys:
            conn.execute(
                "INSERT OR IGNORE INTO menu_permission (user_id, menu_key) VALUES (?, ?)",
                (u["id"], mk),
            )


def _seed_notification_channels(conn):
    for code, name, enabled, cfg in SEED_CHANNELS:
        conn.execute(
            "INSERT OR IGNORE INTO notification_channel (code, name, enabled, config_json) VALUES (?, ?, ?, ?)",
            (code, name, enabled, cfg),
        )


def _seed_alert_rules(conn):
    has_any = conn.execute("SELECT 1 FROM alert_rule LIMIT 1").fetchone()
    if has_any:
        return
    for rule_type, enabled, channels, recipients in SEED_ALERT_RULES:
        conn.execute(
            "INSERT INTO alert_rule (rule_type, enabled, channel_codes, recipient_user_ids) VALUES (?, ?, ?, ?)",
            (rule_type, enabled, channels, recipients),
        )


def _migrate(conn):
    """schema 迁移：ADD COLUMN + 必要的重建表（用于改 CHECK 约束）。"""
    # 1. user 表加 position 字段（历史迁移，保留）
    user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(user)").fetchall()]
    if "position" not in user_cols:
        conn.execute("ALTER TABLE user ADD COLUMN position TEXT")

    # 2. user 表加 must_change_password 字段（首次登录强制改密标记）
    user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(user)").fetchall()]
    if "must_change_password" not in user_cols:
        conn.execute("ALTER TABLE user ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")

    # 3. sku.storage_zone 从 normal/cold/freeze 迁到 1/2/3/4 区
    #    SQLite 不支持改 CHECK，需要重建表
    sku_def = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sku'"
    ).fetchone()
    if sku_def and "'normal'" in sku_def["sql"]:
        conn.execute("PRAGMA foreign_keys = OFF")
        # legacy_alter_table=ON 阻止 sqlite 把其他表的 REFERENCES sku 自动改成 REFERENCES sku_old
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.executescript("""
                ALTER TABLE sku RENAME TO sku_old;
                CREATE TABLE sku (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    spec TEXT,
                    unit TEXT DEFAULT '份',
                    storage_zone TEXT NOT NULL CHECK(storage_zone IN ('1', '2', '3', '4')),
                    safety_stock INTEGER DEFAULT 0,
                    cost_price REAL DEFAULT 0,
                    sale_price REAL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO sku (id, code, name, spec, unit, storage_zone,
                                  safety_stock, cost_price, sale_price, created_at)
                SELECT id, code, name, spec, unit,
                    CASE storage_zone
                        WHEN 'normal' THEN '1'
                        WHEN 'cold'   THEN '2'
                        WHEN 'freeze' THEN '3'
                        ELSE '1'
                    END,
                    safety_stock, cost_price, sale_price, created_at
                FROM sku_old;
                DROP TABLE sku_old;
            """)
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

    # 4. 修复旧迁移留下的孤儿 FK（早期 _migrate 未关 legacy_alter_table，
    #    导致其他表的 REFERENCES sku 被改成 REFERENCES "sku_old"，
    #    sku_old 被 DROP 后这些 FK 指向不存在的表）
    broken = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%sku_old%'"
    ).fetchall()
    if broken:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = REPLACE(REPLACE(sql, '\"sku_old\"', 'sku'), 'sku_old', 'sku') "
            "WHERE sql LIKE '%sku_old%'"
        )
        conn.execute("PRAGMA writable_schema = OFF")
        conn.execute("PRAGMA foreign_keys = ON")

    # 4b. requisition_order status 增加 'rejected'（驳回）—— 直接改 CHECK 定义文本，不动数据
    ro = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='requisition_order'"
    ).fetchone()
    if ro and "'rejected'" not in (ro["sql"] or ""):
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = "
            "REPLACE(sql, \"'submitted','handled','cancelled'\", "
            "\"'submitted','handled','cancelled','rejected'\") "
            "WHERE type='table' AND name='requisition_order'"
        )
        conn.execute("PRAGMA writable_schema = OFF")

    # 5. user 表加联系字段（v2: 勾选用户后真发到该人）
    user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(user)").fetchall()]
    for col in ("email", "phone", "wechat_userid"):
        if col not in user_cols:
            conn.execute(f"ALTER TABLE user ADD COLUMN {col} TEXT")

    # 5b. v2 砍掉 QC 岗位：把现有 position='qc' 的用户转为 picker
    conn.execute("UPDATE user SET position='picker' WHERE position='qc'")

    # 6. outbound_order 加 receiver_desc / note；sales_order_no 改 nullable（重建表）
    ob_cols_meta = conn.execute("PRAGMA table_info(outbound_order)").fetchall()
    ob_cols = [r["name"] for r in ob_cols_meta]
    if "receiver_desc" not in ob_cols:
        conn.execute("ALTER TABLE outbound_order ADD COLUMN receiver_desc TEXT")
    if "note" not in ob_cols:
        conn.execute("ALTER TABLE outbound_order ADD COLUMN note TEXT")
    # sales_order_no NOT NULL → nullable（手动建出库单不绑销售单）
    # 重新查一次（前两步可能改了 schema）
    ob_cols_meta2 = conn.execute("PRAGMA table_info(outbound_order)").fetchall()
    so_no_notnull = any(r["name"] == "sales_order_no" and r["notnull"] == 1 for r in ob_cols_meta2)
    if so_no_notnull:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.executescript("""
                ALTER TABLE outbound_order RENAME TO outbound_order_old;
                CREATE TABLE outbound_order (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_no TEXT UNIQUE NOT NULL,
                    sales_order_no TEXT REFERENCES sales_order(order_no),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled')),
                    picker_id INTEGER REFERENCES user(id),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    receiver_desc TEXT,
                    note TEXT
                );
                INSERT INTO outbound_order (id, order_no, sales_order_no, status, picker_id,
                                             created_at, completed_at, receiver_desc, note)
                SELECT id, order_no, sales_order_no, status, picker_id,
                       created_at, completed_at, receiver_desc, note FROM outbound_order_old;
                DROP TABLE outbound_order_old;
            """)
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

    # 7. outbound_item 加 reversed_qty
    oi_cols = [r["name"] for r in conn.execute("PRAGMA table_info(outbound_item)").fetchall()]
    if "reversed_qty" not in oi_cols:
        conn.execute("ALTER TABLE outbound_item ADD COLUMN reversed_qty INTEGER NOT NULL DEFAULT 0")

    # 7b. 修复 outbound_item.order_no 的孤儿 FK（v2 重建 outbound_order 时 legacy_alter_table=ON
    #     导致 outbound_item 的 FK 仍引用 "outbound_order_old"，该表已被 DROP）
    oi_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE name='outbound_item'").fetchone()
    if oi_sql_row and "outbound_order_old" in (oi_sql_row["sql"] or ""):
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = REPLACE(REPLACE(sql, '\"outbound_order_old\"', 'outbound_order'), 'outbound_order_old', 'outbound_order') "
            "WHERE name='outbound_item'"
        )
        conn.execute("PRAGMA writable_schema = OFF")
        conn.execute("PRAGMA foreign_keys = ON")

    # 8. wps 渠道数据迁移到 dingtalk/feishu/webhook（按 format 字段分流）
    import json as _json
    wps_row = conn.execute(
        "SELECT enabled, config_json FROM notification_channel WHERE code='wps'"
    ).fetchone()
    if wps_row:
        try:
            wps_cfg = _json.loads(wps_row["config_json"] or "{}")
        except _json.JSONDecodeError:
            wps_cfg = {}
        fmt = (wps_cfg.get("format") or "markdown").lower()
        target = {"feishu": "feishu", "markdown": "dingtalk", "text": "dingtalk"}.get(fmt, "webhook")
        # 把 wps 配置（包含 webhook_url）搬到目标渠道（如果目标没启用）
        existing = conn.execute(
            "SELECT enabled FROM notification_channel WHERE code=?", (target,)
        ).fetchone()
        if existing and not existing["enabled"] and wps_cfg.get("webhook_url"):
            new_cfg = {"webhook_url": wps_cfg["webhook_url"]}
            conn.execute(
                "UPDATE notification_channel SET enabled=?, config_json=? WHERE code=?",
                (wps_row["enabled"], _json.dumps(new_cfg, ensure_ascii=False), target),
            )
        # 删 wps 行 + 删 alert_rule 里 channel_codes 含 'wps' 的引用（替换为 target）
        conn.execute("DELETE FROM notification_channel WHERE code='wps'")
        for r in conn.execute(
            "SELECT id, channel_codes FROM alert_rule WHERE channel_codes LIKE '%wps%'"
        ).fetchall():
            codes = [c if c != "wps" else target for c in (r["channel_codes"] or "").split(",")]
            conn.execute("UPDATE alert_rule SET channel_codes=? WHERE id=?",
                         (",".join(codes), r["id"]))

    # 9. v3: 删除 sku.storage_zone 字段（重建表）
    sku_cols = [r["name"] for r in conn.execute("PRAGMA table_info(sku)").fetchall()]
    if "storage_zone" in sku_cols:
        conn.commit()  # 把之前的迁移落盘，避免事务里 PRAGMA 失效
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute("ALTER TABLE sku RENAME TO sku_old_v3")
            conn.execute("""CREATE TABLE sku (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                spec TEXT,
                unit TEXT DEFAULT '份',
                safety_stock INTEGER DEFAULT 0,
                cost_price REAL DEFAULT 0,
                sale_price REAL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""INSERT INTO sku (id, code, name, spec, unit, safety_stock, cost_price, sale_price, created_at)
                SELECT id, code, name, spec, unit, safety_stock, cost_price, sale_price, created_at FROM sku_old_v3""")
            conn.execute("DROP TABLE sku_old_v3")
            # 修复其他表对 sku_old_v3 的孤儿 FK 引用（legacy_alter_table=ON 时 RENAME 不会改 FK，但保险起见）
            conn.execute("PRAGMA writable_schema = ON")
            conn.execute(
                "UPDATE sqlite_master SET sql = REPLACE(REPLACE(sql, '\"sku_old_v3\"', 'sku'), 'sku_old_v3', 'sku') "
                "WHERE sql LIKE '%sku_old_v3%'"
            )
            conn.execute("PRAGMA writable_schema = OFF")
            conn.commit()
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

    # 10. v3: inventory_snapshot 重建（删 storage_zone 列）
    # 强制 schema cache 刷新（避免 step 9 后残留的 sku_old_v3 引用幻影）
    conn.execute("PRAGMA writable_schema = 1")
    conn.execute("PRAGMA writable_schema = 0")
    snap_cols = [r["name"] for r in conn.execute("PRAGMA table_info(inventory_snapshot)").fetchall()]
    if "storage_zone" in snap_cols:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute("ALTER TABLE inventory_snapshot RENAME TO inventory_snapshot_old")
            conn.execute("""CREATE TABLE inventory_snapshot (
                snapshot_date DATE NOT NULL,
                sku_id INTEGER NOT NULL REFERENCES sku(id),
                on_hand INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(snapshot_date, sku_id)
            )""")
            conn.execute("""INSERT OR REPLACE INTO inventory_snapshot (snapshot_date, sku_id, on_hand)
                SELECT snapshot_date, sku_id, SUM(on_hand) FROM inventory_snapshot_old
                GROUP BY snapshot_date, sku_id""")
            conn.execute("DROP TABLE inventory_snapshot_old")
            conn.commit()
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

    # 11. v3: SEED POSITIONS 字典到 position 表（首次启动）
    has_pos = conn.execute("SELECT 1 FROM position LIMIT 1").fetchone()
    if not has_pos:
        for code, label in POSITIONS.items():
            conn.execute(
                "INSERT OR IGNORE INTO position (code, label) VALUES (?, ?)",
                (code, label),
            )

    # 12. v4: 砍临期相关 — 删 expiry_7d / expiry_30d 规则和历史事件
    conn.execute("DELETE FROM alert_rule WHERE rule_type IN ('expiry_7d', 'expiry_30d')")
    conn.execute("DELETE FROM alert_event WHERE rule_type IN ('expiry_7d', 'expiry_30d')")

    # 13. v4: 删 batch.expiry_date / production_date 字段（重建表）
    batch_cols = [r["name"] for r in conn.execute("PRAGMA table_info(batch)").fetchall()]
    if "expiry_date" in batch_cols or "production_date" in batch_cols:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute("ALTER TABLE batch RENAME TO batch_old_v4")
            conn.execute("""CREATE TABLE batch (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no TEXT UNIQUE NOT NULL,
                sku_id INTEGER NOT NULL REFERENCES sku(id),
                supplier_id INTEGER REFERENCES supplier(id),
                inbound_order_no TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""INSERT INTO batch (id, batch_no, sku_id, supplier_id, inbound_order_no, created_at)
                SELECT id, batch_no, sku_id, supplier_id, inbound_order_no, created_at FROM batch_old_v4""")
            conn.execute("DROP TABLE batch_old_v4")
            conn.execute("PRAGMA writable_schema = ON")
            conn.execute(
                "UPDATE sqlite_master SET sql = REPLACE(REPLACE(sql, '\"batch_old_v4\"', 'batch'), 'batch_old_v4', 'batch') "
                "WHERE sql LIKE '%batch_old_v4%'"
            )
            conn.execute("PRAGMA writable_schema = OFF")
            conn.commit()
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

    # ============ v5 迁移 ============

    # 15. v5: 4 类单据加 attachments 字段（JSON 数组存附件路径）
    for table in ("inbound_order", "outbound_order", "damage_log", "stocktake_order"):
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "attachments" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN attachments TEXT")

    # 16. v5: stocktake_order 加 title 字段（盘点主题）
    st_cols = [r["name"] for r in conn.execute("PRAGMA table_info(stocktake_order)").fetchall()]
    if "title" not in st_cols:
        conn.execute("ALTER TABLE stocktake_order ADD COLUMN title TEXT")

    # 17. v5: location 加 storage_area_id 字段（v16 砸存储区后此步跳过，仅老 db 用）
    loc_cols = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    sa_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='storage_area'"
    ).fetchone()
    if "storage_area_id" not in loc_cols and sa_exists:
        conn.execute("ALTER TABLE location ADD COLUMN storage_area_id INTEGER REFERENCES storage_area(id)")

    # 18. v5: inbound_order.status CHECK 加 'cancelled'（重建表）
    ib_def = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='inbound_order'"
    ).fetchone()
    if ib_def and "'cancelled'" not in ib_def["sql"]:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute("ALTER TABLE inbound_order RENAME TO inbound_order_old_v5")
            conn.execute("""CREATE TABLE inbound_order (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                supplier_id INTEGER REFERENCES supplier(id),
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected','cancelled')),
                creator_id INTEGER NOT NULL REFERENCES user(id),
                approver_id INTEGER REFERENCES user(id),
                note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                approved_at DATETIME,
                attachments TEXT
            )""")
            conn.execute("""INSERT INTO inbound_order (id, order_no, supplier_id, status, creator_id, approver_id, note, created_at, approved_at, attachments)
                SELECT id, order_no, supplier_id, status, creator_id, approver_id, note, created_at, approved_at, attachments FROM inbound_order_old_v5""")
            conn.execute("DROP TABLE inbound_order_old_v5")
            conn.commit()
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

    # 19. v5: SEED 默认 1 个存储区（v16 砸后此步跳过）
    sa_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='storage_area'"
    ).fetchone()
    if sa_table:
        has_sa = conn.execute("SELECT 1 FROM storage_area LIMIT 1").fetchone()
        if not has_sa:
            cur = conn.execute("INSERT INTO storage_area (name, note) VALUES (?, ?)",
                               ("默认区", "首次启动自动创建，包含所有现有库位"))
            default_sa_id = cur.lastrowid
            conn.execute("UPDATE location SET storage_area_id=? WHERE storage_area_id IS NULL", (default_sa_id,))

    # 20. v6: 只保留 inapp/email/sms/webhook 4 个渠道，删多余的 row + 清 alert_rule 引用
    KEEP_CHANNELS = ("inapp", "email", "sms", "webhook")
    placeholders = ",".join("?" * len(KEEP_CHANNELS))
    obsolete = [r["code"] for r in conn.execute(
        f"SELECT code FROM notification_channel WHERE code NOT IN ({placeholders})", KEEP_CHANNELS
    ).fetchall()]
    if obsolete:
        conn.execute(
            f"DELETE FROM notification_channel WHERE code NOT IN ({placeholders})", KEEP_CHANNELS
        )
        for r in conn.execute("SELECT id, channel_codes FROM alert_rule").fetchall():
            codes = [c for c in (r["channel_codes"] or "").split(",") if c in KEEP_CHANNELS]
            if not codes:
                codes = ["inapp"]
            conn.execute("UPDATE alert_rule SET channel_codes=? WHERE id=?",
                         (",".join(codes), r["id"]))

    # 14. v4: 删 inbound_item.production_date / expiry_date 字段（重建表）
    ibi_cols = [r["name"] for r in conn.execute("PRAGMA table_info(inbound_item)").fetchall()]
    if "expiry_date" in ibi_cols or "production_date" in ibi_cols:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("ALTER TABLE inbound_item RENAME TO inbound_item_old_v4")
            conn.execute("""CREATE TABLE inbound_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL REFERENCES inbound_order(order_no),
                sku_id INTEGER NOT NULL REFERENCES sku(id),
                batch_no TEXT NOT NULL,
                location_id INTEGER NOT NULL REFERENCES location(id),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                unit_price REAL DEFAULT 0,
                batch_id INTEGER REFERENCES batch(id)
            )""")
            conn.execute("""INSERT INTO inbound_item (id, order_no, sku_id, batch_no, location_id, quantity, unit_price, batch_id)
                SELECT id, order_no, sku_id, batch_no, location_id, quantity, unit_price, batch_id FROM inbound_item_old_v4""")
            conn.execute("DROP TABLE inbound_item_old_v4")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    # 15. v13: 物品扩展字段（物资所属方/单位、品牌、类别、用途、合同内）
    sku_cols_v13 = [r["name"] for r in conn.execute("PRAGMA table_info(sku)").fetchall()]
    for col_name, col_def in [
        ("owner_party_id", "INTEGER REFERENCES owner_party(id)"),
        ("owner_unit_id", "INTEGER REFERENCES owner_unit(id)"),
        ("brand", "TEXT"),
        ("category", "TEXT"),
        ("category_major", "TEXT"),
        ("usage_purpose", "TEXT"),
        ("in_contract", "INTEGER DEFAULT 0"),
    ]:
        if col_name not in sku_cols_v13:
            conn.execute(f"ALTER TABLE sku ADD COLUMN {col_name} {col_def}")

    # 16. v14: sku 加 owner_admin_id + category_id + category_major_id（FK 引用新主数据表）
    sku_cols_v14 = [r["name"] for r in conn.execute("PRAGMA table_info(sku)").fetchall()]
    for col_name, col_def in [
        ("owner_admin_id",      "INTEGER REFERENCES owner_admin(id)"),
        ("category_id",         "INTEGER REFERENCES item_category(id)"),
        ("category_major_id",   "INTEGER REFERENCES item_category_major(id)"),
    ]:
        if col_name not in sku_cols_v14:
            conn.execute(f"ALTER TABLE sku ADD COLUMN {col_name} {col_def}")

    # 17. v14: warehouse 加 4 个 FK 字段（分配部门 / 使用部门 / 类型 / 责任人）
    wh_cols_v14 = [r["name"] for r in conn.execute("PRAGMA table_info(warehouse)").fetchall()]
    for col_name, col_def in [
        ("alloc_dept_id", "INTEGER REFERENCES wh_alloc_dept(id)"),
        ("use_dept_id",   "INTEGER REFERENCES wh_use_dept(id)"),
        ("wh_type_id",    "INTEGER REFERENCES wh_type(id)"),
        ("owner_user_id", "INTEGER REFERENCES user(id)"),
    ]:
        if col_name not in wh_cols_v14:
            conn.execute(f"ALTER TABLE warehouse ADD COLUMN {col_name} {col_def}")

    # 18. v15: warehouse 加 4 个扩展字段（编号 / 图片 / 备注 / 是否分配）
    wh_cols_v15 = [r["name"] for r in conn.execute("PRAGMA table_info(warehouse)").fetchall()]
    for col_name, col_def in [
        ("code",         "TEXT"),                # 仓库编号 WH000001
        ("image_path",   "TEXT"),                # 单图相对路径
        ("note",         "TEXT"),                # 备注
        ("is_allocated", "INTEGER DEFAULT 0"),   # 是否分配
    ]:
        if col_name not in wh_cols_v15:
            conn.execute(f"ALTER TABLE warehouse ADD COLUMN {col_name} {col_def}")
    # warehouse.code 上 UNIQUE（允许 NULL）
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_warehouse_code ON warehouse(code) WHERE code IS NOT NULL"
    )

    # 19. v15 方案 B: location 表加 building / floor 文本字段（楼栋 / 楼层，不独立建表）
    #     v25 起 location 改用 存放区域/位置；仅当还存在旧 code 列（即未经 v25）才补这两列，避免 v25 后重跑又加回来。
    loc_cols_v15 = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    if "code" in loc_cols_v15:
        for col_name, col_def in [
            ("building", "TEXT"),
            ("floor",    "TEXT"),
        ]:
            if col_name not in loc_cols_v15:
                conn.execute(f"ALTER TABLE location ADD COLUMN {col_name} {col_def}")

    # 20. v16: location 加 4 个 FK（分配部门 / 使用部门 / 类型 / 责任人），从 warehouse 搬过来
    loc_cols_v16 = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    for col_name, col_def in [
        ("alloc_dept_id", "INTEGER REFERENCES wh_alloc_dept(id)"),
        ("use_dept_id",   "INTEGER REFERENCES wh_use_dept(id)"),
        ("wh_type_id",    "INTEGER REFERENCES wh_type(id)"),
        ("owner_user_id", "INTEGER REFERENCES user(id)"),
    ]:
        if col_name not in loc_cols_v16:
            conn.execute(f"ALTER TABLE location ADD COLUMN {col_name} {col_def}")

    # 21. v16: 砸掉存储区 — DROP location.storage_area_id（重建表）+ DROP TABLE storage_area
    loc_cols_v16b = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    if "storage_area_id" in loc_cols_v16b:
        conn.commit()
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("ALTER TABLE location RENAME TO location_old_v16")
            conn.execute("""CREATE TABLE location (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
                code TEXT NOT NULL,
                zone_type TEXT NOT NULL CHECK(zone_type IN ('normal', 'cold', 'freeze', 'isolation', 'downgrade')),
                building TEXT,
                floor TEXT,
                alloc_dept_id INTEGER REFERENCES wh_alloc_dept(id),
                use_dept_id INTEGER REFERENCES wh_use_dept(id),
                wh_type_id INTEGER REFERENCES wh_type(id),
                owner_user_id INTEGER REFERENCES user(id),
                UNIQUE(warehouse_id, code)
            )""")
            conn.execute("""INSERT INTO location (
                id, warehouse_id, code, zone_type, building, floor,
                alloc_dept_id, use_dept_id, wh_type_id, owner_user_id
            ) SELECT
                id, warehouse_id, code, zone_type, building, floor,
                alloc_dept_id, use_dept_id, wh_type_id, owner_user_id
              FROM location_old_v16""")
            conn.execute("DROP TABLE location_old_v16")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA legacy_alter_table = OFF")
    # 砸 storage_area 表（如还在）
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='storage_area'").fetchone():
        conn.execute("DROP TABLE storage_area")

    # 22. v19: location 加 note + attachments 字段（备注 + 多文件附件）
    loc_cols_v19 = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    for col_name, col_def in [
        ("note",        "TEXT"),
        ("attachments", "TEXT"),  # JSON 数组（复用 attachments.py 多文件机制）
    ]:
        if col_name not in loc_cols_v19:
            conn.execute(f"ALTER TABLE location ADD COLUMN {col_name} {col_def}")

    # 23. v20: 砸 location.zone_type 字段（重建表）+ 砸 stock_log 引用 location 没问题（不动）
    loc_cols_v20 = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    if "zone_type" in loc_cols_v20:
        conn.commit()
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("ALTER TABLE location RENAME TO location_old_v20")
            conn.execute("""CREATE TABLE location (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
                code TEXT NOT NULL,
                building TEXT,
                floor TEXT,
                alloc_dept_id INTEGER REFERENCES wh_alloc_dept(id),
                use_dept_id INTEGER REFERENCES wh_use_dept(id),
                wh_type_id INTEGER REFERENCES wh_type(id),
                owner_user_id INTEGER REFERENCES user(id),
                note TEXT,
                attachments TEXT,
                UNIQUE(warehouse_id, code)
            )""")
            conn.execute("""INSERT INTO location (
                id, warehouse_id, code, building, floor,
                alloc_dept_id, use_dept_id, wh_type_id, owner_user_id, note, attachments
            ) SELECT
                id, warehouse_id, code, building, floor,
                alloc_dept_id, use_dept_id, wh_type_id, owner_user_id, note, attachments
              FROM location_old_v20""")
            conn.execute("DROP TABLE location_old_v20")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA legacy_alter_table = OFF")

    # 18. v14 方案 B: location 表加 building / floor 文本字段（楼栋 / 楼层，不独立建表）
    #     同 v15：仅未经 v25（还有 code 列）时才补，避免 v25 后重跑把列加回来。
    loc_cols_v14 = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    if "code" in loc_cols_v14:
        for col_name, col_def in [
            ("building", "TEXT"),
            ("floor",    "TEXT"),
        ]:
            if col_name not in loc_cols_v14:
                conn.execute(f"ALTER TABLE location ADD COLUMN {col_name} {col_def}")

    # 24. v21: 砸 supplier — batch / inbound_order 删 supplier_id 字段 + DROP TABLE supplier
    batch_cols_v21 = [r["name"] for r in conn.execute("PRAGMA table_info(batch)").fetchall()]
    if "supplier_id" in batch_cols_v21:
        conn.commit()
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("ALTER TABLE batch RENAME TO batch_old_v21")
            conn.execute("""CREATE TABLE batch (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no TEXT UNIQUE NOT NULL,
                sku_id INTEGER NOT NULL REFERENCES sku(id),
                inbound_order_no TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""INSERT INTO batch (id, batch_no, sku_id, inbound_order_no, created_at)
                SELECT id, batch_no, sku_id, inbound_order_no, created_at FROM batch_old_v21""")
            conn.execute("DROP TABLE batch_old_v21")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA legacy_alter_table = OFF")

    ib_cols_v21 = [r["name"] for r in conn.execute("PRAGMA table_info(inbound_order)").fetchall()]
    if "supplier_id" in ib_cols_v21:
        conn.commit()
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("ALTER TABLE inbound_order RENAME TO inbound_order_old_v21")
            conn.execute("""CREATE TABLE inbound_order (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected','cancelled')),
                creator_id INTEGER NOT NULL REFERENCES user(id),
                approver_id INTEGER REFERENCES user(id),
                note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                approved_at DATETIME,
                attachments TEXT
            )""")
            conn.execute("""INSERT INTO inbound_order (id, order_no, status, creator_id, approver_id, note, created_at, approved_at, attachments)
                SELECT id, order_no, status, creator_id, approver_id, note, created_at, approved_at, attachments FROM inbound_order_old_v21""")
            conn.execute("DROP TABLE inbound_order_old_v21")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA legacy_alter_table = OFF")

    # 26. v22: inbound_order / outbound_order 加 operator_id（入/出库经手员工，可选）
    for table in ("inbound_order", "outbound_order"):
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "operator_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN operator_id INTEGER REFERENCES user(id)")

    # 27. v22: sku 加 image_path 字段（物品图片单图，仿 warehouse.image_path）
    sku_cols_v22 = [r["name"] for r in conn.execute("PRAGMA table_info(sku)").fetchall()]
    if "image_path" not in sku_cols_v22:
        conn.execute("ALTER TABLE sku ADD COLUMN image_path TEXT")

    # 25. v21: 清洗 location.building / floor 历史脏数据（曾被写入 "1号楼" / "1层" 这种带后缀的字符串）
    #     仅当 building 列仍存在（未经 v25）时执行，避免 v25 删列后重跑报 no such column。
    if "building" in [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]:
        conn.execute(
            "UPDATE location SET building = TRIM(REPLACE(REPLACE(REPLACE(building, '号楼',''), '号 楼',''), ' ','')) "
            "WHERE building IS NOT NULL AND (building LIKE '%号楼%' OR building LIKE '%号 楼%' OR building LIKE '% %')"
        )
        conn.execute(
            "UPDATE location SET floor = TRIM(REPLACE(REPLACE(floor, '层',''), ' ','')) "
            "WHERE floor IS NOT NULL AND (floor LIKE '%层%' OR floor LIKE '% %')"
        )

    # 28. v23: 责任人改为独立主数据表 wh_owner（不再复用 user）。
    #     - location 加新外键 resp_owner_id（保留旧 owner_user_id 列不动）
    #     - 把现有 location.owner_user_id 指向的 user.display_name 灌进 wh_owner，再按名字回填 resp_owner_id
    loc_cols_v23 = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    if "resp_owner_id" not in loc_cols_v23:
        conn.execute("ALTER TABLE location ADD COLUMN resp_owner_id INTEGER REFERENCES wh_owner(id)")
    # 数据迁移（幂等）：老库位的责任人(user)名字 → wh_owner，再回填新外键
    legacy_owners = conn.execute(
        "SELECT DISTINCT u.display_name AS nm FROM location l "
        "JOIN user u ON u.id = l.owner_user_id "
        "WHERE l.owner_user_id IS NOT NULL "
        "AND u.display_name IS NOT NULL AND TRIM(u.display_name) <> ''"
    ).fetchall()
    for r in legacy_owners:
        conn.execute("INSERT OR IGNORE INTO wh_owner (name) VALUES (?)", (r["nm"],))
    # 按名字把 resp_owner_id 回填（仅回填尚未设置的行，幂等不覆盖已迁移）
    conn.execute(
        "UPDATE location SET resp_owner_id = ("
        "  SELECT wo.id FROM wh_owner wo "
        "  JOIN user u ON u.display_name = wo.name "
        "  WHERE u.id = location.owner_user_id) "
        "WHERE resp_owner_id IS NULL AND owner_user_id IS NOT NULL"
    )

    # 29. v24: outbound_order 加 requisition_no（出库单溯源到领用申请单；申领转出库时回填）
    ob_cols_v24 = [r["name"] for r in conn.execute("PRAGMA table_info(outbound_order)").fetchall()]
    if "requisition_no" not in ob_cols_v24:
        conn.execute("ALTER TABLE outbound_order ADD COLUMN requisition_no TEXT REFERENCES requisition_order(order_no)")

    # 30. v25: location 废弃 code/building/floor → 改为 存放区域(storage_area)/存放位置(storage_position)。
    #     重建表保 id（所有外键引用 location.id，安全）；老行最小回填：区域='默认区'，位置=原 code。
    loc_cols_v25 = [r["name"] for r in conn.execute("PRAGMA table_info(location)").fetchall()]
    if "code" in loc_cols_v25:
        conn.commit()
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("ALTER TABLE location RENAME TO location_old_v25")
            conn.execute("""CREATE TABLE location (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
                storage_area TEXT NOT NULL DEFAULT '默认区',
                storage_position TEXT NOT NULL DEFAULT '',
                alloc_dept_id INTEGER REFERENCES wh_alloc_dept(id),
                use_dept_id INTEGER REFERENCES wh_use_dept(id),
                wh_type_id INTEGER REFERENCES wh_type(id),
                owner_user_id INTEGER REFERENCES user(id),
                resp_owner_id INTEGER REFERENCES wh_owner(id),
                note TEXT,
                attachments TEXT,
                UNIQUE(warehouse_id, storage_area, storage_position)
            )""")
            conn.execute("""INSERT INTO location (
                id, warehouse_id, storage_area, storage_position,
                alloc_dept_id, use_dept_id, wh_type_id, owner_user_id, resp_owner_id, note, attachments
            ) SELECT
                id, warehouse_id, '默认区',
                COALESCE(NULLIF(TRIM(code), ''), '位置-' || id),
                alloc_dept_id, use_dept_id, wh_type_id, owner_user_id, resp_owner_id, note, attachments
              FROM location_old_v25""")
            conn.execute("DROP TABLE location_old_v25")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA legacy_alter_table = OFF")

    # 砸 supplier 表（最后做，确保 FK 引用已清）+ 清菜单权限残留
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='supplier'").fetchone():
        conn.execute("DROP TABLE supplier")
    conn.execute("DELETE FROM menu_permission WHERE menu_key='supplier'")
    for r in conn.execute("SELECT code, menu_keys FROM role_preset").fetchall():
        keys = [k for k in (r["menu_keys"] or "").split(",") if k and k != "supplier"]
        new_keys = ",".join(keys)
        if new_keys != (r["menu_keys"] or ""):
            conn.execute("UPDATE role_preset SET menu_keys=? WHERE code=?", (new_keys, r["code"]))


def _seed_users(conn):
    for username, password, role, display_name, position in SEED_USERS:
        conn.execute(
            "INSERT OR IGNORE INTO user (username, password_hash, role, display_name, position) VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), role, display_name, position),
        )
        # 兼容老种子（已存在但 position 为空时补上）
        conn.execute(
            "UPDATE user SET position = ?, display_name = ? WHERE username = ? AND (position IS NULL OR position = '')",
            (position, display_name, username),
        )


def _seed_warehouse_and_locations(conn):
    name, addr = SEED_WAREHOUSE
    conn.execute("INSERT OR IGNORE INTO warehouse (name, address) VALUES (?, ?)", (name, addr))
    wh = conn.execute("SELECT id FROM warehouse WHERE name = ?", (name,)).fetchone()
    for area, position in SEED_LOCATIONS:
        conn.execute(
            "INSERT OR IGNORE INTO location (warehouse_id, storage_area, storage_position) VALUES (?, ?, ?)",
            (wh["id"], area, position),
        )


def _seed_skus(conn):
    for code, name, spec, unit, ss, cp, sp in SEED_SKUS:
        conn.execute(
            "INSERT OR IGNORE INTO sku (code, name, spec, unit, safety_stock, cost_price, sale_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, name, spec, unit, ss, cp, sp),
        )


def _seed_customers(conn):
    for phone, name, addr in SEED_CUSTOMERS:
        conn.execute(
            "INSERT OR IGNORE INTO customer (phone, name, default_address) VALUES (?, ?, ?)",
            (phone, name, addr),
        )


def _seed_wh_owners(conn):
    """v23: 责任人主数据（独立 wh_owner 表，幂等）。"""
    for name, note in SEED_WH_OWNERS:
        conn.execute(
            "INSERT OR IGNORE INTO wh_owner (name, note) VALUES (?, ?)",
            (name, note),
        )


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
