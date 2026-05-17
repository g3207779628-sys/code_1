import sqlite3
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import database

app = Flask(__name__)
app.secret_key = "code_1-dev-secret-change-in-prod"


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("请先登录", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                flash("请先登录", "error")
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                flash(f"权限不足。该操作需要角色：{', '.join(roles)}", "error")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return wrapper
    return deco


# ============ 登录 / 登出 ============

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with database.get_conn() as conn:
            row = conn.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            session["role"] = row["role"]
            session["display_name"] = row["display_name"] or row["username"]
            flash(f"欢迎回来，{session['display_name']}", "success")
            return redirect(url_for("index"))
        flash("用户名或密码错误", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录", "success")
    return redirect(url_for("login"))


# ============ 首页 ============

@app.route("/")
@login_required
def index():
    with database.get_conn() as conn:
        stats = {
            "sku": conn.execute("SELECT COUNT(*) AS c FROM sku").fetchone()["c"],
            "warehouse": conn.execute("SELECT COUNT(*) AS c FROM warehouse").fetchone()["c"],
            "location": conn.execute("SELECT COUNT(*) AS c FROM location").fetchone()["c"],
            "supplier": conn.execute("SELECT COUNT(*) AS c FROM supplier").fetchone()["c"],
            "customer": conn.execute("SELECT COUNT(*) AS c FROM customer").fetchone()["c"],
            "batch": conn.execute("SELECT COUNT(*) AS c FROM batch").fetchone()["c"],
            "total_on_hand": conn.execute("SELECT COALESCE(SUM(on_hand), 0) AS c FROM inventory").fetchone()["c"],
            "total_reserved": conn.execute("SELECT COALESCE(SUM(reserved), 0) AS c FROM inventory").fetchone()["c"],
            "draft_inbound": conn.execute("SELECT COUNT(*) AS c FROM inbound_order WHERE status = 'draft'").fetchone()["c"],
            "pending_pick": conn.execute("SELECT COUNT(*) AS c FROM outbound_order WHERE status = 'pending'").fetchone()["c"],
            "expiry_warn": conn.execute(
                "SELECT COUNT(*) AS c FROM inventory inv JOIN batch b ON b.id=inv.batch_id "
                "WHERE inv.on_hand > 0 AND date(b.expiry_date) <= date('now', '+30 days')"
            ).fetchone()["c"],
            "pending_damage": conn.execute("SELECT COUNT(*) AS c FROM damage_log WHERE status = 'pending'").fetchone()["c"],
            "open_stocktake": conn.execute("SELECT COUNT(*) AS c FROM stocktake_order WHERE status = 'open'").fetchone()["c"],
            "open_return": conn.execute("SELECT COUNT(*) AS c FROM return_order WHERE status IN ('approved','received','qc_done')").fetchone()["c"],
        }
    return render_template("index.html", stats=stats)


# ============ SKU ============

@app.route("/sku")
@login_required
def sku_list():
    with database.get_conn() as conn:
        skus = conn.execute("SELECT * FROM sku ORDER BY code").fetchall()
    return render_template("sku_list.html", skus=skus)


@app.route("/sku/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def sku_new():
    if request.method == "POST":
        try:
            with database.get_conn() as conn:
                conn.execute(
                    "INSERT INTO sku (code, name, spec, unit, storage_zone, safety_stock, cost_price, sale_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.form["code"].strip(),
                        request.form["name"].strip(),
                        request.form.get("spec", "").strip(),
                        request.form.get("unit", "份").strip(),
                        request.form["storage_zone"],
                        int(request.form.get("safety_stock") or 0),
                        float(request.form.get("cost_price") or 0),
                        float(request.form.get("sale_price") or 0),
                    ),
                )
            flash("SKU 创建成功", "success")
            return redirect(url_for("sku_list"))
        except sqlite3.IntegrityError:
            flash("SKU 编码已存在", "error")
    return render_template("sku_form.html", sku=None)


@app.route("/sku/<int:sku_id>/edit", methods=["GET", "POST"])
@role_required("admin", "manager")
def sku_edit(sku_id):
    with database.get_conn() as conn:
        sku = conn.execute("SELECT * FROM sku WHERE id = ?", (sku_id,)).fetchone()
        if not sku:
            flash("SKU 不存在", "error")
            return redirect(url_for("sku_list"))
        if request.method == "POST":
            try:
                conn.execute(
                    "UPDATE sku SET code=?, name=?, spec=?, unit=?, storage_zone=?, safety_stock=?, cost_price=?, sale_price=? WHERE id=?",
                    (
                        request.form["code"].strip(),
                        request.form["name"].strip(),
                        request.form.get("spec", "").strip(),
                        request.form.get("unit", "份").strip(),
                        request.form["storage_zone"],
                        int(request.form.get("safety_stock") or 0),
                        float(request.form.get("cost_price") or 0),
                        float(request.form.get("sale_price") or 0),
                        sku_id,
                    ),
                )
                flash("SKU 更新成功", "success")
                return redirect(url_for("sku_list"))
            except sqlite3.IntegrityError:
                flash("SKU 编码冲突", "error")
    return render_template("sku_form.html", sku=sku)


@app.route("/sku/<int:sku_id>/delete", methods=["POST"])
@role_required("admin")
def sku_delete(sku_id):
    with database.get_conn() as conn:
        conn.execute("DELETE FROM sku WHERE id = ?", (sku_id,))
    flash("SKU 已删除", "success")
    return redirect(url_for("sku_list"))


# ============ 仓库 ============

@app.route("/warehouse")
@login_required
def warehouse_list():
    with database.get_conn() as conn:
        warehouses = conn.execute(
            """SELECT w.*, (SELECT COUNT(*) FROM location WHERE warehouse_id = w.id) AS location_count
               FROM warehouse w ORDER BY w.id"""
        ).fetchall()
    return render_template("warehouse_list.html", warehouses=warehouses)


@app.route("/warehouse/new", methods=["GET", "POST"])
@role_required("admin")
def warehouse_new():
    if request.method == "POST":
        try:
            with database.get_conn() as conn:
                conn.execute(
                    "INSERT INTO warehouse (name, address) VALUES (?, ?)",
                    (request.form["name"].strip(), request.form.get("address", "").strip()),
                )
            flash("仓库创建成功", "success")
            return redirect(url_for("warehouse_list"))
        except sqlite3.IntegrityError:
            flash("仓库名已存在", "error")
    return render_template("warehouse_form.html", warehouse=None)


@app.route("/warehouse/<int:warehouse_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def warehouse_edit(warehouse_id):
    with database.get_conn() as conn:
        warehouse = conn.execute("SELECT * FROM warehouse WHERE id = ?", (warehouse_id,)).fetchone()
        if not warehouse:
            flash("仓库不存在", "error")
            return redirect(url_for("warehouse_list"))
        if request.method == "POST":
            try:
                conn.execute(
                    "UPDATE warehouse SET name=?, address=? WHERE id=?",
                    (request.form["name"].strip(), request.form.get("address", "").strip(), warehouse_id),
                )
                flash("仓库已更新", "success")
                return redirect(url_for("warehouse_list"))
            except sqlite3.IntegrityError:
                flash("仓库名冲突", "error")
    return render_template("warehouse_form.html", warehouse=warehouse)


# ============ 库位 ============

@app.route("/location")
@login_required
def location_list():
    with database.get_conn() as conn:
        locations = conn.execute(
            """SELECT l.*, w.name AS warehouse_name
               FROM location l JOIN warehouse w ON w.id = l.warehouse_id
               ORDER BY w.id, l.code"""
        ).fetchall()
    return render_template("location_list.html", locations=locations)


@app.route("/location/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def location_new():
    with database.get_conn() as conn:
        warehouses = conn.execute("SELECT * FROM warehouse ORDER BY id").fetchall()
        if request.method == "POST":
            try:
                conn.execute(
                    "INSERT INTO location (warehouse_id, code, zone_type) VALUES (?, ?, ?)",
                    (
                        int(request.form["warehouse_id"]),
                        request.form["code"].strip(),
                        request.form["zone_type"],
                    ),
                )
                flash("库位创建成功", "success")
                return redirect(url_for("location_list"))
            except sqlite3.IntegrityError:
                flash("该仓库下库位编码已存在", "error")
    return render_template("location_form.html", warehouses=warehouses)


@app.route("/location/<int:location_id>/delete", methods=["POST"])
@role_required("admin")
def location_delete(location_id):
    with database.get_conn() as conn:
        conn.execute("DELETE FROM location WHERE id = ?", (location_id,))
    flash("库位已删除", "success")
    return redirect(url_for("location_list"))


# ============ 供应商 ============

@app.route("/supplier")
@login_required
def supplier_list():
    with database.get_conn() as conn:
        suppliers = conn.execute("SELECT * FROM supplier ORDER BY id").fetchall()
    return render_template("supplier_list.html", suppliers=suppliers)


@app.route("/supplier/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def supplier_new():
    if request.method == "POST":
        try:
            with database.get_conn() as conn:
                conn.execute(
                    "INSERT INTO supplier (name, contact, phone, quality_score) VALUES (?, ?, ?, ?)",
                    (
                        request.form["name"].strip(),
                        request.form.get("contact", "").strip(),
                        request.form.get("phone", "").strip(),
                        int(request.form.get("quality_score") or 100),
                    ),
                )
            flash("供应商已添加", "success")
            return redirect(url_for("supplier_list"))
        except sqlite3.IntegrityError:
            flash("供应商名已存在", "error")
    return render_template("supplier_form.html", supplier=None)


@app.route("/supplier/<int:supplier_id>/edit", methods=["GET", "POST"])
@role_required("admin", "manager")
def supplier_edit(supplier_id):
    with database.get_conn() as conn:
        supplier = conn.execute("SELECT * FROM supplier WHERE id = ?", (supplier_id,)).fetchone()
        if not supplier:
            flash("供应商不存在", "error")
            return redirect(url_for("supplier_list"))
        if request.method == "POST":
            try:
                conn.execute(
                    "UPDATE supplier SET name=?, contact=?, phone=?, quality_score=? WHERE id=?",
                    (
                        request.form["name"].strip(),
                        request.form.get("contact", "").strip(),
                        request.form.get("phone", "").strip(),
                        int(request.form.get("quality_score") or 100),
                        supplier_id,
                    ),
                )
                flash("供应商已更新", "success")
                return redirect(url_for("supplier_list"))
            except sqlite3.IntegrityError:
                flash("供应商名冲突", "error")
    return render_template("supplier_form.html", supplier=supplier)


# ============ 客户 ============

@app.route("/customer")
@login_required
def customer_list():
    with database.get_conn() as conn:
        customers = conn.execute("SELECT * FROM customer ORDER BY id DESC").fetchall()
    return render_template("customer_list.html", customers=customers)


@app.route("/customer/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def customer_new():
    if request.method == "POST":
        try:
            with database.get_conn() as conn:
                conn.execute(
                    "INSERT INTO customer (phone, name, default_address) VALUES (?, ?, ?)",
                    (
                        request.form["phone"].strip(),
                        request.form.get("name", "").strip(),
                        request.form.get("default_address", "").strip(),
                    ),
                )
            flash("客户已添加", "success")
            return redirect(url_for("customer_list"))
        except sqlite3.IntegrityError:
            flash("该手机号已存在", "error")
    return render_template("customer_form.html", customer=None)


@app.route("/customer/<int:customer_id>/edit", methods=["GET", "POST"])
@role_required("admin", "manager")
def customer_edit(customer_id):
    with database.get_conn() as conn:
        customer = conn.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            flash("客户不存在", "error")
            return redirect(url_for("customer_list"))
        if request.method == "POST":
            try:
                conn.execute(
                    "UPDATE customer SET phone=?, name=?, default_address=? WHERE id=?",
                    (
                        request.form["phone"].strip(),
                        request.form.get("name", "").strip(),
                        request.form.get("default_address", "").strip(),
                        customer_id,
                    ),
                )
                flash("客户已更新", "success")
                return redirect(url_for("customer_list"))
            except sqlite3.IntegrityError:
                flash("手机号冲突", "error")
    return render_template("customer_form.html", customer=customer)


# ============ 入库单 ============

@app.route("/inbound")
@login_required
def inbound_list():
    with database.get_conn() as conn:
        orders = conn.execute(
            """SELECT o.*, s.name AS supplier_name,
                      uc.display_name AS creator_name,
                      ua.display_name AS approver_name,
                      (SELECT COALESCE(SUM(quantity), 0) FROM inbound_item WHERE order_no = o.order_no) AS total_qty
               FROM inbound_order o
               LEFT JOIN supplier s ON s.id = o.supplier_id
               LEFT JOIN user uc ON uc.id = o.creator_id
               LEFT JOIN user ua ON ua.id = o.approver_id
               ORDER BY o.id DESC"""
        ).fetchall()
    return render_template("inbound_list.html", orders=orders)


@app.route("/inbound/new", methods=["GET", "POST"])
@login_required
def inbound_new():
    with database.get_conn() as conn:
        suppliers = conn.execute("SELECT * FROM supplier ORDER BY id").fetchall()
        skus = conn.execute("SELECT * FROM sku ORDER BY code").fetchall()
        locations = conn.execute(
            "SELECT id, code, zone_type FROM location WHERE zone_type IN ('normal','cold','freeze') ORDER BY code"
        ).fetchall()

        if request.method == "POST":
            supplier_id = int(request.form["supplier_id"])
            note = request.form.get("note", "").strip()

            sku_ids = request.form.getlist("sku_id[]")
            batch_nos = request.form.getlist("batch_no[]")
            prod_dates = request.form.getlist("production_date[]")
            exp_dates = request.form.getlist("expiry_date[]")
            loc_ids = request.form.getlist("location_id[]")
            quantities = request.form.getlist("quantity[]")
            unit_prices = request.form.getlist("unit_price[]")

            valid_rows = []
            for i in range(len(sku_ids)):
                if not sku_ids[i] or not quantities[i]:
                    continue
                try:
                    qty = int(quantities[i])
                    if qty <= 0:
                        continue
                except ValueError:
                    continue
                if not batch_nos[i].strip() or not prod_dates[i] or not exp_dates[i] or not loc_ids[i]:
                    flash(f"第 {i+1} 行：批次号/生产日期/效期/库位不能为空", "error")
                    return render_template("inbound_form.html", suppliers=suppliers, skus=skus, locations=locations)
                valid_rows.append({
                    "sku_id": int(sku_ids[i]),
                    "batch_no": batch_nos[i].strip(),
                    "production_date": prod_dates[i],
                    "expiry_date": exp_dates[i],
                    "location_id": int(loc_ids[i]),
                    "quantity": qty,
                    "unit_price": float(unit_prices[i] or 0),
                })

            if not valid_rows:
                flash("至少要填一行明细", "error")
                return render_template("inbound_form.html", suppliers=suppliers, skus=skus, locations=locations)

            order_no = database.gen_order_no(conn, "RKD", "inbound_order")
            conn.execute(
                "INSERT INTO inbound_order (order_no, supplier_id, status, creator_id, note) VALUES (?, ?, 'draft', ?, ?)",
                (order_no, supplier_id, session["user_id"], note),
            )
            for r in valid_rows:
                conn.execute(
                    "INSERT INTO inbound_item (order_no, sku_id, batch_no, production_date, expiry_date, location_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (order_no, r["sku_id"], r["batch_no"], r["production_date"], r["expiry_date"], r["location_id"], r["quantity"], r["unit_price"]),
                )
            flash(f"入库单 {order_no} 已创建（草稿，待审核）", "success")
            return redirect(url_for("inbound_detail", order_no=order_no))

    return render_template("inbound_form.html", suppliers=suppliers, skus=skus, locations=locations)


@app.route("/inbound/<order_no>")
@login_required
def inbound_detail(order_no):
    with database.get_conn() as conn:
        order = conn.execute(
            """SELECT o.*, s.name AS supplier_name,
                      uc.display_name AS creator_name,
                      ua.display_name AS approver_name
               FROM inbound_order o
               LEFT JOIN supplier s ON s.id = o.supplier_id
               LEFT JOIN user uc ON uc.id = o.creator_id
               LEFT JOIN user ua ON ua.id = o.approver_id
               WHERE o.order_no = ?""",
            (order_no,),
        ).fetchone()
        if not order:
            flash("入库单不存在", "error")
            return redirect(url_for("inbound_list"))
        items = conn.execute(
            """SELECT i.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      l.code AS location_code
               FROM inbound_item i
               JOIN sku s ON s.id = i.sku_id
               JOIN location l ON l.id = i.location_id
               WHERE i.order_no = ?
               ORDER BY i.id""",
            (order_no,),
        ).fetchall()
    return render_template("inbound_detail.html", order=order, items=items)


@app.route("/inbound/<order_no>/approve", methods=["POST"])
@role_required("admin", "manager")
def inbound_approve(order_no):
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM inbound_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order:
            flash("入库单不存在", "error")
            return redirect(url_for("inbound_list"))
        if order["status"] != "draft":
            flash(f"只能审核草稿状态的单据（当前状态：{order['status']}）", "error")
            return redirect(url_for("inbound_detail", order_no=order_no))

        items = conn.execute("SELECT * FROM inbound_item WHERE order_no = ?", (order_no,)).fetchall()

        for item in items:
            # 1. 批次表 upsert
            existing_batch = conn.execute("SELECT id FROM batch WHERE batch_no = ?", (item["batch_no"],)).fetchone()
            if existing_batch:
                batch_id = existing_batch["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO batch (batch_no, sku_id, production_date, expiry_date, supplier_id, inbound_order_no) VALUES (?, ?, ?, ?, ?, ?)",
                    (item["batch_no"], item["sku_id"], item["production_date"], item["expiry_date"], order["supplier_id"], order_no),
                )
                batch_id = cur.lastrowid

            # 2. 反填 inbound_item.batch_id（追溯用）
            conn.execute("UPDATE inbound_item SET batch_id = ? WHERE id = ?", (batch_id, item["id"]))

            # 3. 先写流水
            conn.execute(
                "INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id) VALUES (?, ?, ?, ?, ?, 'inbound', ?)",
                (item["sku_id"], batch_id, item["location_id"], item["quantity"], order_no, session["user_id"]),
            )

            # 4. 再更新快照（upsert）
            existing_inv = conn.execute(
                "SELECT id FROM inventory WHERE sku_id = ? AND location_id = ? AND batch_id = ?",
                (item["sku_id"], item["location_id"], batch_id),
            ).fetchone()
            if existing_inv:
                conn.execute(
                    "UPDATE inventory SET on_hand = on_hand + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (item["quantity"], existing_inv["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO inventory (sku_id, location_id, batch_id, on_hand) VALUES (?, ?, ?, ?)",
                    (item["sku_id"], item["location_id"], batch_id, item["quantity"]),
                )

        # 5. 单据状态
        conn.execute(
            "UPDATE inbound_order SET status='approved', approver_id=?, approved_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (session["user_id"], order_no),
        )

    flash(f"入库单 {order_no} 审核通过，库存已更新", "success")
    return redirect(url_for("inbound_detail", order_no=order_no))


@app.route("/inbound/<order_no>/reject", methods=["POST"])
@role_required("admin", "manager")
def inbound_reject(order_no):
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM inbound_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order or order["status"] != "draft":
            flash("只能驳回草稿单", "error")
            return redirect(url_for("inbound_list"))
        conn.execute(
            "UPDATE inbound_order SET status='rejected', approver_id=?, approved_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (session["user_id"], order_no),
        )
    flash(f"入库单 {order_no} 已驳回", "success")
    return redirect(url_for("inbound_detail", order_no=order_no))


# ============ 库存 ============

@app.route("/inventory")
@login_required
def inventory_list():
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id AS sku_id, s.code, s.name, s.spec, s.unit, s.storage_zone, s.safety_stock,
                      COALESCE(SUM(i.on_hand), 0) AS total_on_hand,
                      COALESCE(SUM(i.reserved), 0) AS total_reserved,
                      COUNT(DISTINCT i.batch_id) AS batch_count
               FROM sku s
               LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.id
               ORDER BY s.code"""
        ).fetchall()
    return render_template("inventory_list.html", rows=rows)


@app.route("/inventory/by-batch")
@login_required
def inventory_by_batch():
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, b.production_date, b.expiry_date,
                      l.code AS location_code, l.zone_type
               FROM inventory i
               JOIN sku s ON s.id = i.sku_id
               JOIN batch b ON b.id = i.batch_id
               JOIN location l ON l.id = i.location_id
               WHERE i.on_hand > 0
               ORDER BY s.code, b.expiry_date ASC"""
        ).fetchall()
    return render_template("inventory_by_batch.html", rows=rows)


# ============ 销售订单 ============

class StockNotEnoughError(Exception):
    pass


def _allocate_fifo(conn, sku_id, qty_needed):
    """FIFO 算法：按效期升序找批次，返回 [(batch_id, location_id, take_qty), ...]"""
    batches = conn.execute(
        """SELECT inv.id AS inv_id, inv.batch_id, inv.location_id,
                  inv.on_hand, inv.reserved, b.expiry_date
           FROM inventory inv
           JOIN batch b ON b.id = inv.batch_id
           WHERE inv.sku_id = ? AND (inv.on_hand - inv.reserved) > 0
           ORDER BY b.expiry_date ASC, inv.id ASC""",
        (sku_id,),
    ).fetchall()
    allocations = []
    remaining = qty_needed
    for row in batches:
        if remaining <= 0:
            break
        available = row["on_hand"] - row["reserved"]
        take = min(remaining, available)
        allocations.append((row["batch_id"], row["location_id"], take, row["inv_id"]))
        remaining -= take
    if remaining > 0:
        return None
    return allocations


@app.route("/sales")
@login_required
def sales_list():
    with database.get_conn() as conn:
        orders = conn.execute(
            """SELECT so.*, c.phone AS customer_phone, c.name AS customer_name,
                      uc.display_name AS creator_name,
                      (SELECT COUNT(*) FROM sales_order_item WHERE order_no = so.order_no) AS line_count,
                      (SELECT COALESCE(SUM(quantity), 0) FROM sales_order_item WHERE order_no = so.order_no) AS total_qty
               FROM sales_order so
               LEFT JOIN customer c ON c.id = so.customer_id
               LEFT JOIN user uc ON uc.id = so.creator_id
               ORDER BY so.id DESC"""
        ).fetchall()
    return render_template("sales_list.html", orders=orders)


@app.route("/sales/new", methods=["GET", "POST"])
@login_required
def sales_new():
    with database.get_conn() as conn:
        customers = conn.execute("SELECT * FROM customer ORDER BY id DESC").fetchall()
        skus = conn.execute("SELECT * FROM sku ORDER BY code").fetchall()

        if request.method == "POST":
            customer_id = int(request.form["customer_id"])
            channel = request.form.get("channel", "manual")
            platform_no = request.form.get("platform_order_no", "").strip()
            receiver_name = request.form.get("receiver_name", "").strip()
            receiver_phone = request.form.get("receiver_phone", "").strip()
            receiver_addr = request.form.get("receiver_addr", "").strip()
            note = request.form.get("note", "").strip()

            sku_ids = request.form.getlist("sku_id[]")
            quantities = request.form.getlist("quantity[]")
            unit_prices = request.form.getlist("unit_price[]")

            valid_rows = []
            for i in range(len(sku_ids)):
                if not sku_ids[i] or not quantities[i]:
                    continue
                try:
                    qty = int(quantities[i])
                    if qty <= 0:
                        continue
                except ValueError:
                    continue
                valid_rows.append({
                    "sku_id": int(sku_ids[i]),
                    "quantity": qty,
                    "unit_price": float(unit_prices[i] or 0),
                })

            if not valid_rows:
                flash("至少要填一行商品明细", "error")
                return render_template("sales_form.html", customers=customers, skus=skus)

            # 如果未填收件人，用客户默认地址
            if customer_id and not receiver_addr:
                cust = conn.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
                if cust:
                    receiver_name = receiver_name or cust["name"]
                    receiver_phone = receiver_phone or cust["phone"]
                    receiver_addr = receiver_addr or cust["default_address"]

            total = sum(r["quantity"] * r["unit_price"] for r in valid_rows)
            order_no = database.gen_order_no(conn, "XSD", "sales_order")
            conn.execute(
                """INSERT INTO sales_order (order_no, channel, platform_order_no, customer_id, total_amount,
                                            status, receiver_name, receiver_phone, receiver_addr, creator_id, note)
                   VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?)""",
                (order_no, channel, platform_no, customer_id, total,
                 receiver_name, receiver_phone, receiver_addr, session["user_id"], note),
            )
            for r in valid_rows:
                conn.execute(
                    "INSERT INTO sales_order_item (order_no, sku_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
                    (order_no, r["sku_id"], r["quantity"], r["unit_price"]),
                )
            flash(f"销售订单 {order_no} 已创建，状态：新建。下一步点击确认进行库存预占。", "success")
            return redirect(url_for("sales_detail", order_no=order_no))

    return render_template("sales_form.html", customers=customers, skus=skus)


@app.route("/sales/<order_no>")
@login_required
def sales_detail(order_no):
    with database.get_conn() as conn:
        order = conn.execute(
            """SELECT so.*, c.phone AS customer_phone, c.name AS customer_name,
                      uc.display_name AS creator_name
               FROM sales_order so
               LEFT JOIN customer c ON c.id = so.customer_id
               LEFT JOIN user uc ON uc.id = so.creator_id
               WHERE so.order_no = ?""",
            (order_no,),
        ).fetchone()
        if not order:
            flash("销售单不存在", "error")
            return redirect(url_for("sales_list"))
        items = conn.execute(
            """SELECT si.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, s.unit AS sku_unit
               FROM sales_order_item si JOIN sku s ON s.id = si.sku_id
               WHERE si.order_no = ? ORDER BY si.id""",
            (order_no,),
        ).fetchall()
        # 关联出库单
        outbounds = conn.execute(
            """SELECT ob.*, up.display_name AS picker_name
               FROM outbound_order ob LEFT JOIN user up ON up.id = ob.picker_id
               WHERE ob.sales_order_no = ? ORDER BY ob.id""",
            (order_no,),
        ).fetchall()
        outbound_items = {}
        for ob in outbounds:
            ob_items = conn.execute(
                """SELECT oi.*, s.code AS sku_code, s.name AS sku_name,
                          b.batch_no, b.expiry_date, l.code AS location_code
                   FROM outbound_item oi
                   JOIN sku s ON s.id = oi.sku_id
                   JOIN batch b ON b.id = oi.batch_id
                   JOIN location l ON l.id = oi.location_id
                   WHERE oi.order_no = ? ORDER BY oi.id""",
                (ob["order_no"],),
            ).fetchall()
            outbound_items[ob["order_no"]] = ob_items
    return render_template("sales_detail.html", order=order, items=items, outbounds=outbounds, outbound_items=outbound_items)


@app.route("/sales/<order_no>/confirm", methods=["POST"])
@login_required
def sales_confirm(order_no):
    """确认销售单 → 校验库存 → 预占 + FIFO 派批次 → 生成出库单（拣货任务）"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM sales_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order:
            flash("销售单不存在", "error")
            return redirect(url_for("sales_list"))
        if order["status"] != "new":
            flash(f"只能确认新建状态的销售单（当前：{order['status']}）", "error")
            return redirect(url_for("sales_detail", order_no=order_no))

        items = conn.execute("SELECT * FROM sales_order_item WHERE order_no = ?", (order_no,)).fetchall()

        # 1. 先全量校验 + FIFO 分配（任一不足则整单失败，全有再下手）
        plan = []
        for item in items:
            allocations = _allocate_fifo(conn, item["sku_id"], item["quantity"])
            if allocations is None:
                sku = conn.execute("SELECT code, name FROM sku WHERE id = ?", (item["sku_id"],)).fetchone()
                flash(f"库存不足：{sku['code']} {sku['name']} 需要 {item['quantity']}", "error")
                return redirect(url_for("sales_detail", order_no=order_no))
            plan.append((item, allocations))

        # 2. 生成出库单
        outbound_no = database.gen_order_no(conn, "CKD", "outbound_order")
        conn.execute(
            "INSERT INTO outbound_order (order_no, sales_order_no, status) VALUES (?, ?, 'pending')",
            (outbound_no, order_no),
        )

        # 3. 执行预占 + 写出库明细
        for item, allocations in plan:
            for batch_id, location_id, take, inv_id in allocations:
                conn.execute(
                    "UPDATE inventory SET reserved = reserved + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (take, inv_id),
                )
                conn.execute(
                    "INSERT INTO outbound_item (order_no, sku_id, batch_id, location_id, quantity) VALUES (?, ?, ?, ?, ?)",
                    (outbound_no, item["sku_id"], batch_id, location_id, take),
                )

        # 4. 更新销售单
        conn.execute(
            "UPDATE sales_order SET status='reserved', updated_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (order_no,),
        )

    flash(f"销售单 {order_no} 已确认，库存已预占。拣货任务 {outbound_no} 已派单。", "success")
    return redirect(url_for("sales_detail", order_no=order_no))


@app.route("/sales/<order_no>/cancel", methods=["POST"])
@login_required
def sales_cancel(order_no):
    """取消销售单 → 释放预占"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM sales_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order:
            flash("销售单不存在", "error")
            return redirect(url_for("sales_list"))
        if order["status"] in ("completed", "cancelled"):
            flash("已完成或已取消的单据不能再取消", "error")
            return redirect(url_for("sales_detail", order_no=order_no))

        # 找出 pending 的出库单，释放预占
        outbound = conn.execute(
            "SELECT * FROM outbound_order WHERE sales_order_no = ? AND status = 'pending'",
            (order_no,),
        ).fetchone()
        if outbound:
            ob_items = conn.execute(
                "SELECT * FROM outbound_item WHERE order_no = ?",
                (outbound["order_no"],),
            ).fetchall()
            for it in ob_items:
                conn.execute(
                    "UPDATE inventory SET reserved = reserved - ?, updated_at = CURRENT_TIMESTAMP WHERE sku_id=? AND batch_id=? AND location_id=?",
                    (it["quantity"], it["sku_id"], it["batch_id"], it["location_id"]),
                )
            conn.execute(
                "UPDATE outbound_order SET status='cancelled' WHERE order_no=?",
                (outbound["order_no"],),
            )

        conn.execute(
            "UPDATE sales_order SET status='cancelled', updated_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (order_no,),
        )

    flash(f"销售单 {order_no} 已取消，预占库存已释放", "success")
    return redirect(url_for("sales_detail", order_no=order_no))


# ============ 拣货 ============

@app.route("/pick")
@login_required
def pick_list():
    with database.get_conn() as conn:
        orders = conn.execute(
            """SELECT ob.*, so.receiver_name, so.receiver_phone,
                      up.display_name AS picker_name,
                      (SELECT COUNT(*) FROM outbound_item WHERE order_no = ob.order_no) AS line_count,
                      (SELECT COALESCE(SUM(quantity),0) FROM outbound_item WHERE order_no = ob.order_no) AS total_qty
               FROM outbound_order ob
               JOIN sales_order so ON so.order_no = ob.sales_order_no
               LEFT JOIN user up ON up.id = ob.picker_id
               ORDER BY (CASE ob.status WHEN 'pending' THEN 0 ELSE 1 END), ob.id DESC"""
        ).fetchall()
    return render_template("pick_list.html", orders=orders)


@app.route("/pick/<order_no>")
@login_required
def pick_detail(order_no):
    with database.get_conn() as conn:
        ob = conn.execute(
            """SELECT ob.*, so.receiver_name, so.receiver_phone, so.receiver_addr,
                      up.display_name AS picker_name
               FROM outbound_order ob
               JOIN sales_order so ON so.order_no = ob.sales_order_no
               LEFT JOIN user up ON up.id = ob.picker_id
               WHERE ob.order_no = ?""",
            (order_no,),
        ).fetchone()
        if not ob:
            flash("拣货任务不存在", "error")
            return redirect(url_for("pick_list"))
        items = conn.execute(
            """SELECT oi.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, b.expiry_date, b.production_date,
                      l.code AS location_code, l.zone_type
               FROM outbound_item oi
               JOIN sku s ON s.id = oi.sku_id
               JOIN batch b ON b.id = oi.batch_id
               JOIN location l ON l.id = oi.location_id
               WHERE oi.order_no = ?
               ORDER BY l.code, b.expiry_date""",
            (order_no,),
        ).fetchall()
    return render_template("pick_detail.html", ob=ob, items=items)


@app.route("/pick/<order_no>/complete", methods=["POST"])
@login_required
def pick_complete(order_no):
    """拣货完成 → 实扣库存 + 写流水 + 销售单转完成"""
    with database.get_conn() as conn:
        ob = conn.execute("SELECT * FROM outbound_order WHERE order_no = ?", (order_no,)).fetchone()
        if not ob:
            flash("拣货任务不存在", "error")
            return redirect(url_for("pick_list"))
        if ob["status"] != "pending":
            flash(f"该任务已经是 {ob['status']} 状态，不能重复完成", "error")
            return redirect(url_for("pick_detail", order_no=order_no))

        items = conn.execute("SELECT * FROM outbound_item WHERE order_no = ?", (order_no,)).fetchall()
        for it in items:
            # 先写流水（负数 = 出库）
            conn.execute(
                """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id)
                   VALUES (?, ?, ?, ?, ?, 'outbound', ?)""",
                (it["sku_id"], it["batch_id"], it["location_id"], -it["quantity"], order_no, session["user_id"]),
            )
            # 实扣：on_hand -= N, reserved -= N（同时把预占也释放）
            conn.execute(
                """UPDATE inventory
                   SET on_hand = on_hand - ?, reserved = reserved - ?, updated_at = CURRENT_TIMESTAMP
                   WHERE sku_id = ? AND batch_id = ? AND location_id = ?""",
                (it["quantity"], it["quantity"], it["sku_id"], it["batch_id"], it["location_id"]),
            )

        conn.execute(
            "UPDATE outbound_order SET status='completed', picker_id=?, completed_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (session["user_id"], order_no),
        )
        conn.execute(
            "UPDATE sales_order SET status='completed', updated_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (ob["sales_order_no"],),
        )

    flash(f"拣货任务 {order_no} 已完成，库存已实扣，销售单已完成", "success")
    return redirect(url_for("pick_detail", order_no=order_no))


# ============ 报损 ============

DAMAGE_REASON_LABEL = {
    "broken": "破损",
    "expired": "过期",
    "lost": "丢失",
    "stolen": "被盗",
    "other": "其他",
}


@app.route("/damage")
@login_required
def damage_list():
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT d.*, s.code AS sku_code, s.name AS sku_name,
                      b.batch_no, l.code AS location_code,
                      ua.display_name AS applicant_name,
                      uv.display_name AS approver_name
               FROM damage_log d
               JOIN sku s ON s.id = d.sku_id
               JOIN batch b ON b.id = d.batch_id
               JOIN location l ON l.id = d.location_id
               LEFT JOIN user ua ON ua.id = d.applicant_id
               LEFT JOIN user uv ON uv.id = d.approver_id
               ORDER BY d.id DESC"""
        ).fetchall()
    return render_template("damage_list.html", rows=rows, reason_label=DAMAGE_REASON_LABEL)


@app.route("/damage/new", methods=["GET", "POST"])
@login_required
def damage_new():
    with database.get_conn() as conn:
        # 只能对有库存的批次报损
        invs = conn.execute(
            """SELECT inv.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, b.expiry_date, l.code AS location_code, l.zone_type
               FROM inventory inv
               JOIN sku s ON s.id = inv.sku_id
               JOIN batch b ON b.id = inv.batch_id
               JOIN location l ON l.id = inv.location_id
               WHERE inv.on_hand > 0
               ORDER BY s.code, b.expiry_date""",
        ).fetchall()

        if request.method == "POST":
            inv_id = int(request.form["inv_id"])
            inv = conn.execute("SELECT * FROM inventory WHERE id = ?", (inv_id,)).fetchone()
            if not inv:
                flash("库存记录不存在", "error")
                return render_template("damage_form.html", invs=invs, reasons=DAMAGE_REASON_LABEL)
            try:
                qty = int(request.form["quantity"])
            except ValueError:
                qty = 0
            if qty <= 0:
                flash("数量必须 > 0", "error")
                return render_template("damage_form.html", invs=invs, reasons=DAMAGE_REASON_LABEL)
            if qty > inv["on_hand"]:
                flash(f"报损数量 {qty} 超过该批次在仓数 {inv['on_hand']}", "error")
                return render_template("damage_form.html", invs=invs, reasons=DAMAGE_REASON_LABEL)
            reason_type = request.form["reason_type"]
            reason_note = request.form.get("reason_note", "").strip()
            photo_note = request.form.get("photo_note", "").strip()

            conn.execute(
                """INSERT INTO damage_log (sku_id, batch_id, location_id, quantity, reason_type, reason_note, photo_note, applicant_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (inv["sku_id"], inv["batch_id"], inv["location_id"], qty, reason_type, reason_note, photo_note, session["user_id"]),
            )
            flash("报损申请已提交，等待主管审批", "success")
            return redirect(url_for("damage_list"))

    return render_template("damage_form.html", invs=invs, reasons=DAMAGE_REASON_LABEL)


@app.route("/damage/<int:damage_id>/approve", methods=["POST"])
@role_required("admin", "manager")
def damage_approve(damage_id):
    with database.get_conn() as conn:
        d = conn.execute("SELECT * FROM damage_log WHERE id = ?", (damage_id,)).fetchone()
        if not d:
            flash("报损记录不存在", "error")
            return redirect(url_for("damage_list"))
        if d["status"] != "pending":
            flash(f"只能审批 pending 状态（当前：{d['status']}）", "error")
            return redirect(url_for("damage_list"))

        # 再次校验库存够不够（防止并发）
        inv = conn.execute(
            "SELECT * FROM inventory WHERE sku_id=? AND batch_id=? AND location_id=?",
            (d["sku_id"], d["batch_id"], d["location_id"]),
        ).fetchone()
        if not inv or inv["on_hand"] < d["quantity"]:
            flash("库存不足，无法报损（可能已被其他操作扣减）", "error")
            return redirect(url_for("damage_list"))

        # 写流水 + 扣库存
        conn.execute(
            """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id, note)
               VALUES (?, ?, ?, ?, ?, 'damage', ?, ?)""",
            (d["sku_id"], d["batch_id"], d["location_id"], -d["quantity"], f"DMG-{damage_id}", session["user_id"], DAMAGE_REASON_LABEL[d["reason_type"]]),
        )
        conn.execute(
            "UPDATE inventory SET on_hand = on_hand - ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (d["quantity"], inv["id"]),
        )
        conn.execute(
            "UPDATE damage_log SET status='approved', approver_id=?, approved_at=CURRENT_TIMESTAMP WHERE id=?",
            (session["user_id"], damage_id),
        )
    flash(f"报损 #{damage_id} 已审批通过，库存已扣减", "success")
    return redirect(url_for("damage_list"))


@app.route("/damage/<int:damage_id>/reject", methods=["POST"])
@role_required("admin", "manager")
def damage_reject(damage_id):
    with database.get_conn() as conn:
        d = conn.execute("SELECT * FROM damage_log WHERE id = ?", (damage_id,)).fetchone()
        if not d or d["status"] != "pending":
            flash("只能驳回 pending 状态的报损", "error")
            return redirect(url_for("damage_list"))
        conn.execute(
            "UPDATE damage_log SET status='rejected', approver_id=?, approved_at=CURRENT_TIMESTAMP WHERE id=?",
            (session["user_id"], damage_id),
        )
    flash(f"报损 #{damage_id} 已驳回", "success")
    return redirect(url_for("damage_list"))


# ============ 临期预警 ============

@app.route("/expiry-alert")
@login_required
def expiry_alert():
    from datetime import date, timedelta
    today = date.today()
    threshold_warn = (today + timedelta(days=30)).isoformat()
    today_iso = today.isoformat()

    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT inv.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, b.production_date, b.expiry_date,
                      l.code AS location_code, l.zone_type,
                      CAST(julianday(b.expiry_date) - julianday(?) AS INTEGER) AS days_left
               FROM inventory inv
               JOIN batch b ON b.id = inv.batch_id
               JOIN sku s ON s.id = inv.sku_id
               JOIN location l ON l.id = inv.location_id
               WHERE inv.on_hand > 0 AND b.expiry_date <= ?
               ORDER BY b.expiry_date ASC""",
            (today_iso, threshold_warn),
        ).fetchall()

    expired = [r for r in rows if r["days_left"] < 0]
    urgent = [r for r in rows if 0 <= r["days_left"] <= 7]
    warning = [r for r in rows if r["days_left"] > 7]

    return render_template("expiry_alert.html", expired=expired, urgent=urgent, warning=warning, today=today_iso)


# ============ 盘点 ============

@app.route("/stocktake")
@login_required
def stocktake_list():
    with database.get_conn() as conn:
        orders = conn.execute(
            """SELECT st.*, uc.display_name AS creator_name, ucl.display_name AS closer_name,
                      (SELECT COUNT(*) FROM stocktake_item WHERE order_no = st.order_no) AS line_count,
                      (SELECT COUNT(*) FROM stocktake_item WHERE order_no = st.order_no AND actual_qty IS NOT NULL) AS counted_count,
                      (SELECT COUNT(*) FROM stocktake_item WHERE order_no = st.order_no AND actual_qty IS NOT NULL AND actual_qty != expected_qty) AS diff_count
               FROM stocktake_order st
               LEFT JOIN user uc ON uc.id = st.creator_id
               LEFT JOIN user ucl ON ucl.id = st.closer_id
               ORDER BY st.id DESC"""
        ).fetchall()
    return render_template("stocktake_list.html", orders=orders)


@app.route("/stocktake/new", methods=["POST"])
@role_required("admin", "manager")
def stocktake_new():
    """创建盘点单 = 对当前所有 on_hand > 0 的 inventory 做快照"""
    from datetime import datetime as dt
    note = request.form.get("note", "").strip()
    with database.get_conn() as conn:
        order_no = database.gen_order_no(conn, "PDD", "stocktake_order")
        conn.execute(
            "INSERT INTO stocktake_order (order_no, scope, status, creator_id, note) VALUES (?, 'all', 'open', ?, ?)",
            (order_no, session["user_id"], note),
        )
        # 快照所有有库存的批次×库位
        snapshots = conn.execute(
            "SELECT sku_id, batch_id, location_id, on_hand FROM inventory WHERE on_hand > 0",
        ).fetchall()
        for s in snapshots:
            conn.execute(
                """INSERT INTO stocktake_item (order_no, sku_id, batch_id, location_id, expected_qty)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_no, s["sku_id"], s["batch_id"], s["location_id"], s["on_hand"]),
            )
    flash(f"盘点单 {order_no} 已创建，含 {len(snapshots)} 行待盘点。请录入实物数量。", "success")
    return redirect(url_for("stocktake_detail", order_no=order_no))


@app.route("/stocktake/<order_no>")
@login_required
def stocktake_detail(order_no):
    with database.get_conn() as conn:
        order = conn.execute(
            """SELECT st.*, uc.display_name AS creator_name, ucl.display_name AS closer_name
               FROM stocktake_order st
               LEFT JOIN user uc ON uc.id = st.creator_id
               LEFT JOIN user ucl ON ucl.id = st.closer_id
               WHERE st.order_no = ?""",
            (order_no,),
        ).fetchone()
        if not order:
            flash("盘点单不存在", "error")
            return redirect(url_for("stocktake_list"))
        items = conn.execute(
            """SELECT sti.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, b.expiry_date, l.code AS location_code, l.zone_type
               FROM stocktake_item sti
               JOIN sku s ON s.id = sti.sku_id
               JOIN batch b ON b.id = sti.batch_id
               JOIN location l ON l.id = sti.location_id
               WHERE sti.order_no = ?
               ORDER BY l.code, s.code, b.expiry_date""",
            (order_no,),
        ).fetchall()
    return render_template("stocktake_detail.html", order=order, items=items)


@app.route("/stocktake/<order_no>/save", methods=["POST"])
@login_required
def stocktake_save(order_no):
    """保存实物数量录入（不关闭单据）"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM stocktake_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order or order["status"] != "open":
            flash("只能编辑 open 状态的盘点单", "error")
            return redirect(url_for("stocktake_list"))
        item_ids = request.form.getlist("item_id[]")
        actuals = request.form.getlist("actual_qty[]")
        saved = 0
        for iid, aq in zip(item_ids, actuals):
            if not iid:
                continue
            if aq == "":
                conn.execute("UPDATE stocktake_item SET actual_qty = NULL WHERE id = ?", (int(iid),))
            else:
                try:
                    a = int(aq)
                    if a < 0:
                        continue
                    conn.execute("UPDATE stocktake_item SET actual_qty = ? WHERE id = ?", (a, int(iid)))
                    saved += 1
                except ValueError:
                    continue
    flash(f"已保存 {saved} 行盘点数据", "success")
    return redirect(url_for("stocktake_detail", order_no=order_no))


@app.route("/stocktake/<order_no>/close", methods=["POST"])
@role_required("admin", "manager")
def stocktake_close(order_no):
    """关闭盘点 → 差异生成 adjust 流水 + 更新 inventory"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM stocktake_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order or order["status"] != "open":
            flash("只能关闭 open 状态的盘点单", "error")
            return redirect(url_for("stocktake_list"))
        items = conn.execute(
            "SELECT * FROM stocktake_item WHERE order_no = ?",
            (order_no,),
        ).fetchall()
        # 还有没录的强制设为 expected（即默认账实一致）
        adjusts = 0
        for it in items:
            actual = it["actual_qty"] if it["actual_qty"] is not None else it["expected_qty"]
            diff = actual - it["expected_qty"]
            if diff != 0:
                conn.execute(
                    """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id, note)
                       VALUES (?, ?, ?, ?, ?, 'adjust', ?, ?)""",
                    (it["sku_id"], it["batch_id"], it["location_id"], diff, order_no, session["user_id"],
                     f"盘点调整（账：{it['expected_qty']}，实：{actual}）"),
                )
                conn.execute(
                    "UPDATE inventory SET on_hand = on_hand + ?, updated_at=CURRENT_TIMESTAMP WHERE sku_id=? AND batch_id=? AND location_id=?",
                    (diff, it["sku_id"], it["batch_id"], it["location_id"]),
                )
                conn.execute("UPDATE stocktake_item SET diff_handled = 1 WHERE id = ?", (it["id"],))
                adjusts += 1
        conn.execute(
            "UPDATE stocktake_order SET status='closed', closer_id=?, closed_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (session["user_id"], order_no),
        )
    flash(f"盘点 {order_no} 已关闭，{adjusts} 条差异已写入调整流水", "success")
    return redirect(url_for("stocktake_detail", order_no=order_no))


# ============ 退换货 ============

RETURN_REASON_LABEL = {
    "quality": "商品质量问题",
    "wrong": "发错货",
    "dislike": "客户不满意",
    "damaged_in_transit": "运输破损",
    "other": "其他",
}

QC_RESULT_LABEL = {
    "good": "完好可入库",
    "downgrade": "可降级销售",
    "damaged": "已损坏销毁",
}


@app.route("/return")
@login_required
def return_list():
    with database.get_conn() as conn:
        orders = conn.execute(
            """SELECT ro.*, c.phone AS customer_phone, c.name AS customer_name,
                      ucs.display_name AS cs_name, uqc.display_name AS qc_name,
                      (SELECT COUNT(*) FROM return_item WHERE order_no = ro.order_no) AS line_count,
                      (SELECT COALESCE(SUM(quantity),0) FROM return_item WHERE order_no = ro.order_no) AS total_qty
               FROM return_order ro
               LEFT JOIN customer c ON c.id = ro.customer_id
               LEFT JOIN user ucs ON ucs.id = ro.cs_user_id
               LEFT JOIN user uqc ON uqc.id = ro.qc_user_id
               ORDER BY ro.id DESC"""
        ).fetchall()
    return render_template("return_list.html", orders=orders, reason_label=RETURN_REASON_LABEL)


@app.route("/return/new", methods=["GET", "POST"])
@login_required
def return_new():
    """客服建退货单：基于一张已完成的销售单选要退的明细行"""
    sales_no = request.args.get("sales_order_no", "").strip() or request.form.get("sales_order_no", "").strip()

    with database.get_conn() as conn:
        # 候选销售单：已完成的
        sales_orders = conn.execute(
            "SELECT order_no, customer_id, total_amount, created_at FROM sales_order WHERE status='completed' ORDER BY id DESC LIMIT 50"
        ).fetchall()

        sales = None
        outbound_items = []
        if sales_no:
            sales = conn.execute("SELECT * FROM sales_order WHERE order_no = ?", (sales_no,)).fetchone()
            if sales:
                # 找出这张销售单对应的出库明细（已发的具体批次）
                outbound_items = conn.execute(
                    """SELECT oi.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, s.unit AS sku_unit,
                              b.batch_no, b.expiry_date,
                              soi.unit_price
                       FROM outbound_item oi
                       JOIN outbound_order oo ON oo.order_no = oi.order_no
                       JOIN sku s ON s.id = oi.sku_id
                       JOIN batch b ON b.id = oi.batch_id
                       LEFT JOIN sales_order_item soi ON soi.order_no = oo.sales_order_no AND soi.sku_id = oi.sku_id
                       WHERE oo.sales_order_no = ? AND oo.status = 'completed'""",
                    (sales_no,),
                ).fetchall()

        if request.method == "POST" and sales:
            reason = request.form["reason"]
            reason_note = request.form.get("reason_note", "").strip()

            ob_item_ids = request.form.getlist("ob_item_id[]")
            return_qtys = request.form.getlist("return_qty[]")

            valid_rows = []
            for ob_id, qty_str in zip(ob_item_ids, return_qtys):
                if not ob_id or not qty_str:
                    continue
                try:
                    qty = int(qty_str)
                except ValueError:
                    continue
                if qty <= 0:
                    continue
                ob_item = next((r for r in outbound_items if str(r["id"]) == ob_id), None)
                if not ob_item:
                    continue
                if qty > ob_item["quantity"]:
                    flash(f"{ob_item['sku_code']} 退货数量 {qty} 超过原发数量 {ob_item['quantity']}", "error")
                    return render_template("return_form.html", sales_orders=sales_orders, sales=sales,
                                           outbound_items=outbound_items, reasons=RETURN_REASON_LABEL)
                valid_rows.append({
                    "sku_id": ob_item["sku_id"],
                    "batch_id": ob_item["batch_id"],
                    "quantity": qty,
                    "unit_price": ob_item["unit_price"] or 0,
                })

            if not valid_rows:
                flash("至少选一行要退的商品并填数量", "error")
                return render_template("return_form.html", sales_orders=sales_orders, sales=sales,
                                       outbound_items=outbound_items, reasons=RETURN_REASON_LABEL)

            return_no = database.gen_order_no(conn, "THD", "return_order")
            conn.execute(
                """INSERT INTO return_order (order_no, sales_order_no, customer_id, reason, reason_note, status, cs_user_id)
                   VALUES (?, ?, ?, ?, ?, 'approved', ?)""",
                (return_no, sales_no, sales["customer_id"], reason, reason_note, session["user_id"]),
            )
            for r in valid_rows:
                conn.execute(
                    """INSERT INTO return_item (order_no, sku_id, batch_id, quantity, unit_price)
                       VALUES (?, ?, ?, ?, ?)""",
                    (return_no, r["sku_id"], r["batch_id"], r["quantity"], r["unit_price"]),
                )
            flash(f"退货单 {return_no} 已创建（已通过客服审核），等待客户寄回。", "success")
            return redirect(url_for("return_detail", order_no=return_no))

    return render_template("return_form.html", sales_orders=sales_orders, sales=sales,
                           outbound_items=outbound_items, reasons=RETURN_REASON_LABEL)


@app.route("/return/<order_no>")
@login_required
def return_detail(order_no):
    with database.get_conn() as conn:
        order = conn.execute(
            """SELECT ro.*, c.phone AS customer_phone, c.name AS customer_name,
                      ucs.display_name AS cs_name, uqc.display_name AS qc_name
               FROM return_order ro
               LEFT JOIN customer c ON c.id = ro.customer_id
               LEFT JOIN user ucs ON ucs.id = ro.cs_user_id
               LEFT JOIN user uqc ON uqc.id = ro.qc_user_id
               WHERE ro.order_no = ?""",
            (order_no,),
        ).fetchone()
        if not order:
            flash("退货单不存在", "error")
            return redirect(url_for("return_list"))
        items = conn.execute(
            """SELECT ri.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, s.unit AS sku_unit,
                      b.batch_no, b.expiry_date,
                      l.code AS return_location_code, l.zone_type AS return_location_zone
               FROM return_item ri
               JOIN sku s ON s.id = ri.sku_id
               JOIN batch b ON b.id = ri.batch_id
               LEFT JOIN location l ON l.id = ri.return_location_id
               WHERE ri.order_no = ?
               ORDER BY ri.id""",
            (order_no,),
        ).fetchall()
        # 候选退入库位（仅在质检时需要）
        normal_locations = conn.execute(
            "SELECT id, code, zone_type FROM location WHERE zone_type IN ('normal','cold','freeze') ORDER BY code"
        ).fetchall()
        downgrade_locations = conn.execute(
            "SELECT id, code, zone_type FROM location WHERE zone_type='downgrade' ORDER BY code"
        ).fetchall()
    return render_template("return_detail.html", order=order, items=items,
                           reason_label=RETURN_REASON_LABEL, qc_label=QC_RESULT_LABEL,
                           normal_locations=normal_locations, downgrade_locations=downgrade_locations)


@app.route("/return/<order_no>/receive", methods=["POST"])
@login_required
def return_receive(order_no):
    """仓库收货员：标记已收到客户寄回的包裹"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM return_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order or order["status"] != "approved":
            flash("只能对 approved 状态的退货单收货", "error")
            return redirect(url_for("return_detail", order_no=order_no))
        conn.execute(
            "UPDATE return_order SET status='received', received_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (order_no,),
        )
    flash(f"已确认收到客户包裹，下一步质检员开箱判定", "success")
    return redirect(url_for("return_detail", order_no=order_no))


@app.route("/return/<order_no>/qc", methods=["POST"])
@login_required
def return_qc(order_no):
    """质检员：录入每行的 QC 三分支结果（good/downgrade/damaged）+ 退入库位"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM return_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order or order["status"] != "received":
            flash("只能对 received 状态的退货单做质检", "error")
            return redirect(url_for("return_detail", order_no=order_no))

        items = conn.execute("SELECT * FROM return_item WHERE order_no = ?", (order_no,)).fetchall()
        # 校验所有行都填了 qc_result
        for it in items:
            qc_result = request.form.get(f"qc_result_{it['id']}", "").strip()
            if qc_result not in ("good", "downgrade", "damaged"):
                flash(f"行 #{it['id']} 必须选一个质检结果", "error")
                return redirect(url_for("return_detail", order_no=order_no))
            if qc_result in ("good", "downgrade"):
                loc_id = request.form.get(f"return_location_{it['id']}", "").strip()
                if not loc_id:
                    flash(f"行 #{it['id']} 完好/降级时必须选退入库位", "error")
                    return redirect(url_for("return_detail", order_no=order_no))

        # 执行
        for it in items:
            qc_result = request.form[f"qc_result_{it['id']}"]
            loc_id = request.form.get(f"return_location_{it['id']}", "").strip()

            if qc_result == "good" or qc_result == "downgrade":
                location_id = int(loc_id)
                # 写流水（+N，return_in）
                note = "完好上架" if qc_result == "good" else "降级处理"
                conn.execute(
                    """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id, note)
                       VALUES (?, ?, ?, ?, ?, 'return_in', ?, ?)""",
                    (it["sku_id"], it["batch_id"], location_id, it["quantity"], order_no, session["user_id"], note),
                )
                # upsert inventory
                ex = conn.execute(
                    "SELECT id FROM inventory WHERE sku_id=? AND location_id=? AND batch_id=?",
                    (it["sku_id"], location_id, it["batch_id"]),
                ).fetchone()
                if ex:
                    conn.execute(
                        "UPDATE inventory SET on_hand = on_hand + ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (it["quantity"], ex["id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO inventory (sku_id, location_id, batch_id, on_hand) VALUES (?, ?, ?, ?)",
                        (it["sku_id"], location_id, it["batch_id"], it["quantity"]),
                    )
                conn.execute(
                    "UPDATE return_item SET qc_result=?, return_location_id=? WHERE id=?",
                    (qc_result, location_id, it["id"]),
                )
            elif qc_result == "damaged":
                # 不动库存，只在 return_item 留痕
                conn.execute(
                    "UPDATE return_item SET qc_result='damaged', return_location_id=NULL WHERE id=?",
                    (it["id"],),
                )

        conn.execute(
            "UPDATE return_order SET status='qc_done', qc_user_id=?, qc_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (session["user_id"], order_no),
        )
    flash(f"质检完成。完好/降级的批次已自动入库，损坏的直接销毁。下一步客服录入退款金额。", "success")
    return redirect(url_for("return_detail", order_no=order_no))


@app.route("/return/<order_no>/refund", methods=["POST"])
@login_required
def return_refund(order_no):
    """客服：录入退款金额并标记完成"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM return_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order or order["status"] != "qc_done":
            flash("只能对 qc_done 状态的退货单退款", "error")
            return redirect(url_for("return_detail", order_no=order_no))
        try:
            amount = float(request.form.get("refund_amount", "0"))
        except ValueError:
            amount = 0
        if amount < 0:
            flash("退款金额不能为负", "error")
            return redirect(url_for("return_detail", order_no=order_no))
        conn.execute(
            "UPDATE return_order SET status='refunded', refund_amount=?, refunded_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (amount, order_no),
        )
    flash(f"已标记退款 ¥{amount:.2f}，退货流程关闭", "success")
    return redirect(url_for("return_detail", order_no=order_no))


@app.route("/return/<order_no>/reject", methods=["POST"])
@role_required("admin", "manager")
def return_reject(order_no):
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM return_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order or order["status"] not in ("approved", "received"):
            flash("只能拒绝 approved/received 状态的退货单", "error")
            return redirect(url_for("return_detail", order_no=order_no))
        conn.execute(
            "UPDATE return_order SET status='rejected' WHERE order_no=?",
            (order_no,),
        )
    flash(f"退货单 {order_no} 已拒绝", "success")
    return redirect(url_for("return_detail", order_no=order_no))


# ============ 报表 Dashboard ============

@app.route("/dashboard")
@login_required
def dashboard():
    with database.get_conn() as conn:
        # 1. 销售趋势 — 最近 14 天
        sales_trend = conn.execute(
            """SELECT date(created_at) AS d,
                      COUNT(*) AS orders,
                      COALESCE(SUM(total_amount), 0) AS revenue
               FROM sales_order
               WHERE date(created_at) >= date('now', '-13 days')
               GROUP BY date(created_at)
               ORDER BY d"""
        ).fetchall()

        # 2. 库存 TOP 10 SKU
        inv_top = conn.execute(
            """SELECT s.code, s.name, COALESCE(SUM(i.on_hand), 0) AS total
               FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.id
               ORDER BY total DESC LIMIT 10"""
        ).fetchall()

        # 3. 库存按存储区
        zone_dist = conn.execute(
            """SELECT s.storage_zone AS zone, COALESCE(SUM(i.on_hand), 0) AS total
               FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.storage_zone"""
        ).fetchall()

        # 4. 流水类型分布（最近 30 天）
        log_type = conn.execute(
            """SELECT event_type, COUNT(*) AS cnt
               FROM stock_log
               WHERE date(occurred_at) >= date('now', '-29 days')
               GROUP BY event_type"""
        ).fetchall()

        # 5. 退货原因分布
        return_reason = conn.execute(
            """SELECT reason, COUNT(*) AS cnt
               FROM return_order
               WHERE reason IS NOT NULL
               GROUP BY reason
               ORDER BY cnt DESC"""
        ).fetchall()

        # 6. 临期分级
        expiry_grade = conn.execute(
            """SELECT
                 SUM(CASE WHEN b.expiry_date < date('now') THEN 1 ELSE 0 END) AS expired,
                 SUM(CASE WHEN b.expiry_date >= date('now') AND b.expiry_date <= date('now','+7 days') THEN 1 ELSE 0 END) AS urgent,
                 SUM(CASE WHEN b.expiry_date > date('now','+7 days') AND b.expiry_date <= date('now','+30 days') THEN 1 ELSE 0 END) AS warning,
                 SUM(CASE WHEN b.expiry_date > date('now','+30 days') THEN 1 ELSE 0 END) AS normal
               FROM inventory i JOIN batch b ON b.id = i.batch_id
               WHERE i.on_hand > 0"""
        ).fetchone()

        # 7. 销售 TOP 5 SKU（按数量）
        sku_top = conn.execute(
            """SELECT s.code, s.name, COALESCE(SUM(si.quantity), 0) AS qty
               FROM sales_order_item si
               JOIN sku s ON s.id = si.sku_id
               JOIN sales_order so ON so.order_no = si.order_no
               WHERE so.status IN ('reserved','completed')
               GROUP BY s.id
               ORDER BY qty DESC LIMIT 5"""
        ).fetchall()

    import json
    charts = {
        "sales_trend": {
            "labels": [r["d"] for r in sales_trend],
            "orders": [r["orders"] for r in sales_trend],
            "revenue": [round(r["revenue"], 2) for r in sales_trend],
        },
        "inv_top": {
            "labels": [f"{r['code']}" for r in inv_top],
            "data": [r["total"] for r in inv_top],
        },
        "zone_dist": {
            "labels": [{"normal":"常温","cold":"冷藏","freeze":"冷冻"}.get(r["zone"], r["zone"]) for r in zone_dist],
            "data": [r["total"] for r in zone_dist],
        },
        "log_type": {
            "labels": [{"inbound":"入库","outbound":"出库","damage":"报损","adjust":"盘点调整","return_in":"退货入","transfer":"调拨"}.get(r["event_type"], r["event_type"]) for r in log_type],
            "data": [r["cnt"] for r in log_type],
        },
        "return_reason": {
            "labels": [{"quality":"质量","wrong":"发错","dislike":"不喜欢","damaged_in_transit":"运输破损","other":"其他"}.get(r["reason"], r["reason"]) for r in return_reason],
            "data": [r["cnt"] for r in return_reason],
        },
        "expiry_grade": {
            "labels": ["已过期", "紧急(7天)", "预警(30天)", "正常"],
            "data": [expiry_grade["expired"] or 0, expiry_grade["urgent"] or 0, expiry_grade["warning"] or 0, expiry_grade["normal"] or 0],
        },
        "sku_top": {
            "labels": [r["code"] for r in sku_top],
            "data": [r["qty"] for r in sku_top],
        },
    }
    return render_template("dashboard.html", charts_json=json.dumps(charts))


# ============ AI 自然语言查询 ============

AI_PRESETS = [
    {"q": "本周销售前 5 的 SKU", "intent": "top_sales"},
    {"q": "现在库存最多的 5 个 SKU", "intent": "top_stock"},
    {"q": "临期 7 天内的批次", "intent": "expiry_urgent"},
    {"q": "已过期需要报损的库存", "intent": "expired"},
    {"q": "低于安全库存的 SKU", "intent": "low_stock"},
    {"q": "本周待审的报损单", "intent": "pending_damage"},
    {"q": "待拣货任务有几个", "intent": "pending_pick"},
    {"q": "今天的销售额", "intent": "today_revenue"},
]


def _classify_intent(query: str) -> str:
    """简化版意图分类（规则引擎）。生产环境可替换成 LLM call。
    规则：单关键词 OR 多关键词 AND（list of str = OR；tuple of str = AND）。
    """
    q = query.lower().strip()
    rules = [
        (["销售前", "销售 top", "热销", "热卖", "卖得最多"], "top_sales"),
        (["库存最多", "库存 top", "存货最多", "在仓最多"], "top_stock"),
        (["临期", "快过期", "马上过期", "7天", "七天"], "expiry_urgent"),
        (["已过期", "过期"], "expired"),
        (["安全库存", "库存不足", "低库存", "缺货"], "low_stock"),
        ([("报损", "待审"), ("待审", "报损"), "待审报损", "报损待审"], "pending_damage"),
        (["待拣货", "拣货任务"], "pending_pick"),
        ([("今天", "销售"), ("今日", "销售"), ("今天", "营业"), ("今日", "营业"), "今天销售", "今日销售"], "today_revenue"),
    ]
    for kws, intent in rules:
        for kw in kws:
            if isinstance(kw, tuple):
                if all(k in q for k in kw):
                    return intent
            else:
                if kw in q:
                    return intent
    return "unknown"


def _execute_intent(conn, intent: str):
    """执行意图，返回 (title, columns, rows)。"""
    if intent == "top_sales":
        rows = conn.execute(
            """SELECT s.code, s.name, s.spec, COALESCE(SUM(si.quantity), 0) AS 销量
               FROM sales_order_item si JOIN sku s ON s.id = si.sku_id
               JOIN sales_order so ON so.order_no = si.order_no
               WHERE so.status IN ('reserved','completed')
                 AND date(so.created_at) >= date('now', '-6 days')
               GROUP BY s.id ORDER BY 销量 DESC LIMIT 5"""
        ).fetchall()
        return ("最近 7 天销售 TOP 5", ["SKU", "商品", "规格", "销量"], [list(r) for r in rows])

    if intent == "top_stock":
        rows = conn.execute(
            """SELECT s.code, s.name, s.spec, COALESCE(SUM(i.on_hand), 0) AS 在仓数
               FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.id ORDER BY 在仓数 DESC LIMIT 5"""
        ).fetchall()
        return ("当前库存 TOP 5", ["SKU", "商品", "规格", "在仓数"], [list(r) for r in rows])

    if intent == "expiry_urgent":
        rows = conn.execute(
            """SELECT s.code, b.batch_no, b.expiry_date,
                      CAST(julianday(b.expiry_date) - julianday('now') AS INTEGER) AS 剩余天数,
                      l.code AS 库位, i.on_hand AS 在仓数
               FROM inventory i
               JOIN batch b ON b.id = i.batch_id
               JOIN sku s ON s.id = i.sku_id
               JOIN location l ON l.id = i.location_id
               WHERE i.on_hand > 0 AND b.expiry_date <= date('now','+7 days')
                 AND b.expiry_date >= date('now')
               ORDER BY b.expiry_date"""
        ).fetchall()
        return ("7 天内到期批次", ["SKU", "批次", "效期", "剩余天数", "库位", "在仓数"], [list(r) for r in rows])

    if intent == "expired":
        rows = conn.execute(
            """SELECT s.code, b.batch_no, b.expiry_date,
                      CAST(julianday('now') - julianday(b.expiry_date) AS INTEGER) AS 已过期天数,
                      l.code AS 库位, i.on_hand AS 在仓数
               FROM inventory i
               JOIN batch b ON b.id = i.batch_id
               JOIN sku s ON s.id = i.sku_id
               JOIN location l ON l.id = i.location_id
               WHERE i.on_hand > 0 AND b.expiry_date < date('now')
               ORDER BY b.expiry_date"""
        ).fetchall()
        return ("已过期未报损的库存", ["SKU", "批次", "效期", "已过期天数", "库位", "在仓数"], [list(r) for r in rows])

    if intent == "low_stock":
        rows = conn.execute(
            """SELECT s.code, s.name, s.safety_stock AS 安全库存, COALESCE(SUM(i.on_hand), 0) AS 在仓数
               FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.id
               HAVING 在仓数 < s.safety_stock AND s.safety_stock > 0
               ORDER BY (s.safety_stock - 在仓数) DESC"""
        ).fetchall()
        return ("低于安全库存的 SKU", ["SKU", "商品", "安全库存", "在仓数"], [list(r) for r in rows])

    if intent == "pending_damage":
        rows = conn.execute(
            """SELECT d.id, s.code AS SKU, b.batch_no AS 批次, l.code AS 库位, d.quantity AS 报损数,
                      u.display_name AS 申请人, d.created_at AS 提交时间
               FROM damage_log d
               JOIN sku s ON s.id = d.sku_id
               JOIN batch b ON b.id = d.batch_id
               JOIN location l ON l.id = d.location_id
               LEFT JOIN user u ON u.id = d.applicant_id
               WHERE d.status = 'pending'
               ORDER BY d.id DESC"""
        ).fetchall()
        return ("待审报损单", ["#", "SKU", "批次", "库位", "报损数", "申请人", "提交时间"], [list(r) for r in rows])

    if intent == "pending_pick":
        rows = conn.execute(
            """SELECT ob.order_no AS 出库单, ob.sales_order_no AS 销售单,
                      so.receiver_name AS 收件人, ob.created_at AS 派单时间,
                      (SELECT COUNT(*) FROM outbound_item WHERE order_no = ob.order_no) AS 明细行
               FROM outbound_order ob JOIN sales_order so ON so.order_no = ob.sales_order_no
               WHERE ob.status = 'pending' ORDER BY ob.id"""
        ).fetchall()
        return ("待拣货任务", ["出库单", "销售单", "收件人", "派单时间", "明细行"], [list(r) for r in rows])

    if intent == "today_revenue":
        row = conn.execute(
            """SELECT COUNT(*) AS 订单数, COALESCE(SUM(total_amount), 0) AS 总销售额
               FROM sales_order WHERE date(created_at) = date('now')"""
        ).fetchone()
        return ("今日销售概况", ["订单数", "总销售额"], [[row["订单数"], f"¥{row['总销售额']:.2f}"]])

    return ("无法识别该查询", ["提示"], [["试试预设按钮，或输入'销售前5'/'临期'/'过期'/'低库存'/'待审'/'待拣货'/'今日销售'等关键词"]])


@app.route("/ai-query", methods=["GET", "POST"])
@login_required
def ai_query():
    query = ""
    intent = ""
    title = ""
    columns = []
    rows = []
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            intent = _classify_intent(query)
            with database.get_conn() as conn:
                title, columns, rows = _execute_intent(conn, intent)
    return render_template("ai_query.html", presets=AI_PRESETS, query=query, intent=intent,
                           title=title, columns=columns, rows=rows)


# ============ 库存流水（阶段 2 已建，路由保留） ============

@app.route("/stock-log")
@login_required
def stock_log_view():
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT sl.*, s.code AS sku_code, s.name AS sku_name,
                      b.batch_no, l.code AS location_code,
                      u.display_name AS operator_name
               FROM stock_log sl
               JOIN sku s ON s.id = sl.sku_id
               JOIN batch b ON b.id = sl.batch_id
               JOIN location l ON l.id = sl.location_id
               LEFT JOIN user u ON u.id = sl.operator_id
               ORDER BY sl.id DESC LIMIT 200"""
        ).fetchall()
    return render_template("stock_log.html", rows=rows)


if __name__ == "__main__":
    database.init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
