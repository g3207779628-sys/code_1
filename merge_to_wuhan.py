# -*- coding: utf-8 -*-
"""把 4 个部门仓(客服/工程/环境/秩序)归并成单个「武汉仓库」。

- 处理 (storage_area, storage_position) 撞唯一约束的库位：保留最小 id，其余库位的
  inventory / stock_log / inbound_item.location_id 重指到幸存库位，再删多余库位。
- 全部库位 warehouse_id 改成武汉仓库。
- 删掉清空后的 4 个部门仓。
"""
import sys, sqlite3
sys.stdout.reconfigure(encoding="utf-8")
import database

LOC_REFS = [("inventory", "location_id"), ("stock_log", "location_id"), ("inbound_item", "location_id")]

def main():
    conn = database.get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # 1. 武汉仓库
        row = conn.execute("SELECT id FROM warehouse WHERE name='武汉仓库'").fetchone()
        wuhan_id = row["id"] if row else conn.execute(
            "INSERT INTO warehouse (name) VALUES ('武汉仓库')").lastrowid
        print("武汉仓库 id =", wuhan_id)

        # 2. 解决 (area, position) 冲突：合并到最小 id 的库位
        groups = conn.execute(
            "SELECT storage_area, storage_position, GROUP_CONCAT(id) ids, COUNT(*) n "
            "FROM location GROUP BY storage_area, storage_position HAVING n>1").fetchall()
        merged = 0
        for g in groups:
            ids = sorted(int(x) for x in g["ids"].split(","))
            survivor, dups = ids[0], ids[1:]
            for d in dups:
                for t, col in LOC_REFS:
                    conn.execute(f"UPDATE {t} SET {col}=? WHERE {col}=?", (survivor, d))
                conn.execute("DELETE FROM location WHERE id=?", (d,))
                merged += 1
            print(f"  合并库位 [{g['storage_area']} / {g['storage_position']}]: {dups} -> {survivor}")
        print(f"合并掉 {merged} 个重复库位")

        # 3. 全部库位归武汉仓库
        n = conn.execute("UPDATE location SET warehouse_id=? WHERE warehouse_id<>?",
                         (wuhan_id, wuhan_id)).rowcount
        print(f"重指 {n} 个库位到武汉仓库")

        # 4. 删空的部门仓
        dn = conn.execute("DELETE FROM warehouse WHERE id<>?", (wuhan_id,)).rowcount
        print(f"删除 {dn} 个部门仓")

        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # 5. 验证
        print("\n=== 验证 ===")
        for r in conn.execute("SELECT id,name FROM warehouse"):
            print("  仓库:", r["id"], r["name"])
        print("  库位总数:", conn.execute("SELECT COUNT(*) FROM location").fetchone()[0])
        print("  库位 warehouse_id 都=武汉?:",
              conn.execute("SELECT COUNT(*) FROM location WHERE warehouse_id<>?", (wuhan_id,)).fetchone()[0] == 0)
        print("  inventory 行:", conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0])
        print("  孤儿 inventory(指向不存在库位):",
              conn.execute("SELECT COUNT(*) FROM inventory i LEFT JOIN location l ON l.id=i.location_id WHERE l.id IS NULL").fetchone()[0])
    finally:
        conn.close()

if __name__ == "__main__":
    main()
