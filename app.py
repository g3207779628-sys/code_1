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
