# -*- coding: utf-8 -*-
"""领用申请门户 —— 独立、免登录、公开页（给手机扫码用）。

设计要点（呼应"公开页与后台隔离"）：
- 这是和 code_1 主后台（app.py）完全独立的另一个程序、另一个端口。
- 公网只暴露本程序；主后台留在内网、要登录。
- 但二者 import 同一个 database 模块 → 共用同一个 warehouse.db（"地下传送带"）。
- 本程序只能做一件事：往 requisition_order/requisition_item 写一条申请单。
  它不读库存、不改库存、没有任何后台权限，所以即便暴露在公网也"偷不到东西"。

启动：python apply_app.py   （默认 5001 端口）
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for

sys.path.insert(0, str(Path(__file__).parent))
import database  # 共用 code_1 的数据库与表定义

app = Flask(__name__)
app.secret_key = "requisition-portal-only"  # 仅本地用途；门户不涉及登录态


def _portal_data():
    """申请页下拉数据源（只读查询，空表给 fallback 示例，保证页面可用）。"""
    with database.get_conn() as conn:
        depts = [dict(r) for r in conn.execute(
            "SELECT id, name FROM wh_use_dept ORDER BY name"
        ).fetchall()]
        locations = [dict(r) for r in conn.execute(
            "SELECT l.id, (l.storage_area || ' / ' || l.storage_position) AS code, w.name AS warehouse_name "
            "FROM location l JOIN warehouse w ON w.id = l.warehouse_id ORDER BY l.storage_area, l.storage_position"
        ).fetchall()]
        categories = [dict(r) for r in conn.execute(
            "SELECT id, name FROM item_category_major ORDER BY name"
        ).fetchall()]
        if not categories:
            rows = conn.execute(
                "SELECT DISTINCT category_major AS name FROM sku "
                "WHERE category_major IS NOT NULL AND category_major != '' ORDER BY category_major"
            ).fetchall()
            categories = [{"id": None, "name": r["name"]} for r in rows]
        skus = [dict(r) for r in conn.execute(
            "SELECT s.id, s.code, s.name, s.spec, s.unit, "
            "(SELECT COALESCE(SUM(on_hand),0) FROM inventory WHERE sku_id=s.id) AS on_hand "
            "FROM sku s ORDER BY s.code"
        ).fetchall()]
    dept_is_sample = not depts
    if dept_is_sample:
        depts = [{"id": None, "name": n} for n in
                 ("行政部", "技术部", "财务部", "市场部", "仓储部", "采购部")]
    cat_is_sample = not categories
    if cat_is_sample:
        categories = [{"id": None, "name": n} for n in
                      ("办公用品", "IT 设备", "耗材", "招待用品")]
    return depts, locations, categories, skus, dept_is_sample, cat_is_sample


def _to_int(v):
    try:
        return int(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


@app.route("/")
def apply_form():
    depts, locations, categories, skus, dsamp, csamp = _portal_data()
    return render_template(
        "apply_portal.html",
        depts=depts, locations=locations, categories=categories, skus=skus,
        dept_is_sample=dsamp, cat_is_sample=csamp,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


@app.route("/submit", methods=["POST"])
def submit():
    applicant = request.form.get("applicant", "").strip()
    dept_id = _to_int(request.form.get("dept_id"))
    dept_name = request.form.get("dept_text", "").strip()
    location_id = _to_int(request.form.get("location_id"))
    location_code = request.form.get("location_text", "").strip()
    category_id = _to_int(request.form.get("category_id"))
    category_name = request.form.get("category_text", "").strip()
    apply_date = request.form.get("apply_date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    note = request.form.get("note", "").strip()

    # 明细：前端 JS 组装成 items_json
    try:
        raw_items = json.loads(request.form.get("items_json", "[]"))
    except (ValueError, TypeError):
        raw_items = []
    items = []
    for it in raw_items:
        qty = _to_int(it.get("quantity"))
        if not qty or qty <= 0:
            continue
        items.append({
            "sku_id": _to_int(it.get("sku_id")),
            "code": (it.get("code") or "").strip(),
            "name": (it.get("name") or "").strip(),
            "unit": (it.get("unit") or "").strip(),
            "quantity": qty,
        })

    # 后端兜底校验（前端也校验，这里防绕过/防脏数据）
    if not applicant or not items:
        depts, locations, categories, skus, dsamp, csamp = _portal_data()
        return render_template(
            "apply_portal.html",
            depts=depts, locations=locations, categories=categories, skus=skus,
            dept_is_sample=dsamp, cat_is_sample=csamp,
            today=datetime.now().strftime("%Y-%m-%d"),
            error="请至少填写申请人，并添加一行有效的领用物品和数量。",
            form={"applicant": applicant, "dept_text": dept_name,
                  "location_text": location_code, "category_id": category_id,
                  "apply_date": apply_date, "note": note},
        ), 400

    with database.get_conn() as conn:
        order_no = database.gen_order_no(conn, "LYD", "requisition_order")
        conn.execute(
            "INSERT INTO requisition_order "
            "(order_no, applicant, dept_id, dept_name, location_id, location_code, "
            " category_id, category_name, apply_date, note, status, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'submitted','portal')",
            (order_no, applicant, dept_id, dept_name, location_id, location_code,
             category_id, category_name, apply_date, note),
        )
        for it in items:
            conn.execute(
                "INSERT INTO requisition_item "
                "(order_no, sku_id, sku_code, sku_name, unit, quantity) VALUES (?,?,?,?,?,?)",
                (order_no, it["sku_id"], it["code"], it["name"], it["unit"], it["quantity"]),
            )

    # PRG：提交后重定向到成功页，避免刷新重复提交
    return redirect(url_for("done", no=order_no))


@app.route("/done")
def done():
    return render_template("apply_done.html", order_no=request.args.get("no", ""))


if __name__ == "__main__":
    import os
    database.init_db()  # 幂等：确保 requisition 等表已建好
    # 默认用 waitress（生产级 WSGI server，跨平台）；调试设 FLASK_DEV=1 走 Flask dev server
    if os.environ.get("FLASK_DEV") == "1":
        print("[portal] Flask dev server on 0.0.0.0:5001")
        app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
    else:
        from waitress import serve
        print("[portal] waitress on 0.0.0.0:5001 (threads=4)  免登录公开页")
        serve(app, host="0.0.0.0", port=5001, threads=4)
