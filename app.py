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


if __name__ == "__main__":
    database.init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
