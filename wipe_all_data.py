"""彻底清空业务数据 + 主数据（SKU/供应商/仓库/库位/存储区/批次）。

保留：
  - user / position / menu_permission / role_preset（用户登录 + 权限）
  - notification_channel / alert_rule（通知配置）
  - 系统设置类（settings 等）

清空：
  - 所有业务单据（入库/出库/调拨/报损/盘点）
  - stock_log / inventory / batch
  - alert_event（预警事件历史）
  - sku / warehouse / location（让用户重新导入）

副作用：创建 .wiped 标记文件 → database.init_db() 之后不再 SEED 主数据（不会自动塞回示例 SKU/供应商/仓库）。
       要恢复 demo 数据：删 .wiped 文件 + 跑 reset_themed.py。

用法：
  python wipe_all_data.py
  （命令行会要求输入 "YES" 确认）
"""
import sys
from pathlib import Path
import database

# 顺序：先清子表 / FK 引用方，后清主表
TABLES_TO_WIPE = [
    # 业务流水（含 FK 到 inventory/batch/sku/location）
    "stock_log",
    "inventory",
    # 业务单据明细
    "transfer_item",
    "outbound_item",
    "inbound_item",
    "stocktake_item",
    "damage_log",
    # 业务单据主表
    "transfer_order",
    "outbound_order",
    "inbound_order",
    "stocktake_order",
    # 预警事件历史（不动 alert_rule 配置）
    "alert_event",
    # 库存快照
    "inventory_snapshot",
    # 批次（被上面 FK 引用）
    "batch",
    # 主数据 — 用户要重新导入
    "sku",
    "location",
    "warehouse",
]


def wipe():
    with database.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in TABLES_TO_WIPE:
            try:
                n = conn.execute(f"DELETE FROM {t}").rowcount
                print(f"  清空 {t}: {n} 行")
            except Exception as e:
                print(f"  跳过 {t}（表不存在或其他）: {e}")
        # 重置 SQLite 自增 ID
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ({})".format(
                ",".join("?" * len(TABLES_TO_WIPE))
            ), TABLES_TO_WIPE)
            print("  重置自增 ID")
        except Exception:
            pass
        conn.execute("PRAGMA foreign_keys = ON")

    # 显示保留下来的关键数据
    with database.get_conn() as conn:
        n_user = conn.execute("SELECT COUNT(*) AS c FROM user").fetchone()["c"]
        n_perm = conn.execute("SELECT COUNT(*) AS c FROM menu_permission").fetchone()["c"]
        n_ch = conn.execute("SELECT COUNT(*) AS c FROM notification_channel").fetchone()["c"]
    # 创建 .wiped 标记，让 database.init_db() 跳过主数据 SEED
    marker = Path(__file__).parent / ".wiped"
    marker.write_text("wiped at clean start\n", encoding="utf-8")
    print()
    print(f"保留：user={n_user} · menu_permission={n_perm} · notification_channel={n_ch}")
    print(f"已创建 {marker.name} 标记 — Flask 启动时 init_db 将跳过主数据 SEED")
    print("完成。现在系统是空的，可以从 /sku/new、/warehouse/new 等页面开始导入主数据。")
    print("（要恢复 demo 数据：删除 .wiped 文件后跑 reset_themed.py）")


if __name__ == "__main__":
    print("=" * 60)
    print("[!] 将清空所有业务数据 + 主数据（SKU/仓库/库位/批次/流水）")
    print("[!] 保留：用户 / 权限 / 通知渠道配置")
    print("=" * 60)
    answer = input("输入 YES 确认清空（其他任何字符取消）：")
    if answer.strip() == "YES":
        wipe()
    else:
        print("已取消，未做任何改动。")
        sys.exit(0)
