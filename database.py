import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).parent / "warehouse.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
    storage_zone TEXT NOT NULL CHECK(storage_zone IN ('normal', 'cold', 'freeze')),
    safety_stock INTEGER DEFAULT 0,
    cost_price REAL DEFAULT 0,
    sale_price REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS supplier (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    contact TEXT,
    phone TEXT,
    quality_score INTEGER DEFAULT 100,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    name TEXT,
    default_address TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


SEED_USERS = [
    ("admin", "admin123", "admin", "管理员"),
    ("manager", "manager123", "manager", "经理"),
    ("staff", "staff123", "staff", "普通员工"),
]

SEED_WAREHOUSE = ("主仓", "杭州市西湖区文三路 100 号")

SEED_LOCATIONS = [
    ("A-01-01", "cold"),
    ("A-01-02", "cold"),
    ("A-02-01", "cold"),
    ("B-01-01", "freeze"),
    ("B-01-02", "freeze"),
    ("C-01-01", "normal"),
    ("ISO-01", "isolation"),
    ("DG-01", "downgrade"),
]

SEED_SUPPLIERS = [
    ("味之源食品厂", "李经理", "13800001111", 95),
    ("鲜达预制菜", "王主管", "13800002222", 88),
]

SEED_SKUS = [
    ("YCY-350-N", "酸菜鱼预制菜", "350g·正常辣", "份", "cold", 50, 18.0, 29.9),
    ("YCY-700-N", "酸菜鱼预制菜", "700g·正常辣", "份", "cold", 30, 33.0, 55.0),
    ("YCY-350-L", "酸菜鱼预制菜", "350g·少辣版", "份", "cold", 50, 18.0, 29.9),
    ("FQNF-500", "番茄牛腩预制菜", "500g", "份", "freeze", 40, 25.0, 42.0),
    ("MLXJ-400", "麻辣香锅预制菜", "400g", "份", "cold", 40, 20.0, 35.0),
]

SEED_CUSTOMERS = [
    ("13900001111", "张三", "杭州市西湖区文三路 1 号"),
    ("13900002222", "李四", "杭州市余杭区未来科技城 88 号"),
]


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _seed_users(conn)
        _seed_warehouse_and_locations(conn)
        _seed_suppliers(conn)
        _seed_skus(conn)
        _seed_customers(conn)


def _seed_users(conn):
    for username, password, role, display_name in SEED_USERS:
        conn.execute(
            "INSERT OR IGNORE INTO user (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), role, display_name),
        )


def _seed_warehouse_and_locations(conn):
    name, addr = SEED_WAREHOUSE
    conn.execute("INSERT OR IGNORE INTO warehouse (name, address) VALUES (?, ?)", (name, addr))
    wh = conn.execute("SELECT id FROM warehouse WHERE name = ?", (name,)).fetchone()
    for code, zone in SEED_LOCATIONS:
        conn.execute(
            "INSERT OR IGNORE INTO location (warehouse_id, code, zone_type) VALUES (?, ?, ?)",
            (wh["id"], code, zone),
        )


def _seed_suppliers(conn):
    for name, contact, phone, score in SEED_SUPPLIERS:
        conn.execute(
            "INSERT OR IGNORE INTO supplier (name, contact, phone, quality_score) VALUES (?, ?, ?, ?)",
            (name, contact, phone, score),
        )


def _seed_skus(conn):
    for code, name, spec, unit, zone, ss, cp, sp in SEED_SKUS:
        conn.execute(
            "INSERT OR IGNORE INTO sku (code, name, spec, unit, storage_zone, safety_stock, cost_price, sale_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (code, name, spec, unit, zone, ss, cp, sp),
        )


def _seed_customers(conn):
    for phone, name, addr in SEED_CUSTOMERS:
        conn.execute(
            "INSERT OR IGNORE INTO customer (phone, name, default_address) VALUES (?, ?, ?)",
            (phone, name, addr),
        )


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
