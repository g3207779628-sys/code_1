import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "warehouse.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO _meta (key, value) VALUES ('schema_version', '0')"
        )
