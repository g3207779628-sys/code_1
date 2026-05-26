"""库存预测（v4 简化版）：只列出低于安全库存的商品。

list_low_stock() -> list of dict
- 取所有 sku
- 计算每个 sku 的总在仓 = SUM(inventory.on_hand)
- HAVING 在仓 < 安全库存 AND 安全库存 > 0
- 返回字典：{sku_id, name, spec, unit, safety_stock, current, gap, suggestion}
"""
import database


def list_low_stock():
    sql = """SELECT s.id AS sku_id, s.name, s.spec, s.unit, s.safety_stock,
                    COALESCE(SUM(i.on_hand), 0) AS current
             FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
             GROUP BY s.id
             HAVING current < s.safety_stock AND s.safety_stock > 0
             ORDER BY (s.safety_stock - current) DESC, s.name"""
    with database.get_conn() as conn:
        rows = conn.execute(sql).fetchall()

    for r in rows:
        gap = r["safety_stock"] - r["current"]
        # 建议补货量：缺口 × 1.5（留一些缓冲），向上取 5 的整数倍
        suggested = int(((gap * 1.5) + 4) // 5 * 5)
        yield {
            "sku_id": r["sku_id"],
            "name": r["name"],
            "spec": r["spec"],
            "unit": r["unit"],
            "safety_stock": r["safety_stock"],
            "current": r["current"],
            "gap": gap,
            "suggested": suggested,
            "action": f"建议补货 {suggested} {r['unit']}（缺口 {gap}，含 50% 缓冲）",
        }


# 兼容：保留旧函数名，但内部调用新逻辑
def forecast_next_month(**kwargs):
    return list_low_stock()
