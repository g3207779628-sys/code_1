"""每日 SQLite 备份。

backup_db() 把 warehouse.db 复制到 backups/warehouse_YYYYMMDD_HHMMSS.db
保留最近 30 份，更早的自动清理。
"""
import shutil
from datetime import datetime
from pathlib import Path

import database

BACKUP_DIR = Path(__file__).parent / "backups"
KEEP_LAST = 30


def backup_db():
    BACKUP_DIR.mkdir(exist_ok=True)
    src = database.DB_PATH
    if not src.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"warehouse_{stamp}.db"
    # 在 SQLite 文件锁定时直接拷贝可能拿到不一致快照，用 sqlite 在线备份 API 更稳：
    import sqlite3
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    # 清理：只留最近 KEEP_LAST 份
    files = sorted(BACKUP_DIR.glob("warehouse_*.db"))
    for f in files[:-KEEP_LAST]:
        try:
            f.unlink()
        except Exception:
            pass

    return str(dst)


def list_backups():
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("warehouse_*.db"), reverse=True)


def restore_db(filename):
    """从 backups/{filename} 还原到 warehouse.db。
    1. 先做一次『恢复前快照』pre_restore_TIMESTAMP.db，便于反悔
    2. 用 sqlite3 backup API 把备份内容写回当前 DB（不替换文件，避免 Windows 文件锁）
    返回 (ok: bool, msg: str)
    """
    import sqlite3

    # 1. 校验目标文件
    safe_name = Path(filename).name  # 防路径穿越
    src = BACKUP_DIR / safe_name
    if not src.exists():
        return False, f"备份文件不存在：{safe_name}"
    if not safe_name.startswith("warehouse_") or not safe_name.endswith(".db"):
        return False, "非法的备份文件名"

    # 2. 自动做一次「恢复前快照」，便于反悔
    pre_path = None
    if database.DB_PATH.exists():
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_path = BACKUP_DIR / f"warehouse_pre_restore_{stamp}.db"
        try:
            src_conn = sqlite3.connect(database.DB_PATH)
            dst_conn = sqlite3.connect(pre_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
        except Exception as e:
            return False, f"恢复前快照失败：{e}"

    # 3. 把备份文件回写到当前 DB（用 sqlite backup API，绕过 Windows 文件锁）
    try:
        bak_conn = sqlite3.connect(src)
        cur_conn = sqlite3.connect(database.DB_PATH)
        try:
            bak_conn.backup(cur_conn)
        finally:
            cur_conn.close()
            bak_conn.close()
    except Exception as e:
        return False, f"还原失败：{e}"

    return True, f"已从 {safe_name} 恢复" + (f"；恢复前快照保存为 {pre_path.name}" if pre_path else "")
