import secrets
import sqlite3
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import database

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB（数据导入大表场景；单据附件单文件 10MB 在 attachments 校验）

# 持久化随机 secret_key（首次启动生成 .secret 文件，后续读取）
_SECRET_FILE = Path(__file__).parent / ".secret"
if _SECRET_FILE.exists():
    app.secret_key = _SECRET_FILE.read_text(encoding="utf-8").strip()
else:
    _sk = secrets.token_hex(32)
    _SECRET_FILE.write_text(_sk, encoding="utf-8")
    app.secret_key = _sk


# 左侧导航树：一级 → 二级。Jinja 用它渲染悬停式两层菜单。
# 菜单可见性 = 每个二级 item.key 是否在用户的 menus 集合里 + endpoint 是否已注册。
NAV_TREE = [
    {"label": "日常使用", "items": [
        {"key": "dashboard",  "endpoint": "index",            "label": "工作台"},
        {"key": "pending",    "endpoint": "pending_approvals_list", "label": "待审批中心"},
    ]},
    {"label": "日常操作", "items": [
        {"key": "inbound",    "endpoint": "inbound_list",     "label": "入库"},
        {"label": "出库管理", "children": [
            {"key": "pick",        "endpoint": "pick_list",        "label": "出库单列表"},
            {"key": "requisition", "endpoint": "requisition_list", "label": "申领表"},
        ]},
        {"key": "transfer",   "endpoint": "transfer_list",    "label": "调拨"},
        {"key": "damage",     "endpoint": "damage_list",      "label": "报损"},
        {"key": "stocktake",  "endpoint": "stocktake_list",   "label": "盘点"},
    ]},
    {"label": "查询分析", "items": [
        {"key": "inventory",  "endpoint": "inventory_list",   "label": "库存查询"},
        {"key": "log",        "endpoint": "stock_log_view",   "label": "库存流水"},
        {"key": "forecast",   "endpoint": "forecast_view",    "label": "库存预测"},
    ]},
    {"label": "物品数据维护", "items": [
        {"key": "sku",                  "endpoint": "sku_list",                  "label": "物品"},
        {"key": "owner_party_admin",    "endpoint": "owner_party_admin",         "label": "物品所属方"},
        {"key": "owner_admin_admin",    "endpoint": "owner_admin_admin",         "label": "物品管理方"},
        {"key": "item_category",        "endpoint": "item_category_admin",       "label": "物品类别"},
        {"key": "item_category_major",  "endpoint": "item_category_major_admin", "label": "物品大类"},
    ]},
    {"label": "仓库数据维护", "items": [
        {"key": "warehouse",            "endpoint": "warehouse_list",            "label": "仓库"},
        {"key": "wh_type",              "endpoint": "wh_type_admin",             "label": "类型"},
        {"key": "wh_owner",             "endpoint": "wh_owner_admin",            "label": "责任人"},
        {"key": "wh_use_dept",          "endpoint": "wh_use_dept_admin",         "label": "使用部门"},
        {"key": "wh_alloc_dept",        "endpoint": "wh_alloc_dept_admin",       "label": "分配部门"},
        {"key": "location",             "endpoint": "location_list",             "label": "库位"},
    ]},
    {"label": "系统", "items": [
        {"key": "user_admin",     "endpoint": "user_admin",        "label": "用户与权限"},
        {"key": "position_admin", "endpoint": "position_admin",    "label": "岗位管理"},
        {"key": "channel_config", "endpoint": "channel_config",    "label": "通知渠道"},
        {"key": "backup",         "endpoint": "backup_admin",      "label": "数据备份"},
        {"key": "data_import",    "endpoint": "data_import",       "label": "数据导入"},
        {"key": "password",       "endpoint": "password_change",   "label": "修改密码"},
    ]},
]

def _iter_nav_leaves(nav_tree):
    """遍历 NAV_TREE 所有叶子菜单项（含三层 children 里的），跳过 divider 和纯容器节点。"""
    for grp in nav_tree:
        for it in grp["items"]:
            if it.get("children"):
                for ch in it["children"]:
                    if "key" in ch:
                        yield ch
            elif "key" in it:
                yield it


# 所有合法菜单 key（由 NAV_TREE 自动派生；跳过 divider 项与纯容器节点）
ALL_MENUS = {it["key"] for it in _iter_nav_leaves(NAV_TREE)}


def _user_menus(user_id, role):
    """计算某用户的可见菜单 key 集合。"""
    if role == "admin":
        menus = set(ALL_MENUS)
    else:
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT menu_key FROM menu_permission WHERE user_id = ?", (user_id,)
            ).fetchall()
        menus = {r["menu_key"] for r in rows}
        if not menus:
            menus = {"dashboard"}  # 最低权限：仅工作台
    menus.add("password")  # 所有登录用户均可改密
    return menus


PER_PAGE_DEFAULT = 50


def _pagination(total, per_page=PER_PAGE_DEFAULT, page_arg="page"):
    """统一分页：读 ?page= 参数，返回分页上下文 dict（供各列表页 + _pager.html 共用）。
    total 是筛选后的总条数。offset 用于 SQL LIMIT/OFFSET 或 Python 切片。"""
    try:
        page = int(request.args.get(page_arg, 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    return {"page": page, "per_page": per_page, "total": total,
            "total_pages": total_pages, "offset": offset,
            "has_prev": page > 1, "has_next": page < total_pages}


def _storage_options(conn):
    """取已有的 distinct 存放区域 / 存放位置，供 datalist 输入即搜下拉用。"""
    areas = [r["storage_area"] for r in conn.execute(
        "SELECT DISTINCT storage_area FROM location WHERE IFNULL(storage_area,'')<>'' ORDER BY storage_area").fetchall()]
    positions = [r["storage_position"] for r in conn.execute(
        "SELECT DISTINCT storage_position FROM location WHERE IFNULL(storage_position,'')<>'' ORDER BY storage_position").fetchall()]
    return areas, positions


@app.template_filter("attachments_list")
def attachments_list(json_str):
    """单据的 attachments 字段 JSON 字符串 → 路径列表。"""
    import attachments as _att
    return _att.load_attachments(json_str)


@app.template_filter("readable_payload")
def readable_payload(payload_json, rule_type=""):
    """alert_event.payload_json → 人类可读摘要。"""
    import json as _json
    try:
        items = _json.loads(payload_json or "{}").get("items", [])
    except Exception:
        return (payload_json or "")[:200]
    if not items:
        return "（无）"
    n = len(items)
    if rule_type == "low_stock":
        sample = "；".join(
            f"{i.get('code','?')} {i.get('name','')} 当前{i.get('on_hand','?')}/安全{i.get('safety_stock','?')}"
            for i in items[:3]
        )
        return f"{n} 个 SKU 低于安全库存：{sample}" + ("…" if n > 3 else "")
    if rule_type == "damage_pending":
        sample = "；".join(
            f"DMG-{i.get('id','?')} {i.get('code','?')} 数量{i.get('quantity','?')}"
            for i in items[:3]
        )
        return f"{n} 张报损单待审：{sample}" + ("…" if n > 3 else "")
    return f"{n} 项事件"


@app.errorhandler(413)
def _too_large(e):
    """v22: MAX_CONTENT_LENGTH 超限时给出友好提示页（不是 Werkzeug 默认丑陋报错）"""
    limit_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // 1024 // 1024
    flash(f"上传文件过大，超过 {limit_mb}MB 上限。请把表拆小或联系管理员调整限制", "error")
    referer = request.referrer or url_for("data_import")
    return redirect(referer), 302


@app.context_processor
def inject_css_version():
    """v11: 注入 style.css 的 mtime 作为 query string 版本号，破坏浏览器静态文件缓存。

    每次修改 style.css → mtime 变 → ?v=数字 变 → 浏览器视为新 URL 重新下载。
    """
    css_path = Path(__file__).parent / "static" / "style.css"
    try:
        return {"css_version": int(css_path.stat().st_mtime)}
    except OSError:
        return {"css_version": 1}


def _load_positions_dict():
    """从 position 表读 {code: label}，找不到表时 fallback 到 database.POSITIONS。"""
    try:
        with database.get_conn() as conn:
            rows = conn.execute("SELECT code, label FROM position").fetchall()
        if rows:
            return {r["code"]: r["label"] for r in rows}
    except Exception:
        pass
    return dict(database.POSITIONS)


def _build_menu_label_map():
    """把 NAV_TREE 拍平成 {menu_key: 中文 label}，加上历史已删菜单 key 的兼容映射。"""
    m = {}
    for item in _iter_nav_leaves(NAV_TREE):
        m[item["key"]] = item["label"]
    # 历史已删菜单 key 的中文兼容（避免老用户权限记录显示成英文）
    m.setdefault("expiry",   "临期预警")
    m.setdefault("return",   "退换货")
    m.setdefault("sales",    "销售订单")
    m.setdefault("supplier", "供应商")
    m.setdefault("ai",       "AI 查询")
    m.setdefault("customer", "客户")
    m.setdefault("storage",  "存储区")
    m.setdefault("report",   "经营报表")
    return m


@app.context_processor
def inject_menus():
    menu_labels = _build_menu_label_map()
    if "user_id" not in session:
        return {"menus": set(), "POSITIONS": _load_positions_dict(),
                "nav_tree": NAV_TREE, "available_endpoints": set(),
                "menu_labels": menu_labels}
    menus = _user_menus(session["user_id"], session.get("role"))
    available = {rule.endpoint for rule in app.url_map.iter_rules()}
    return {"menus": menus, "POSITIONS": _load_positions_dict(),
            "nav_tree": NAV_TREE, "available_endpoints": available,
            "menu_labels": menu_labels}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("请先登录", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/api/sku-info")
@login_required
def api_sku_info():
    """v20: 入库/出库表单 JS 选完物品后调用，返回 编号 / 单位 / 规格 / 总在仓数。"""
    from flask import jsonify
    sku_id = request.args.get("id", "").strip()
    if not sku_id:
        return jsonify({"error": "id required"}), 400
    try:
        sku_id = int(sku_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400
    with database.get_conn() as conn:
        r = conn.execute(
            "SELECT id, code, name, spec, unit, "
            "(SELECT COALESCE(SUM(on_hand),0) FROM inventory WHERE sku_id=sku.id) AS on_hand "
            "FROM sku WHERE id=?", (sku_id,)
        ).fetchone()
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": r["id"], "code": r["code"], "name": r["name"],
        "spec": r["spec"] or "", "unit": r["unit"] or "",
        "on_hand": r["on_hand"],
    })


@app.route("/uploads/<path:filepath>")
def download_attachment(filepath):
    """安全地从 uploads/ 提供附件下载/预览。"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    from flask import send_from_directory
    import attachments as _att
    return send_from_directory(_att.UPLOAD_ROOT, filepath, as_attachment=False)


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
            session["position"] = row["position"] or "warehouse_manager"
            session["position_label"] = database.POSITIONS.get(session["position"], session["position"])
            session["must_change_password"] = bool(row["must_change_password"])
            if session["must_change_password"]:
                flash("首次登录请先修改初始密码", "error")
                return redirect(url_for("password_change"))
            # 欢迎语在 welcome 页内显示（hero 大字），不再用 flash
            return redirect(url_for("welcome"))
        flash("用户名或密码错误", "error")
    return render_template("login.html")


@app.route("/", endpoint="welcome")
def welcome():
    """v9 公共/已登录双角色通用首页。

    未登录访客 → base.html 走 content_unauth 分支，渲染公共 hero + 「进入管理系统」按钮
    已登录用户 → base.html 走 content 分支，渲染带 sider 的中央 hero（无按钮，从菜单进工作台）
    """
    return render_template("welcome.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录", "success")
    return redirect(url_for("login"))


@app.before_request
def _force_password_change():
    """带 must_change_password 标记的用户被锁在 /password 直到改密。"""
    if not session.get("user_id"):
        return
    if not session.get("must_change_password"):
        return
    if request.endpoint in ("password_change", "logout", "static", "login"):
        return
    return redirect(url_for("password_change"))


# ============ 修改密码 ============

@app.route("/password", methods=["GET", "POST"], endpoint="password_change")
@login_required
def password_change():
    if request.method == "POST":
        old = request.form.get("old_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not new or len(new) < 6:
            flash("新密码至少 6 位", "error")
        elif new != confirm:
            flash("两次输入的新密码不一致", "error")
        else:
            with database.get_conn() as conn:
                row = conn.execute(
                    "SELECT password_hash FROM user WHERE id = ?", (session["user_id"],)
                ).fetchone()
                if not row or not check_password_hash(row["password_hash"], old):
                    flash("旧密码不正确", "error")
                else:
                    conn.execute(
                        "UPDATE user SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                        (generate_password_hash(new), session["user_id"]),
                    )
                    session["must_change_password"] = False
                    flash("密码已修改", "success")
                    return redirect(url_for("index"))
    return render_template("password_change.html")


# ============ 用户与权限管理 ============

@app.route("/user-admin", methods=["GET", "POST"], endpoint="user_admin")
@role_required("admin", "manager")
def user_admin():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_perms":
            uid = int(request.form["user_id"])
            keys = request.form.getlist("menu_keys")
            with database.get_conn() as conn:
                conn.execute("DELETE FROM menu_permission WHERE user_id = ?", (uid,))
                for k in keys:
                    if k in ALL_MENUS:
                        conn.execute(
                            "INSERT OR IGNORE INTO menu_permission (user_id, menu_key) VALUES (?, ?)",
                            (uid, k),
                        )
            flash("权限已更新", "success")

        elif action == "apply_preset":
            uid = int(request.form["user_id"])
            preset_code = request.form.get("preset_code", "")
            with database.get_conn() as conn:
                preset = conn.execute(
                    "SELECT name, menu_keys FROM role_preset WHERE code=?", (preset_code,)
                ).fetchone()
                if not preset:
                    flash("预设角色不存在", "error")
                else:
                    keys = [k.strip() for k in (preset["menu_keys"] or "").split(",") if k.strip()]
                    conn.execute("DELETE FROM menu_permission WHERE user_id = ?", (uid,))
                    for k in keys:
                        if k in ALL_MENUS:
                            conn.execute(
                                "INSERT OR IGNORE INTO menu_permission (user_id, menu_key) VALUES (?, ?)",
                                (uid, k),
                            )
                    flash(f"已套用预设：{preset['name']}（{len(keys)} 项权限）", "success")

        elif action == "reset_password":
            uid = int(request.form["user_id"])
            new_pwd = request.form.get("new_password", "")
            if len(new_pwd) < 6:
                flash("密码至少 6 位", "error")
            else:
                with database.get_conn() as conn:
                    conn.execute(
                        "UPDATE user SET password_hash = ?, must_change_password = 1 WHERE id = ?",
                        (generate_password_hash(new_pwd), uid),
                    )
                flash("密码已重置；对方下次登录需改密", "success")

        elif action == "create_user":
            username = request.form.get("username", "").strip()
            role = request.form.get("role", "staff")
            # picker 模式：name="position" 是逗号分隔字符串；旧 select multiple 用 getlist 兼容
            positions_selected = request.form.get("position", "") or ",".join(request.form.getlist("position"))
            pwd = request.form.get("password", "")
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            if not username or not pwd:
                flash("用户名和初始密码必填", "error")
            elif len(pwd) < 6:
                flash("密码至少 6 位", "error")
            elif role not in ("admin", "manager", "staff"):
                flash("非法角色", "error")
            else:
                try:
                    with database.get_conn() as conn:
                        cur = conn.execute(
                            "INSERT INTO user (username, password_hash, role, display_name, position, "
                            "must_change_password, email, phone) "
                            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                            (username, generate_password_hash(pwd), role, username, positions_selected,
                             email or None, phone or None),
                        )
                        new_uid = cur.lastrowid
                        # v3: 新建用户默认仅"工作台"权限，不再按岗位自动赋权
                        conn.execute(
                            "INSERT OR IGNORE INTO menu_permission (user_id, menu_key) VALUES (?, ?)",
                            (new_uid, "dashboard"),
                        )
                    flash(f"用户 {username} 已创建，首次登录将强制改密。默认仅工作台权限，请到权限里勾选其他菜单。", "success")
                except sqlite3.IntegrityError:
                    flash("用户名已存在", "error")

        elif action == "update_user":
            uid = int(request.form["user_id"])
            username = request.form.get("username", "").strip()
            role = request.form.get("role", "staff")
            positions_selected = request.form.get("position", "") or ",".join(request.form.getlist("position"))
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            if not username:
                flash("用户名不能空", "error")
            elif role not in ("admin", "manager", "staff"):
                flash("非法角色", "error")
            else:
                try:
                    with database.get_conn() as conn:
                        conn.execute(
                            "UPDATE user SET username=?, display_name=?, role=?, position=?, email=?, phone=? WHERE id=?",
                            (username, username, role, positions_selected,
                             email or None, phone or None, uid),
                        )
                    flash("用户信息已更新", "success")
                except sqlite3.IntegrityError:
                    flash("用户名已存在", "error")

        elif action == "delete_user":
            uid = int(request.form["user_id"])
            if uid == session["user_id"]:
                flash("不能删除自己", "error")
            else:
                with database.get_conn() as conn:
                    target = conn.execute("SELECT role FROM user WHERE id=?", (uid,)).fetchone()
                    if target and target["role"] == "admin" and session.get("role") != "admin":
                        flash("非 admin 不能删除 admin 用户", "error")
                    else:
                        conn.execute("DELETE FROM menu_permission WHERE user_id = ?", (uid,))
                        conn.execute("DELETE FROM user WHERE id = ?", (uid,))
                        flash("用户已删除", "success")

        return redirect(url_for("user_admin"))

    with database.get_conn() as conn:
        users = conn.execute(
            "SELECT id, username, display_name, role, position, must_change_password, "
            "email, phone, created_at FROM user ORDER BY id"
        ).fetchall()
        perms = {u["id"]: set() for u in users}
        for r in conn.execute("SELECT user_id, menu_key FROM menu_permission").fetchall():
            if r["user_id"] in perms:
                perms[r["user_id"]].add(r["menu_key"])
        positions_rows = conn.execute("SELECT code, label FROM position ORDER BY code").fetchall()
        presets = conn.execute(
            "SELECT code, name, description, menu_keys FROM role_preset ORDER BY id"
        ).fetchall()
    # 给每个 preset 算"已选 X 项"
    presets_list = []
    for p in presets:
        keys = [k.strip() for k in (p["menu_keys"] or "").split(",") if k.strip()]
        presets_list.append({
            "code": p["code"], "name": p["name"], "description": p["description"],
            "menu_keys": keys, "count": len(keys),
        })
    return render_template(
        "user_admin.html",
        users=users,
        perms=perms,
        nav_tree=NAV_TREE,
        all_menus=ALL_MENUS,
        positions=positions_rows,
        presets=presets_list,
    )


# ============ 独立权限页（chip 形式，无预设角色 step） ============

@app.route("/user/<int:user_id>/perms", methods=["GET", "POST"], endpoint="user_perms")
@role_required("admin", "manager")
def user_perms(user_id):
    with database.get_conn() as conn:
        user = conn.execute(
            "SELECT id, username, display_name, role, position, email, phone FROM user WHERE id=?",
            (user_id,)
        ).fetchone()
        if not user:
            flash("用户不存在", "error")
            return redirect(url_for("user_admin"))
        if user["role"] == "admin":
            flash("admin 自动拥有全部权限，无需配置", "info")
            return redirect(url_for("user_admin"))
        if request.method == "POST":
            keys = request.form.getlist("menu_keys")
            conn.execute("DELETE FROM menu_permission WHERE user_id=?", (user_id,))
            for k in keys:
                if k in ALL_MENUS:
                    conn.execute(
                        "INSERT OR IGNORE INTO menu_permission (user_id, menu_key) VALUES (?, ?)",
                        (user_id, k),
                    )
            flash(f"已保存 {user['username']} 的权限（{len(keys)} 项）", "success")
            return redirect(url_for("user_admin"))
        current_perms = set(r["menu_key"] for r in conn.execute(
            "SELECT menu_key FROM menu_permission WHERE user_id=?", (user_id,)
        ).fetchall())
    return render_template("user_perms.html",
                           user=user, current_perms=current_perms, nav_tree=NAV_TREE)


# ============ 独立新建用户页 ============

@app.route("/user/new", methods=["GET", "POST"], endpoint="user_new")
@role_required("admin", "manager")
def user_new():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        role = "staff"  # 角色字段已砍，新建用户固定 staff（admin 由 SEED 或代码改 DB）
        positions_selected = request.form.get("position", "")
        pwd = request.form.get("password", "")
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        if not username or not pwd:
            flash("用户名和初始密码必填", "error")
        elif len(pwd) < 6:
            flash("密码至少 6 位", "error")
        else:
            try:
                with database.get_conn() as conn:
                    cur = conn.execute(
                        "INSERT INTO user (username, password_hash, role, display_name, position, "
                        "must_change_password, email, phone) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                        (username, generate_password_hash(pwd), role, username, positions_selected,
                         email or None, phone or None),
                    )
                    new_uid = cur.lastrowid
                    conn.execute(
                        "INSERT OR IGNORE INTO menu_permission (user_id, menu_key) VALUES (?, ?)",
                        (new_uid, "dashboard"),
                    )
                flash(f"用户 {username} 已创建，首次登录将强制改密。默认仅工作台权限。", "success")
                return redirect(url_for("user_perms", user_id=new_uid))
            except sqlite3.IntegrityError:
                flash("用户名已存在", "error")
    return render_template("user_new.html", nav_tree=NAV_TREE)


# ============ 岗位 CRUD（独立页） ============

@app.route("/position-admin", methods=["GET", "POST"], endpoint="position_admin")
@role_required("admin", "manager")
def position_admin():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            label = request.form.get("label", "").strip()
            code = request.form.get("code", "").strip() or f"pos_{int(__import__('time').time()*1000)}"
            if not label:
                flash("岗位名称不能空", "error")
            else:
                try:
                    with database.get_conn() as conn:
                        conn.execute("INSERT INTO position (code, label) VALUES (?, ?)", (code, label))
                    flash(f"岗位 {label} 已新增", "success")
                except sqlite3.IntegrityError:
                    flash("岗位代码冲突", "error")
        elif action == "delete":
            code = request.form["code"]
            with database.get_conn() as conn:
                in_use = conn.execute(
                    "SELECT COUNT(*) AS c FROM user WHERE position LIKE ? OR position LIKE ? OR position LIKE ? OR position = ?",
                    (f"{code},%", f"%,{code},%", f"%,{code}", code),
                ).fetchone()
                if in_use["c"] > 0:
                    flash(f"该岗位仍被 {in_use['c']} 个用户使用，无法删除", "error")
                else:
                    conn.execute("DELETE FROM position WHERE code=?", (code,))
                    flash("岗位已删除", "success")
        return redirect(url_for("position_admin"))

    with database.get_conn() as conn:
        rows = conn.execute("SELECT code, label FROM position ORDER BY code").fetchall()
        # 顺便统计每个岗位被多少用户使用
        usage = {}
        for u in conn.execute("SELECT position FROM user").fetchall():
            for c in (u["position"] or "").split(","):
                c = c.strip()
                if c:
                    usage[c] = usage.get(c, 0) + 1
    positions_list = [{"code": r["code"], "label": r["label"], "in_use": usage.get(r["code"], 0)} for r in rows]
    return render_template("position_admin.html", positions=positions_list)


# ============ 预设角色 CRUD ============

@app.route("/role-preset", methods=["GET", "POST"], endpoint="role_preset")
@role_required("admin", "manager")
def role_preset():
    if request.method == "POST":
        action = request.form.get("action")
        with database.get_conn() as conn:
            if action == "create":
                code = request.form.get("code", "").strip()
                name = request.form.get("name", "").strip()
                desc = request.form.get("description", "").strip()
                keys = ",".join(request.form.getlist("menu_keys"))
                if not name:
                    flash("名称必填", "error")
                else:
                    if not code:
                        code = f"preset_{uuid.uuid4().hex[:8]}"
                    try:
                        conn.execute(
                            "INSERT INTO role_preset (code, name, description, menu_keys) VALUES (?, ?, ?, ?)",
                            (code, name, desc, keys),
                        )
                        flash(f"已新建预设：{name}", "success")
                    except sqlite3.IntegrityError:
                        flash("代码已存在", "error")
            elif action == "update":
                pid = int(request.form["preset_id"])
                name = request.form.get("name", "").strip()
                desc = request.form.get("description", "").strip()
                keys = ",".join(request.form.getlist("menu_keys"))
                conn.execute(
                    "UPDATE role_preset SET name=?, description=?, menu_keys=? WHERE id=?",
                    (name, desc, keys, pid),
                )
                flash("预设已更新", "success")
            elif action == "delete":
                pid = int(request.form["preset_id"])
                conn.execute("DELETE FROM role_preset WHERE id=?", (pid,))
                flash("预设已删除", "success")
        return redirect(url_for("role_preset"))

    with database.get_conn() as conn:
        presets = conn.execute(
            "SELECT id, code, name, description, menu_keys, created_at FROM role_preset ORDER BY id"
        ).fetchall()
    presets_list = []
    for p in presets:
        keys = [k.strip() for k in (p["menu_keys"] or "").split(",") if k.strip()]
        presets_list.append({
            "id": p["id"], "code": p["code"], "name": p["name"],
            "description": p["description"], "menu_keys": keys,
            "count": len(keys), "created_at": p["created_at"],
        })
    return render_template(
        "role_preset.html",
        presets=presets_list, nav_tree=NAV_TREE,
    )


# ============ 首页 ============

def _global_stats(conn):
    return {
        "sku": conn.execute("SELECT COUNT(*) AS c FROM sku").fetchone()["c"],
        "batch": conn.execute("SELECT COUNT(*) AS c FROM batch").fetchone()["c"],
        "total_on_hand": conn.execute("SELECT COALESCE(SUM(on_hand),0) AS c FROM inventory").fetchone()["c"],
        "total_reserved": conn.execute("SELECT COALESCE(SUM(reserved),0) AS c FROM inventory").fetchone()["c"],
        "pending_pick": conn.execute("SELECT COUNT(*) AS c FROM outbound_order WHERE status='pending'").fetchone()["c"],
        "pending_damage": conn.execute("SELECT COUNT(*) AS c FROM damage_log WHERE status='pending'").fetchone()["c"],
        "low_stock_count": conn.execute(
            "SELECT COUNT(*) AS c FROM (SELECT s.id FROM sku s LEFT JOIN inventory i ON i.sku_id=s.id "
            "GROUP BY s.id HAVING COALESCE(SUM(i.on_hand),0) < s.safety_stock AND s.safety_stock > 0)"
        ).fetchone()["c"],
        "open_return": 0,
        "draft_inbound": conn.execute("SELECT COUNT(*) AS c FROM inbound_order WHERE status='draft'").fetchone()["c"],
        "open_stocktake": conn.execute("SELECT COUNT(*) AS c FROM stocktake_order WHERE status='open'").fetchone()["c"],
        "today_revenue": 0,
        "today_sales_orders": conn.execute("SELECT COUNT(*) AS c FROM outbound_order WHERE date(created_at)=date('now')").fetchone()["c"],
        "approved_today": conn.execute(
            "SELECT COUNT(*) AS c FROM (SELECT id FROM damage_log WHERE date(approved_at)=date('now') AND status='approved' "
            "UNION ALL SELECT id FROM inbound_order WHERE date(approved_at)=date('now') AND status='approved')"
        ).fetchone()["c"],
        "month_inbound": conn.execute(
            "SELECT COUNT(*) AS c FROM inbound_order "
            "WHERE status='approved' AND strftime('%Y-%m', approved_at) = strftime('%Y-%m', 'now')"
        ).fetchone()["c"],
        "month_outbound": conn.execute(
            "SELECT COUNT(*) AS c FROM outbound_order "
            "WHERE status='completed' AND strftime('%Y-%m', completed_at) = strftime('%Y-%m', 'now')"
        ).fetchone()["c"],
    }


def _pending_approvals(conn, limit=10):
    """全仓维度的待审批：报损 + 入库（草稿）+ 退货待退款。"""
    items = []
    for r in conn.execute(
        "SELECT d.id, s.code AS sku_code, b.batch_no, d.quantity, u.display_name AS applicant, d.created_at "
        "FROM damage_log d JOIN sku s ON s.id=d.sku_id JOIN batch b ON b.id=d.batch_id "
        "LEFT JOIN user u ON u.id=d.applicant_id WHERE d.status='pending' ORDER BY d.id DESC LIMIT ?", (limit,)
    ).fetchall():
        items.append({"type": "报损", "id": f"DMG-{r['id']:03d}", "detail": f"{r['sku_code']} · {r['batch_no']}", "qty": r["quantity"], "by": r["applicant"], "at": r["created_at"], "url": "/damage"})
    for r in conn.execute(
        "SELECT o.order_no, uo.display_name AS operator, u.display_name AS creator, o.created_at, "
        "(SELECT COALESCE(SUM(quantity),0) FROM inbound_item WHERE order_no=o.order_no) AS qty "
        "FROM inbound_order o LEFT JOIN user uo ON uo.id=o.operator_id LEFT JOIN user u ON u.id=o.creator_id "
        "WHERE o.status='draft' ORDER BY o.id DESC LIMIT ?", (limit,)
    ).fetchall():
        items.append({"type": "入库", "id": r["order_no"], "detail": r["operator"] or "-", "qty": r["qty"], "by": r["creator"], "at": r["created_at"], "url": f"/inbound/{r['order_no']}"})
    return items[:limit]


@app.route("/workbench")
@login_required
def index():
    """v9: 工作台路由从 / 改为 /workbench，按 session.position 分发到 11 个 dashboard 模板之一。

    endpoint 仍是 'index'（向后兼容所有 url_for('index') 引用 + 菜单"工作台" key='dashboard' endpoint='index'）。
    /  路径让给了 welcome（公共/已登录通用首页）。
    """
    pos = session.get("position") or "warehouse_manager"
    if pos not in database.POSITIONS:
        pos = "warehouse_manager"
    template = f"dashboard_{pos}.html"
    with database.get_conn() as conn:
        ctx = {"stats": _global_stats(conn)}
        # 各 dashboard 需要的角色化数据
        if pos == "warehouse_manager":
            ctx["pending_approvals"] = _pending_approvals(conn, 8)
            ctx["low_stock_top3"] = conn.execute(
                "SELECT s.code, s.name, s.safety_stock, COALESCE(SUM(i.on_hand),0) AS on_hand, "
                "(SELECT (l.storage_area || ' / ' || l.storage_position) FROM location l JOIN inventory i2 ON i2.location_id=l.id "
                " WHERE i2.sku_id=s.id ORDER BY i2.id LIMIT 1) AS location_code "
                "FROM sku s LEFT JOIN inventory i ON i.sku_id=s.id GROUP BY s.id "
                "HAVING on_hand < s.safety_stock AND s.safety_stock > 0 "
                "ORDER BY (s.safety_stock - on_hand) DESC LIMIT 3"
            ).fetchall()
            ctx["recent_inout5"] = conn.execute(
                "SELECT sl.occurred_at, s.name AS sku_name, sl.delta, sl.event_type "
                "FROM stock_log sl JOIN sku s ON s.id=sl.sku_id "
                "ORDER BY sl.id DESC LIMIT 5"
            ).fetchall()
        elif pos == "inventory_ctrl":
            ctx["pending_damage"] = conn.execute(
                "SELECT d.id, s.code AS sku_code, s.name AS sku_name, b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS loc, d.quantity, d.reason_type, u.display_name AS applicant "
                "FROM damage_log d JOIN sku s ON s.id=d.sku_id JOIN batch b ON b.id=d.batch_id JOIN location l ON l.id=d.location_id "
                "LEFT JOIN user u ON u.id=d.applicant_id WHERE d.status='pending' ORDER BY d.id DESC LIMIT 10"
            ).fetchall()
            ctx["low_stock"] = conn.execute(
                "SELECT s.code, s.name, s.safety_stock, COALESCE(SUM(i.on_hand),0) AS on_hand "
                "FROM sku s LEFT JOIN inventory i ON i.sku_id=s.id GROUP BY s.id "
                "HAVING on_hand < s.safety_stock AND s.safety_stock > 0 ORDER BY (s.safety_stock - on_hand) DESC LIMIT 10"
            ).fetchall()
        elif pos == "purchaser":
            ctx["low_stock"] = conn.execute(
                "SELECT s.code, s.name, s.safety_stock, COALESCE(SUM(i.on_hand),0) AS on_hand "
                "FROM sku s LEFT JOIN inventory i ON i.sku_id=s.id GROUP BY s.id "
                "HAVING on_hand < s.safety_stock AND s.safety_stock > 0 ORDER BY (s.safety_stock - on_hand) DESC LIMIT 10"
            ).fetchall()
            ctx["recent_inbound"] = conn.execute(
                "SELECT o.order_no, uo.display_name AS operator, o.status, o.created_at, "
                "(SELECT COALESCE(SUM(quantity*unit_price),0) FROM inbound_item WHERE order_no=o.order_no) AS amount "
                "FROM inbound_order o LEFT JOIN user uo ON uo.id=o.operator_id ORDER BY o.id DESC LIMIT 8"
            ).fetchall()
        elif pos == "receiver":
            ctx["pending_inbound"] = conn.execute(
                "SELECT o.order_no, uo.display_name AS operator, o.status, o.created_at, "
                "(SELECT COALESCE(SUM(quantity),0) FROM inbound_item WHERE order_no=o.order_no) AS qty "
                "FROM inbound_order o LEFT JOIN user uo ON uo.id=o.operator_id "
                "WHERE o.status='draft' ORDER BY o.created_at DESC LIMIT 10"
            ).fetchall()
            ctx["recent_log"] = conn.execute(
                "SELECT sl.delta, sl.occurred_at, s.code AS sku, b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS loc "
                "FROM stock_log sl JOIN sku s ON s.id=sl.sku_id JOIN batch b ON b.id=sl.batch_id JOIN location l ON l.id=sl.location_id "
                "WHERE sl.event_type='inbound' ORDER BY sl.id DESC LIMIT 10"
            ).fetchall()
        elif pos == "putaway":
            ctx["loc_occupancy"] = conn.execute(
                "SELECT (l.storage_area || ' / ' || l.storage_position) AS code, COALESCE(SUM(i.on_hand),0) AS used "
                "FROM location l LEFT JOIN inventory i ON i.location_id=l.id GROUP BY l.id ORDER BY l.storage_area, l.storage_position"
            ).fetchall()
            ctx["recent_inbound"] = conn.execute(
                "SELECT o.order_no, uo.display_name AS operator, o.created_at, "
                "(SELECT COALESCE(SUM(quantity),0) FROM inbound_item WHERE order_no=o.order_no) AS qty "
                "FROM inbound_order o LEFT JOIN user uo ON uo.id=o.operator_id "
                "WHERE o.status='approved' AND date(o.approved_at)>=date('now','-1 day') "
                "ORDER BY o.approved_at DESC LIMIT 8"
            ).fetchall()
        elif pos == "cs":
            ctx["recent_outbound"] = conn.execute(
                "SELECT order_no, receiver_desc, status, created_at FROM outbound_order ORDER BY id DESC LIMIT 8"
            ).fetchall()
        elif pos == "picker":
            ctx["my_pending"] = conn.execute(
                "SELECT ob.order_no, ob.receiver_desc, ob.created_at, "
                "(SELECT COALESCE(SUM(quantity),0) FROM outbound_item WHERE order_no=ob.order_no) AS qty, "
                "(SELECT COUNT(*) FROM outbound_item WHERE order_no=ob.order_no) AS lines "
                "FROM outbound_order ob "
                "WHERE ob.status='pending' ORDER BY ob.id LIMIT 10"
            ).fetchall()
            ctx["my_done_today"] = conn.execute(
                "SELECT ob.order_no, ob.completed_at, "
                "(SELECT COALESCE(SUM(quantity),0) FROM outbound_item WHERE order_no=ob.order_no) AS qty "
                "FROM outbound_order ob WHERE ob.picker_id=? AND date(ob.completed_at)=date('now') "
                "ORDER BY ob.id DESC LIMIT 8", (session["user_id"],)
            ).fetchall()
            ctx["pick_rank"] = conn.execute(
                "SELECT u.display_name AS name, COUNT(ob.id) AS picked FROM user u "
                "LEFT JOIN outbound_order ob ON ob.picker_id=u.id AND date(ob.completed_at)=date('now') "
                "WHERE u.position='picker' GROUP BY u.id ORDER BY picked DESC LIMIT 5"
            ).fetchall()
        elif pos == "packer":
            ctx["to_pack"] = conn.execute(
                "SELECT ob.order_no, ob.receiver_desc, ob.completed_at "
                "FROM outbound_order ob "
                "WHERE ob.status='completed' ORDER BY ob.completed_at DESC LIMIT 12"
            ).fetchall()
        elif pos == "shipping":
            ctx["to_ship"] = conn.execute(
                "SELECT ob.order_no, ob.receiver_desc, ob.completed_at "
                "FROM outbound_order ob "
                "WHERE ob.status='completed' ORDER BY ob.completed_at DESC LIMIT 12"
            ).fetchall()
            ctx["channel_dist"] = []
        elif pos == "stocktaker":
            ctx["open_orders"] = conn.execute(
                "SELECT st.order_no, st.note, st.created_at, "
                "(SELECT COUNT(*) FROM stocktake_item WHERE order_no=st.order_no) AS total, "
                "(SELECT COUNT(*) FROM stocktake_item WHERE order_no=st.order_no AND actual_qty IS NOT NULL) AS counted, "
                "(SELECT COUNT(*) FROM stocktake_item WHERE order_no=st.order_no AND actual_qty IS NOT NULL AND actual_qty != expected_qty) AS diff "
                "FROM stocktake_order st WHERE st.status='open' ORDER BY st.id DESC LIMIT 5"
            ).fetchall()
            ctx["recent_closed"] = conn.execute(
                "SELECT st.order_no, st.closed_at, "
                "(SELECT COUNT(*) FROM stocktake_item WHERE order_no=st.order_no AND actual_qty != expected_qty) AS diff "
                "FROM stocktake_order st WHERE st.status='closed' ORDER BY st.id DESC LIMIT 5"
            ).fetchall()
    return render_template(template, **ctx)


# ============ SKU ============

@app.route("/sku")
@login_required
def sku_list():
    q = request.args.get("q", "").strip()
    where, params = "", []
    if q:
        where = "WHERE name LIKE ? OR code LIKE ? OR IFNULL(brand,'') LIKE ?"
        kw = f"%{q}%"; params = [kw, kw, kw]
    with database.get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM sku {where}", params).fetchone()["c"]
        pg = _pagination(total)
        skus = conn.execute(
            f"SELECT * FROM sku {where} ORDER BY code LIMIT ? OFFSET ?",
            params + [pg["per_page"], pg["offset"]],
        ).fetchall()
    return render_template("sku_list.html", skus=skus, pg=pg, q=q)


def _next_sku_code(conn):
    """生成新的物品编码 SP000001 / SP000002 / ..."""
    row = conn.execute(
        "SELECT code FROM sku WHERE code LIKE 'SP%' ORDER BY code DESC LIMIT 1"
    ).fetchone()
    if row:
        try:
            return f"SP{int(row['code'][2:]) + 1:06d}"
        except (ValueError, TypeError):
            pass
    return "SP000001"


@app.route("/sku/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def sku_new():
    if request.method == "POST":
        try:
            with database.get_conn() as conn:
                code = request.form.get("code", "").strip() or _next_sku_code(conn)
                owner_party_id = request.form.get("owner_party_id") or None
                owner_admin_id = request.form.get("owner_admin_id") or None
                category_id = request.form.get("category_id") or None
                category_major_id = request.form.get("category_major_id") or None
                in_contract = 1 if request.form.get("in_contract") == "on" else 0
                cur = conn.execute(
                    "INSERT INTO sku (code, name, spec, unit, safety_stock, "
                    "owner_party_id, owner_admin_id, brand, "
                    "category_id, category_major_id, usage_purpose, in_contract) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        code,
                        request.form["name"].strip(),
                        request.form.get("spec", "").strip(),
                        request.form.get("unit", "份").strip(),
                        int(request.form.get("safety_stock") or 0),
                        int(owner_party_id) if owner_party_id else None,
                        int(owner_admin_id) if owner_admin_id else None,
                        request.form.get("brand", "").strip(),
                        int(category_id) if category_id else None,
                        int(category_major_id) if category_major_id else None,
                        request.form.get("usage_purpose", "").strip(),
                        in_contract,
                    ),
                )
                new_id = cur.lastrowid
                # 图片上传（单图，仿 warehouse 模式）
                import attachments as _att
                files = request.files.getlist("image")
                try:
                    saved = _att.save_files(files, "sku", new_id) if files else []
                except ValueError as _e:
                    flash(str(_e), "error")
                    return render_template("sku_form.html", sku=None)
                if saved:
                    conn.execute("UPDATE sku SET image_path=? WHERE id=?", (saved[0], new_id))
            if request.form.get("inline") == "1":
                with database.get_conn() as conn:
                    new_row = conn.execute("SELECT id, code, name, spec FROM sku WHERE code=?", (code,)).fetchone()
                from flask import jsonify
                return jsonify({"id": new_row["id"], "label": f"{new_row['name']} ({new_row['spec'] or ''})".strip()})
            flash(f"物品 {code} 已创建", "success")
            return redirect(url_for("sku_list"))
        except sqlite3.IntegrityError:
            if request.form.get("inline") == "1":
                from flask import jsonify
                return jsonify({"error": "物品编码已存在"}), 400
            flash("物品编码已存在", "error")
    return render_template("sku_form.html", sku=None)


@app.route("/sku/<int:sku_id>/edit", methods=["GET", "POST"])
@role_required("admin", "manager")
def sku_edit(sku_id):
    with database.get_conn() as conn:
        sku = conn.execute("SELECT * FROM sku WHERE id = ?", (sku_id,)).fetchone()
        if not sku:
            flash("物品不存在", "error")
            return redirect(url_for("sku_list"))
        if request.method == "POST":
            try:
                owner_party_id = request.form.get("owner_party_id") or None
                owner_admin_id = request.form.get("owner_admin_id") or None
                category_id = request.form.get("category_id") or None
                category_major_id = request.form.get("category_major_id") or None
                in_contract = 1 if request.form.get("in_contract") == "on" else 0
                conn.execute(
                    "UPDATE sku SET code=?, name=?, spec=?, unit=?, safety_stock=?, "
                    "owner_party_id=?, owner_admin_id=?, brand=?, category_id=?, category_major_id=?, "
                    "usage_purpose=?, in_contract=? WHERE id=?",
                    (
                        request.form["code"].strip(),
                        request.form["name"].strip(),
                        request.form.get("spec", "").strip(),
                        request.form.get("unit", "份").strip(),
                        int(request.form.get("safety_stock") or 0),
                        int(owner_party_id) if owner_party_id else None,
                        int(owner_admin_id) if owner_admin_id else None,
                        request.form.get("brand", "").strip(),
                        int(category_id) if category_id else None,
                        int(category_major_id) if category_major_id else None,
                        request.form.get("usage_purpose", "").strip(),
                        in_contract,
                        sku_id,
                    ),
                )
                # 图片上传（单图覆盖；留空保留原图）
                import attachments as _att
                files = request.files.getlist("image")
                if files and files[0] and files[0].filename:
                    try:
                        saved = _att.save_files(files, "sku", sku_id)
                    except ValueError as _e:
                        flash(str(_e), "error")
                        # 主字段已 UPDATE，图片失败仅 flash 提示，不回滚
                        saved = []
                    if saved:
                        conn.execute("UPDATE sku SET image_path=? WHERE id=?", (saved[0], sku_id))
                flash("物品更新成功", "success")
                return redirect(url_for("sku_list"))
            except sqlite3.IntegrityError:
                flash("物品编码冲突", "error")
        # 加载关联主数据名称给模板回填
        def _name_of(tbl, idv):
            if not idv:
                return ""
            r = conn.execute(f"SELECT name FROM {tbl} WHERE id=?", (idv,)).fetchone()
            return r["name"] if r else ""
        owner_party_name = _name_of("owner_party", sku["owner_party_id"])
        owner_admin_name = _name_of("owner_admin", sku["owner_admin_id"]) if "owner_admin_id" in sku.keys() else ""
        category_name = _name_of("item_category", sku["category_id"]) if "category_id" in sku.keys() else ""
        category_major_name = _name_of("item_category_major", sku["category_major_id"]) if "category_major_id" in sku.keys() else ""
    return render_template("sku_form.html", sku=sku,
                           owner_party_name=owner_party_name,
                           owner_admin_name=owner_admin_name,
                           category_name=category_name,
                           category_major_name=category_major_name)


@app.route("/sku/<int:sku_id>/delete", methods=["POST"])
@role_required("admin")
def sku_delete(sku_id):
    with database.get_conn() as conn:
        conn.execute("DELETE FROM sku WHERE id = ?", (sku_id,))
    flash("SKU 已删除", "success")
    return redirect(url_for("sku_list"))


@app.route("/sku/<int:sku_id>/detail")
@login_required
def sku_detail(sku_id):
    with database.get_conn() as conn:
        sku = conn.execute("SELECT * FROM sku WHERE id = ?", (sku_id,)).fetchone()
        if not sku:
            flash("SKU 不存在", "error")
            return redirect(url_for("sku_list"))

        # 关联主数据名称：物资所属单位 / 物资管理方
        owner_unit = owner_party = None
        if sku["owner_unit_id"]:
            r = conn.execute("SELECT name FROM owner_unit WHERE id=?", (sku["owner_unit_id"],)).fetchone()
            owner_unit = r["name"] if r else None
        if sku["owner_party_id"]:
            r = conn.execute("SELECT name FROM owner_party WHERE id=?", (sku["owner_party_id"],)).fetchone()
            owner_party = r["name"] if r else None

        current_total = conn.execute(
            "SELECT COALESCE(SUM(on_hand), 0) AS total FROM inventory WHERE sku_id = ?",
            (sku_id,),
        ).fetchone()["total"]

        # 存放位置（该物品有库存的库位，去重）；统一格式 = 存储区 / 存放位置，与库存查询页一致
        positions = [
            r["loc"] for r in conn.execute(
                "SELECT DISTINCT (l.storage_area || ' / ' || l.storage_position) AS loc FROM inventory i "
                "JOIN location l ON l.id = i.location_id "
                "WHERE i.sku_id = ? AND i.on_hand > 0 "
                "ORDER BY l.storage_area, l.storage_position",
                (sku_id,),
            ).fetchall()
        ]

        # 采购记录（导入的入库单据：采购数量 + 采购时间）
        purchases = conn.execute(
            """SELECT io.created_at AS purchase_time, ii.quantity, io.order_no,
                      (l.storage_area || ' / ' || l.storage_position) AS loc
               FROM inbound_item ii
               JOIN inbound_order io ON io.order_no = ii.order_no
               JOIN location l ON l.id = ii.location_id
               WHERE ii.sku_id = ?
               ORDER BY io.created_at DESC, io.order_no DESC""",
            (sku_id,),
        ).fetchall()

        # 出库流水（客服出入库台账导入的月度出库）
        outbound_rows = conn.execute(
            """SELECT sl.occurred_at, sl.delta, sl.event_type, sl.source_doc, sl.note,
                      (l.storage_area || ' / ' || l.storage_position) AS loc
               FROM stock_log sl
               JOIN location l ON l.id = sl.location_id
               WHERE sl.sku_id = ?
                 AND sl.event_type IN ('outbound', 'damage')
               ORDER BY sl.occurred_at DESC""",
            (sku_id,),
        ).fetchall()

    return render_template(
        "sku_detail.html",
        sku=sku,
        owner_unit=owner_unit,
        owner_party=owner_party,
        current_total=current_total,
        positions=positions,
        purchases=purchases,
        outbound_rows=outbound_rows,
    )


# ============ 入库单 ============

@app.route("/inbound")
@login_required
def inbound_list():
    """v20: 列表加 仓库 / 楼栋 / 楼层 / 物品 / 数量 信息（聚合自 inbound_item）。
    v24: 加条件查询(单号/状态/物品) + 创建时间区间 + 分页。"""
    f = {
        "order_no":          request.args.get("order_no", "").strip(),
        "item":              request.args.get("item", "").strip(),
        "category_major_id": request.args.get("category_major_id", "").strip(),
        "warehouse_id":      request.args.get("warehouse_id", "").strip(),
        "operator":          request.args.get("operator", "").strip(),
        "status":            request.args.get("status", "").strip(),
        "date_from":         request.args.get("date_from", "").strip(),
        "date_to":           request.args.get("date_to", "").strip(),
    }
    where, params = [], []
    if f["order_no"]:
        where.append("o.order_no LIKE ?"); params.append(f"%{f['order_no']}%")
    if f["item"]:
        where.append("EXISTS (SELECT 1 FROM inbound_item ii JOIN sku s ON s.id=ii.sku_id "
                     "WHERE ii.order_no=o.order_no AND (s.name LIKE ? OR s.code LIKE ?))")
        params += [f"%{f['item']}%", f"%{f['item']}%"]
    if f["category_major_id"]:
        where.append("EXISTS (SELECT 1 FROM inbound_item ii JOIN sku s ON s.id=ii.sku_id "
                     "WHERE ii.order_no=o.order_no AND s.category_major_id=?)")
        params.append(f["category_major_id"])
    if f["warehouse_id"]:
        where.append("EXISTS (SELECT 1 FROM inbound_item ii JOIN location l ON l.id=ii.location_id "
                     "WHERE ii.order_no=o.order_no AND l.warehouse_id=?)")
        params.append(f["warehouse_id"])
    if f["operator"]:
        where.append("(IFNULL(uo.display_name,'') LIKE ? OR IFNULL(uc.display_name,'') LIKE ?)")
        params += [f"%{f['operator']}%", f"%{f['operator']}%"]
    if f["status"]:
        where.append("o.status = ?"); params.append(f["status"])
    if f["date_from"]:
        where.append("date(o.created_at) >= ?"); params.append(f["date_from"])
    if f["date_to"]:
        where.append("date(o.created_at) <= ?"); params.append(f["date_to"])
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    join = (" FROM inbound_order o "
            "LEFT JOIN user uo ON uo.id=o.operator_id "
            "LEFT JOIN user uc ON uc.id=o.creator_id "
            "LEFT JOIN user ua ON ua.id=o.approver_id")
    with database.get_conn() as conn:
        filtered_total = conn.execute("SELECT COUNT(*) AS c" + join + where_sql, params).fetchone()["c"]
        pg = _pagination(filtered_total)
        orders = conn.execute(
            "SELECT o.*, uo.display_name AS operator_name, uc.display_name AS creator_name, "
            "ua.display_name AS approver_name, "
            "(SELECT COALESCE(SUM(quantity),0) FROM inbound_item WHERE order_no=o.order_no) AS total_qty, "
            "(SELECT COUNT(*) FROM inbound_item WHERE order_no=o.order_no) AS line_count"
            + join + where_sql + " ORDER BY o.id DESC LIMIT ? OFFSET ?",
            params + [pg["per_page"], pg["offset"]]).fetchall()
        order_summary = {}
        for o in orders:
            row = conn.execute(
                """SELECT w.name AS wh_name, l.storage_area, l.storage_position,
                          GROUP_CONCAT(DISTINCT s.name) AS sku_names,
                          GROUP_CONCAT(DISTINCT icm.name) AS cat_names
                   FROM inbound_item ii
                   JOIN location l ON l.id = ii.location_id
                   JOIN warehouse w ON w.id = l.warehouse_id
                   JOIN sku s ON s.id = ii.sku_id
                   LEFT JOIN item_category_major icm ON icm.id = s.category_major_id
                   WHERE ii.order_no = ?""",
                (o["order_no"],),
            ).fetchone()
            order_summary[o["order_no"]] = row
        warehouses = conn.execute("SELECT id, name FROM warehouse ORDER BY id").fetchall()
        cat_majors = conn.execute("SELECT id, name FROM item_category_major ORDER BY name").fetchall()
    return render_template("inbound_list.html", orders=orders, order_summary=order_summary, f=f, pg=pg,
                           warehouses=warehouses, cat_majors=cat_majors)


@app.route("/inbound/new", methods=["GET", "POST"])
@login_required
def inbound_new():
    with database.get_conn() as conn:
        skus = conn.execute("SELECT * FROM sku ORDER BY code").fetchall()
        locations = conn.execute(
            "SELECT id, storage_area, storage_position FROM location ORDER BY storage_area, storage_position"
        ).fetchall()
        area_options, position_options = _storage_options(conn)

        if request.method == "POST":
            # v25：明细行填 存放区域 + 存放位置 + 物品 + 数量 + 批次号
            warehouse_id_raw = request.form.get("warehouse_id", "").strip()
            if not warehouse_id_raw:
                flash("入库仓库必填", "error")
                return render_template("inbound_form.html", skus=skus, locations=locations, area_options=area_options, position_options=position_options)
            warehouse_id = int(warehouse_id_raw)
            operator_raw = request.form.get("operator_id", "").strip()
            if not operator_raw:
                flash("入库员工必填", "error")
                return render_template("inbound_form.html", skus=skus, locations=locations, area_options=area_options, position_options=position_options)
            operator_id = int(operator_raw)
            note = request.form.get("note", "").strip()

            areas = request.form.getlist("storage_area[]")
            positions = request.form.getlist("storage_position[]")
            sku_ids = request.form.getlist("sku_id[]")
            batch_nos = request.form.getlist("batch_no[]")
            quantities = request.form.getlist("quantity[]")

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
                storage_area = (areas[i] if i < len(areas) else "").strip()
                storage_position = (positions[i] if i < len(positions) else "").strip()
                if not storage_area or not storage_position:
                    flash(f"第 {i+1} 行：存放区域 + 存放位置 必填", "error")
                    return render_template("inbound_form.html", skus=skus, locations=locations,
                                           area_options=area_options, position_options=position_options)
                # 自动按 (仓库, 存放区域, 存放位置) 找 location；找不到自动创建
                loc_row = conn.execute(
                    "SELECT id FROM location WHERE warehouse_id=? AND storage_area=? AND storage_position=? LIMIT 1",
                    (warehouse_id, storage_area, storage_position),
                ).fetchone()
                if loc_row:
                    location_id = loc_row["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO location (warehouse_id, storage_area, storage_position) VALUES (?, ?, ?)",
                        (warehouse_id, storage_area, storage_position),
                    )
                    location_id = cur.lastrowid
                # 批次号留空时自动生成
                batch_no = (batch_nos[i] if i < len(batch_nos) else "").strip()
                if not batch_no:
                    sku_code_row = conn.execute("SELECT code FROM sku WHERE id=?", (int(sku_ids[i]),)).fetchone()
                    sku_code = sku_code_row["code"] if sku_code_row else "SP"
                    from datetime import datetime as _dt
                    batch_no = f"{sku_code}-{_dt.now().strftime('%Y%m%d%H%M%S')}-{i+1}"
                valid_rows.append({
                    "sku_id": int(sku_ids[i]),
                    "batch_no": batch_no,
                    "location_id": location_id,
                    "quantity": qty,
                    "unit_price": 0.0,
                })

            if not valid_rows:
                flash("至少要填一行明细", "error")
                return render_template("inbound_form.html", skus=skus, locations=locations, area_options=area_options, position_options=position_options)

            order_no = database.gen_order_no(conn, "RKD", "inbound_order")
            # 附件保存
            import attachments as _att
            import json as _json
            try:
                paths = _att.save_files(request.files.getlist("attachments[]"), "inbound", order_no)
            except ValueError as e:
                flash(str(e), "error")
                return render_template("inbound_form.html", skus=skus, locations=locations, area_options=area_options, position_options=position_options)
            conn.execute(
                "INSERT INTO inbound_order (order_no, operator_id, status, creator_id, note, attachments) VALUES (?, ?, 'draft', ?, ?, ?)",
                (order_no, operator_id, session["user_id"], note,
                 _json.dumps(paths, ensure_ascii=False) if paths else None),
            )
            for r in valid_rows:
                conn.execute(
                    "INSERT INTO inbound_item (order_no, sku_id, batch_no, location_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?, ?)",
                    (order_no, r["sku_id"], r["batch_no"], r["location_id"], r["quantity"], r["unit_price"]),
                )
            flash(f"入库单 {order_no} 已创建（草稿，待审核）", "success")
            return redirect(url_for("inbound_detail", order_no=order_no))

    return render_template("inbound_form.html", skus=skus, locations=locations, area_options=area_options, position_options=position_options)


@app.route("/inbound/<order_no>")
@login_required
def inbound_detail(order_no):
    with database.get_conn() as conn:
        order = conn.execute(
            """SELECT o.*,
                      uo.display_name AS operator_name,
                      uc.display_name AS creator_name,
                      ua.display_name AS approver_name
               FROM inbound_order o
               LEFT JOIN user uo ON uo.id = o.operator_id
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
                      (l.storage_area || ' / ' || l.storage_position) AS location_code
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
                    "INSERT INTO batch (batch_no, sku_id, inbound_order_no) VALUES (?, ?, ?)",
                    (item["batch_no"], item["sku_id"], order_no),
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


@app.route("/inbound/<order_no>/cancel", methods=["POST"])
@login_required
def inbound_cancel(order_no):
    """撤销入库单：仅 draft 状态。已 approved 的不允许（库存已入账）。"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM inbound_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order:
            flash("入库单不存在", "error")
            return redirect(url_for("inbound_list"))
        if order["status"] != "draft":
            flash(f"只能撤销草稿状态的入库单（当前：{order['status']}）。已审批的请走报损流程。", "error")
            return redirect(url_for("inbound_list"))
        # 仅创建人或 admin/manager 可撤销
        if order["creator_id"] != session["user_id"] and session.get("role") not in ("admin", "manager"):
            flash("只有创建人或管理员可撤销", "error")
            return redirect(url_for("inbound_list"))
        conn.execute(
            "UPDATE inbound_order SET status='cancelled' WHERE order_no=?",
            (order_no,),
        )
    flash(f"入库单 {order_no} 已撤销", "success")
    return redirect(url_for("inbound_list"))


# ============ 库存 ============

@app.route("/inventory")
@login_required
def inventory_list():
    """v19: 统一查询 (P2 方案)。

    顶部 4 常用 + 高级展开两栏（物品蓝 / 仓库橙）。共 16 个筛选维度，
    动态拼 WHERE 条件，按"物品+库位+批次"组合行展示（每行一个 inventory 行）。
    """
    # 16 个筛选参数（请求 URL）
    f = {
        "q":                request.args.get("q", "").strip(),
        # 顶部 4 常用
        "warehouse_id":     request.args.get("warehouse_id", "").strip(),
        "use_dept_id":      request.args.get("use_dept_id", "").strip(),
        "category_major_id":request.args.get("category_major_id", "").strip(),
        # 物品高级（6）
        "sku_code":         request.args.get("sku_code", "").strip(),
        "brand":            request.args.get("brand", "").strip(),
        "usage_purpose":    request.args.get("usage_purpose", "").strip(),
        "category_id":      request.args.get("category_id", "").strip(),
        "owner_party_id":   request.args.get("owner_party_id", "").strip(),
        "owner_admin_id":   request.args.get("owner_admin_id", "").strip(),
        # 仓库高级（6）
        "storage_area":     request.args.get("storage_area", "").strip(),
        "storage_position": request.args.get("storage_position", "").strip(),
        "location_id":      request.args.get("location_id", "").strip(),
        "alloc_dept_id":    request.args.get("alloc_dept_id", "").strip(),
        "wh_type_id":       request.args.get("wh_type_id", "").strip(),
        "owner_user_id":    request.args.get("owner_user_id", "").strip(),
    }

    where = []
    params = []
    if f["q"]:
        where.append("(s.name LIKE ? OR s.code LIKE ? OR s.brand LIKE ?)")
        params.extend([f"%{f['q']}%", f"%{f['q']}%", f"%{f['q']}%"])
    if f["warehouse_id"]:
        where.append("l.warehouse_id = ?"); params.append(int(f["warehouse_id"]))
    if f["use_dept_id"]:
        where.append("l.use_dept_id = ?"); params.append(int(f["use_dept_id"]))
    if f["category_major_id"]:
        where.append("s.category_major_id = ?"); params.append(int(f["category_major_id"]))
    if f["sku_code"]:
        where.append("s.code LIKE ?"); params.append(f"%{f['sku_code']}%")
    if f["brand"]:
        where.append("s.brand LIKE ?"); params.append(f"%{f['brand']}%")
    if f["usage_purpose"]:
        where.append("s.usage_purpose LIKE ?"); params.append(f"%{f['usage_purpose']}%")
    if f["category_id"]:
        where.append("s.category_id = ?"); params.append(int(f["category_id"]))
    if f["owner_party_id"]:
        where.append("s.owner_party_id = ?"); params.append(int(f["owner_party_id"]))
    if f["owner_admin_id"]:
        where.append("s.owner_admin_id = ?"); params.append(int(f["owner_admin_id"]))
    if f["storage_area"]:
        where.append("l.storage_area LIKE ?"); params.append(f"%{f['storage_area']}%")
    if f["storage_position"]:
        where.append("l.storage_position LIKE ?"); params.append(f"%{f['storage_position']}%")
    if f["location_id"]:
        where.append("l.id = ?"); params.append(int(f["location_id"]))
    if f["alloc_dept_id"]:
        where.append("l.alloc_dept_id = ?"); params.append(int(f["alloc_dept_id"]))
    if f["wh_type_id"]:
        where.append("l.wh_type_id = ?"); params.append(int(f["wh_type_id"]))
    if f["owner_user_id"]:
        where.append("l.resp_owner_id = ?"); params.append(int(f["owner_user_id"]))

    sql = """
        SELECT i.id AS inv_id, i.on_hand, i.reserved,
               s.id AS sku_id, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, s.brand,
               s.safety_stock, s.unit AS sku_unit,
               icm.name AS category_major_name,
               ic.name AS category_name,
               op.name AS owner_party_name,
               oa.name AS owner_admin_name,
               w.id AS wh_id, w.name AS wh_name,
               l.id AS loc_id, (l.storage_area || ' / ' || l.storage_position) AS loc_code, l.storage_area, l.storage_position,
               wad.name AS alloc_dept_name,
               wud.name AS use_dept_name,
               wt.name AS wh_type_name,
               wo.name AS owner_user_name
        FROM inventory i
        JOIN sku s ON s.id = i.sku_id
        JOIN location l ON l.id = i.location_id
        LEFT JOIN warehouse w ON w.id = l.warehouse_id
        LEFT JOIN item_category_major icm ON icm.id = s.category_major_id
        LEFT JOIN item_category ic ON ic.id = s.category_id
        LEFT JOIN owner_party op ON op.id = s.owner_party_id
        LEFT JOIN owner_admin oa ON oa.id = s.owner_admin_id
        LEFT JOIN wh_alloc_dept wad ON wad.id = l.alloc_dept_id
        LEFT JOIN wh_use_dept wud ON wud.id = l.use_dept_id
        LEFT JOIN wh_type wt ON wt.id = l.wh_type_id
        LEFT JOIN wh_owner wo ON wo.id = l.resp_owner_id
        WHERE i.on_hand > 0
    """
    if where:
        sql += " AND " + " AND ".join(where)
    sql += " ORDER BY s.name, l.storage_area, l.storage_position"

    with database.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        total_inv_qty = sum(r["on_hand"] for r in rows) if rows else 0
        unique_skus = len(set(r["sku_id"] for r in rows)) if rows else 0
        unique_locs = len(set(r["loc_id"] for r in rows)) if rows else 0
        low_count = sum(1 for r in rows if r["safety_stock"] and r["on_hand"] < r["safety_stock"])

    summary = {
        "row_count": len(rows),
        "total_on_hand": total_inv_qty,
        "unique_skus": unique_skus,
        "unique_locs": unique_locs,
        "low_count": low_count,
    }
    pg = _pagination(len(rows))
    page_rows = rows[pg["offset"]:pg["offset"] + pg["per_page"]]
    return render_template("inventory_list.html", rows=page_rows, f=f, summary=summary, pg=pg)


@app.route("/inventory/warehouse/<int:warehouse_id>")
@login_required
def inventory_warehouse(warehouse_id):
    """仓库下钻：该仓库下所有库位 + 物品在仓"""
    with database.get_conn() as conn:
        wh = conn.execute("SELECT * FROM warehouse WHERE id=?", (warehouse_id,)).fetchone()
        if not wh:
            flash("仓库不存在", "error")
            return redirect(url_for("inventory_list", view="by_warehouse"))
        items = conn.execute(
            """SELECT s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, s.unit,
                      b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code,
                      NULL AS area_name,
                      i.on_hand, i.reserved
               FROM inventory i
               JOIN sku s ON s.id = i.sku_id
               JOIN batch b ON b.id = i.batch_id
               JOIN location l ON l.id = i.location_id
               WHERE l.warehouse_id = ? AND i.on_hand > 0
               ORDER BY l.storage_area, l.storage_position, s.name""",
            (warehouse_id,),
        ).fetchall()
        locs = conn.execute(
            """SELECT l.id, (l.storage_area || ' / ' || l.storage_position) AS code, NULL AS area_name
               FROM location l
               WHERE l.warehouse_id=? ORDER BY l.storage_area, l.storage_position""",
            (warehouse_id,),
        ).fetchall()
    return render_template("inventory_warehouse.html", wh=wh, items=items, locs=locs)


@app.route("/inventory/location/<int:location_id>")
@login_required
def inventory_location(location_id):
    """库位下钻：该库位下所有物品+批次"""
    with database.get_conn() as conn:
        loc = conn.execute(
            """SELECT l.*, w.name AS wh_name, NULL AS area_name
               FROM location l
               LEFT JOIN warehouse w ON w.id = l.warehouse_id
               WHERE l.id=?""",
            (location_id,),
        ).fetchone()
        if not loc:
            flash("库位不存在", "error")
            return redirect(url_for("inventory_list", view="by_location"))
        items = conn.execute(
            """SELECT s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, s.unit,
                      b.batch_no, i.on_hand, i.reserved
               FROM inventory i
               JOIN sku s ON s.id = i.sku_id
               JOIN batch b ON b.id = i.batch_id
               WHERE i.location_id = ? AND i.on_hand > 0
               ORDER BY s.name, b.id""",
            (location_id,),
        ).fetchall()
    return render_template("inventory_location.html", loc=loc, items=items)


# v16: inventory_area 路由已砸（存储区整体砸掉）


class StockNotEnoughError(Exception):
    pass


def _allocate_fifo(conn, sku_id, qty_needed):
    """FIFO 算法：按批次入库顺序（batch.id 升序，老批次先出），返回 [(batch_id, location_id, take_qty, inv_id), ...]"""
    batches = conn.execute(
        """SELECT inv.id AS inv_id, inv.batch_id, inv.location_id,
                  inv.on_hand, inv.reserved
           FROM inventory inv
           WHERE inv.sku_id = ? AND (inv.on_hand - inv.reserved) > 0
           ORDER BY inv.batch_id ASC, inv.id ASC""",
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


@app.route("/outbound/new", methods=["GET", "POST"])
@login_required
def outbound_new():
    """手动建出库单：选 SKU+数量，自动 FIFO 分批次。"""
    with database.get_conn() as conn:
        skus = conn.execute("SELECT * FROM sku ORDER BY code").fetchall()
        if request.method == "POST":
            receiver_desc = request.form.get("receiver_desc", "").strip()
            note = request.form.get("note", "").strip()
            operator_raw = request.form.get("operator_id", "").strip()
            if not operator_raw:
                flash("出库员工必填", "error")
                return render_template("outbound_form.html", skus=skus,
                                       receiver_desc=receiver_desc, note=note)
            operator_id = int(operator_raw)
            sku_ids = request.form.getlist("sku_id[]")
            quantities = request.form.getlist("quantity[]")
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
                valid_rows.append({"sku_id": int(sku_ids[i]), "quantity": qty})
            if not valid_rows:
                flash("至少要填一行物品明细", "error")
                return render_template("outbound_form.html", skus=skus,
                                       receiver_desc=receiver_desc, note=note)

            # FIFO 校验 + 分配（任一不足整单失败）
            plan = []
            for row in valid_rows:
                allocations = _allocate_fifo(conn, row["sku_id"], row["quantity"])
                if allocations is None:
                    sku = conn.execute("SELECT code, name FROM sku WHERE id=?", (row["sku_id"],)).fetchone()
                    flash(f"库存不足：{sku['code']} {sku['name']} 需要 {row['quantity']}", "error")
                    return render_template("outbound_form.html", skus=skus,
                                           receiver_desc=receiver_desc, note=note)
                plan.append((row, allocations))

            order_no = database.gen_order_no(conn, "CKD", "outbound_order")
            import attachments as _att
            import json as _json
            try:
                paths = _att.save_files(request.files.getlist("attachments[]"), "outbound", order_no)
            except ValueError as e:
                flash(str(e), "error")
                return render_template("outbound_form.html", skus=skus,
                                       receiver_desc=receiver_desc, note=note)
            conn.execute(
                "INSERT INTO outbound_order (order_no, operator_id, status, receiver_desc, note, attachments) VALUES (?, ?, 'pending', ?, ?, ?)",
                (order_no, operator_id, receiver_desc, note,
                 _json.dumps(paths, ensure_ascii=False) if paths else None),
            )
            for row, allocations in plan:
                for batch_id, location_id, take, inv_id in allocations:
                    conn.execute(
                        "UPDATE inventory SET reserved=reserved+?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (take, inv_id),
                    )
                    conn.execute(
                        "INSERT INTO outbound_item (order_no, sku_id, batch_id, location_id, quantity) VALUES (?, ?, ?, ?, ?)",
                        (order_no, row["sku_id"], batch_id, location_id, take),
                    )
            flash(f"出库单 {order_no} 已创建，库存已预占。", "success")
            return redirect(url_for("pick_detail", order_no=order_no))
    return render_template("outbound_form.html", skus=skus)


# ============ 领用申请单 UI 原型预览（展示型：只读、未接库，落库逻辑留待下一步）============

def _requisition_preview_data():
    """领用申请单原型页的下拉数据源（全部只读查询，不落库）。

    返回的都是 list[dict]，方便模板 |tojson 传到前端做 inline 搜索过滤。
    wh_use_dept / item_category_major 为空表时给 fallback，保证原型页有内容可演示。
    """
    from datetime import datetime
    with database.get_conn() as conn:
        users = [dict(r) for r in conn.execute(
            "SELECT id, display_name, username FROM user ORDER BY display_name"
        ).fetchall()]
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
    # 空表 fallback（仅原型演示用，标注 is_sample 供模板提示）
    dept_is_sample = not depts
    if dept_is_sample:
        depts = [{"id": None, "name": n} for n in
                 ("行政部", "技术部", "财务部", "市场部", "仓储部", "采购部")]
    cat_is_sample = not categories
    if cat_is_sample:
        categories = [{"id": None, "name": n} for n in
                      ("办公用品", "IT 设备", "耗材", "招待用品")]
    return {
        "users": users, "depts": depts, "locations": locations,
        "categories": categories, "skus": skus,
        "dept_is_sample": dept_is_sample, "cat_is_sample": cat_is_sample,
        "today": datetime.now().strftime("%Y-%m-%d"),
    }


@app.route("/requisition/preview-a")
@login_required
def requisition_preview_a():
    return render_template("requisition_form_a.html", **_requisition_preview_data())


@app.route("/requisition/preview-b")
@login_required
def requisition_preview_b():
    return render_template("requisition_form_b.html", **_requisition_preview_data())


@app.route("/requisition/preview-c")
@login_required
def requisition_preview_c():
    return render_template("requisition_form_c.html", **_requisition_preview_data())


# ============ 领用申请单：后台查看列表（管理员，登录）============

@app.route("/requisition")
@login_required
def requisition_list():
    """领用申请单列表 + 多维度筛选。数据由独立的免登录申请门户(apply_app.py)写入。"""
    f = {
        "applicant": request.args.get("applicant", "").strip(),
        "dept":      request.args.get("dept", "").strip(),
        "location":  request.args.get("location", "").strip(),
        "category":  request.args.get("category", "").strip(),
        "sku":       request.args.get("sku", "").strip(),
        "date_from": request.args.get("date_from", "").strip(),
        "date_to":   request.args.get("date_to", "").strip(),
        "status":    request.args.get("status", "").strip(),
    }
    where, params = [], []
    if f["applicant"]:
        where.append("ro.applicant LIKE ?"); params.append(f"%{f['applicant']}%")
    if f["dept"]:
        where.append("IFNULL(ro.dept_name,'') LIKE ?"); params.append(f"%{f['dept']}%")
    if f["location"]:
        where.append("IFNULL(ro.location_code,'') LIKE ?"); params.append(f"%{f['location']}%")
    if f["category"]:
        where.append("IFNULL(ro.category_name,'') LIKE ?"); params.append(f"%{f['category']}%")
    if f["date_from"]:
        where.append("ro.apply_date >= ?"); params.append(f["date_from"])
    if f["date_to"]:
        where.append("ro.apply_date <= ?"); params.append(f["date_to"])
    if f["status"]:
        where.append("ro.status = ?"); params.append(f["status"])
    if f["sku"]:
        where.append(
            "EXISTS (SELECT 1 FROM requisition_item ri WHERE ri.order_no = ro.order_no "
            "AND (IFNULL(ri.sku_name,'') LIKE ? OR IFNULL(ri.sku_code,'') LIKE ?))"
        )
        params.append(f"%{f['sku']}%"); params.append(f"%{f['sku']}%")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = "SELECT ro.* FROM requisition_order ro" + where_sql + " ORDER BY ro.id DESC LIMIT ? OFFSET ?"

    items_by = {}
    outbound_by = {}  # requisition order_no -> 已生成的出库单号（handled 单子用）
    with database.get_conn() as conn:
        filtered_total = conn.execute(
            "SELECT COUNT(*) AS c FROM requisition_order ro" + where_sql, params
        ).fetchone()["c"]
        pg = _pagination(filtered_total)
        orders = conn.execute(sql, params + [pg["per_page"], pg["offset"]]).fetchall()
        if orders:
            nos = [o["order_no"] for o in orders]
            qm = ",".join("?" * len(nos))
            for r in conn.execute(
                f"SELECT * FROM requisition_item WHERE order_no IN ({qm}) ORDER BY id", nos
            ).fetchall():
                items_by.setdefault(r["order_no"], []).append(r)
            for r in conn.execute(
                f"SELECT order_no, requisition_no FROM outbound_order WHERE requisition_no IN ({qm})", nos
            ).fetchall():
                outbound_by[r["requisition_no"]] = r["order_no"]
        total_all = conn.execute("SELECT COUNT(*) AS c FROM requisition_order").fetchone()["c"]
        depts = [dict(r) for r in conn.execute("SELECT name FROM wh_use_dept ORDER BY name").fetchall()]
        cats = [dict(r) for r in conn.execute("SELECT name FROM item_category_major ORDER BY name").fetchall()]

    return render_template("requisition_list.html", orders=orders, items_by=items_by,
                           outbound_by=outbound_by, pg=pg,
                           f=f, total_all=total_all, depts=depts, cats=cats)


@app.route("/requisition/<order_no>/reject", methods=["POST"])
@login_required
def requisition_reject(order_no):
    """驳回领用申请：仅 submitted 可驳回，置 rejected，不动库存。"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM requisition_order WHERE order_no = ?", (order_no,)).fetchone()
        if not order:
            flash("申请单不存在", "error")
            return redirect(url_for("requisition_list"))
        if order["status"] != "submitted":
            flash("只能驳回待处理的申请单", "error")
            return redirect(url_for("requisition_list"))
        conn.execute(
            "UPDATE requisition_order SET status='rejected' WHERE order_no=?",
            (order_no,),
        )
    flash(f"申请单 {order_no} 已驳回", "success")
    return redirect(url_for("requisition_list"))


@app.route("/requisition/<order_no>/confirm-outbound", methods=["POST"])
@login_required
def requisition_confirm_outbound(order_no):
    """申领转出库（一步实扣）：全单校验——每行须匹配到系统 SKU 且 FIFO 库存足额，
    同一 SKU 多行合并核量；任一不过则整单拦截、库存一动不动。全过则生成一张
    status=completed 的出库单、FIFO 写明细、实扣 inventory.on_hand、写 stock_log，
    并把领用申请置为 handled。整个过程在一个事务里，异常自动回滚。"""
    with database.get_conn() as conn:
        req = conn.execute("SELECT * FROM requisition_order WHERE order_no = ?", (order_no,)).fetchone()
        if not req:
            flash("领用申请单不存在", "error")
            return redirect(url_for("requisition_list"))
        if req["status"] != "submitted":
            flash(f"该申请单已是 {req['status']} 状态，不能重复出库确认", "error")
            return redirect(url_for("requisition_list"))

        items = conn.execute(
            "SELECT * FROM requisition_item WHERE order_no = ? ORDER BY id", (order_no,)
        ).fetchall()
        if not items:
            flash("该申请单没有任何物品明细，无法出库", "error")
            return redirect(url_for("requisition_list"))

        # ---- 全单校验：收集所有问题，任一不过整单拦截 ----
        problems = []
        demand = {}      # sku_id -> 累计需求量（同一 SKU 多行合并）
        sku_info = {}    # sku_id -> (code, name)
        for it in items:
            disp = it["sku_name"] or it["sku_code"] or "（未命名）"
            if not it["sku_id"]:
                problems.append(f"「{disp}」未对应到系统物品(SKU)")
                continue
            sku = conn.execute("SELECT id, code, name FROM sku WHERE id = ?", (it["sku_id"],)).fetchone()
            if not sku:
                problems.append(f"「{disp}」对应的系统物品已不存在")
                continue
            demand[it["sku_id"]] = demand.get(it["sku_id"], 0) + it["quantity"]
            sku_info[it["sku_id"]] = (sku["code"], sku["name"])

        plan = []  # [(sku_id, allocations)]
        if not problems:
            for sid, qty in demand.items():
                allocations = _allocate_fifo(conn, sid, qty)
                if allocations is None:
                    code, nm = sku_info[sid]
                    problems.append(f"「{code} {nm}」库存不足，需 {qty}")
                else:
                    plan.append((sid, allocations))

        if problems:
            flash("出库确认被拦截，请先处理：" + "；".join(problems), "error")
            return redirect(url_for("requisition_list"))

        # ---- 全过：生成已完成出库单 + 实扣库存 ----
        uid = session["user_id"]
        receiver = req["applicant"] + (f" · {req['dept_name']}" if req["dept_name"] else "")
        ob_no = database.gen_order_no(conn, "CKD", "outbound_order")
        conn.execute(
            "INSERT INTO outbound_order (order_no, operator_id, picker_id, status, receiver_desc, note, requisition_no, completed_at) "
            "VALUES (?, ?, ?, 'completed', ?, ?, ?, CURRENT_TIMESTAMP)",
            (ob_no, uid, uid, receiver, f"由领用申请 {order_no} 转出库", order_no),
        )
        for sid, allocations in plan:
            for batch_id, location_id, take, inv_id in allocations:
                conn.execute(
                    "INSERT INTO outbound_item (order_no, sku_id, batch_id, location_id, quantity) VALUES (?, ?, ?, ?, ?)",
                    (ob_no, sid, batch_id, location_id, take),
                )
                conn.execute(
                    "INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id) "
                    "VALUES (?, ?, ?, ?, ?, 'outbound', ?)",
                    (sid, batch_id, location_id, -take, ob_no, uid),
                )
                conn.execute(
                    "UPDATE inventory SET on_hand = on_hand - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (take, inv_id),
                )
        conn.execute("UPDATE requisition_order SET status = 'handled' WHERE order_no = ?", (order_no,))

    flash(f"出库确认完成，已生成出库单 {ob_no} 并实扣库存。", "success")
    return redirect(url_for("requisition_list"))


# ============ 出库 ============

@app.route("/pick")
@login_required
def pick_list():
    f = {
        "order_no":          request.args.get("order_no", "").strip(),
        "receiver":          request.args.get("receiver", "").strip(),
        "item":              request.args.get("item", "").strip(),
        "category_major_id": request.args.get("category_major_id", "").strip(),
        "warehouse_id":      request.args.get("warehouse_id", "").strip(),
        "operator":          request.args.get("operator", "").strip(),
        "status":            request.args.get("status", "").strip(),
        "date_from":         request.args.get("date_from", "").strip(),
        "date_to":           request.args.get("date_to", "").strip(),
    }
    where, params = [], []
    if f["order_no"]:
        where.append("ob.order_no LIKE ?"); params.append(f"%{f['order_no']}%")
    if f["receiver"]:
        where.append("IFNULL(ob.receiver_desc,'') LIKE ?"); params.append(f"%{f['receiver']}%")
    if f["item"]:
        where.append("EXISTS (SELECT 1 FROM outbound_item oi JOIN sku s ON s.id=oi.sku_id "
                     "WHERE oi.order_no=ob.order_no AND (s.name LIKE ? OR s.code LIKE ?))")
        params += [f"%{f['item']}%", f"%{f['item']}%"]
    if f["category_major_id"]:
        where.append("EXISTS (SELECT 1 FROM outbound_item oi JOIN sku s ON s.id=oi.sku_id "
                     "WHERE oi.order_no=ob.order_no AND s.category_major_id=?)")
        params.append(f["category_major_id"])
    if f["warehouse_id"]:
        where.append("EXISTS (SELECT 1 FROM outbound_item oi JOIN location l ON l.id=oi.location_id "
                     "WHERE oi.order_no=ob.order_no AND l.warehouse_id=?)")
        params.append(f["warehouse_id"])
    if f["operator"]:
        where.append("IFNULL(up.display_name,'') LIKE ?"); params.append(f"%{f['operator']}%")
    if f["status"]:
        where.append("ob.status = ?"); params.append(f["status"])
    if f["date_from"]:
        where.append("date(ob.created_at) >= ?"); params.append(f["date_from"])
    if f["date_to"]:
        where.append("date(ob.created_at) <= ?"); params.append(f["date_to"])
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    join = " FROM outbound_order ob LEFT JOIN user up ON up.id = ob.picker_id"
    base = ("SELECT ob.*, ob.receiver_desc, up.display_name AS picker_name, "
            "(SELECT COUNT(*) FROM outbound_item WHERE order_no = ob.order_no) AS line_count, "
            "(SELECT COALESCE(SUM(quantity),0) FROM outbound_item WHERE order_no = ob.order_no) AS total_qty, "
            "(SELECT COALESCE(SUM(reversed_qty),0) FROM outbound_item WHERE order_no = ob.order_no) AS reversed_qty"
            + join)
    order_clause = " ORDER BY (CASE ob.status WHEN 'pending' THEN 0 ELSE 1 END), ob.id DESC"
    with database.get_conn() as conn:
        filtered_total = conn.execute(
            "SELECT COUNT(*) AS c" + join + where_sql, params).fetchone()["c"]
        pg = _pagination(filtered_total)
        orders = conn.execute(base + where_sql + order_clause + " LIMIT ? OFFSET ?",
                              params + [pg["per_page"], pg["offset"]]).fetchall()
        order_summary = {}
        for o in orders:
            row = conn.execute(
                """SELECT GROUP_CONCAT(DISTINCT s.name) AS sku_names,
                          GROUP_CONCAT(DISTINCT w.name) AS wh_names
                   FROM outbound_item oi
                   JOIN sku s ON s.id = oi.sku_id
                   LEFT JOIN location l ON l.id = oi.location_id
                   LEFT JOIN warehouse w ON w.id = l.warehouse_id
                   WHERE oi.order_no = ?""",
                (o["order_no"],),
            ).fetchone()
            order_summary[o["order_no"]] = row
        warehouses = conn.execute("SELECT id, name FROM warehouse ORDER BY id").fetchall()
        cat_majors = conn.execute("SELECT id, name FROM item_category_major ORDER BY name").fetchall()
    return render_template("pick_list.html", orders=orders, order_summary=order_summary, f=f, pg=pg,
                           warehouses=warehouses, cat_majors=cat_majors)


@app.route("/pick/<order_no>")
@login_required
def pick_detail(order_no):
    with database.get_conn() as conn:
        ob = conn.execute(
            """SELECT ob.*, ob.receiver_desc, ob.note, up.display_name AS picker_name
               FROM outbound_order ob
               LEFT JOIN user up ON up.id = ob.picker_id
               WHERE ob.order_no = ?""",
            (order_no,),
        ).fetchone()
        if not ob:
            flash("出库单不存在", "error")
            return redirect(url_for("pick_list"))
        items = conn.execute(
            """SELECT oi.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code
               FROM outbound_item oi
               JOIN sku s ON s.id = oi.sku_id
               JOIN batch b ON b.id = oi.batch_id
               JOIN location l ON l.id = oi.location_id
               WHERE oi.order_no = ?
               ORDER BY l.storage_area, l.storage_position, b.id""",
            (order_no,),
        ).fetchall()
    return render_template("pick_detail.html", ob=ob, items=items)


@app.route("/pick/<order_no>/complete", methods=["POST"])
@login_required
def pick_complete(order_no):
    """出库完成 → 实扣库存 + 写流水"""
    with database.get_conn() as conn:
        ob = conn.execute("SELECT * FROM outbound_order WHERE order_no = ?", (order_no,)).fetchone()
        if not ob:
            flash("出库单不存在", "error")
            return redirect(url_for("pick_list"))
        if ob["status"] != "pending":
            flash(f"该单已经是 {ob['status']} 状态，不能重复完成", "error")
            return redirect(url_for("pick_detail", order_no=order_no))

        items = conn.execute("SELECT * FROM outbound_item WHERE order_no = ?", (order_no,)).fetchall()
        for it in items:
            conn.execute(
                """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id)
                   VALUES (?, ?, ?, ?, ?, 'outbound', ?)""",
                (it["sku_id"], it["batch_id"], it["location_id"], -it["quantity"], order_no, session["user_id"]),
            )
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

    flash(f"出库单 {order_no} 已完成，库存已实扣", "success")
    return redirect(url_for("pick_detail", order_no=order_no))


@app.route("/pick/<order_no>/reverse", methods=["POST"])
@login_required
def pick_reverse(order_no):
    """出库后退回（部分撤销）：每行可指定撤销数量，自动回到原批次原库位。"""
    with database.get_conn() as conn:
        ob = conn.execute("SELECT * FROM outbound_order WHERE order_no = ?", (order_no,)).fetchone()
        if not ob:
            flash("出库单不存在", "error")
            return redirect(url_for("pick_list"))
        if ob["status"] != "completed":
            flash("只能撤销已完成的出库单", "error")
            return redirect(url_for("pick_detail", order_no=order_no))

        items = conn.execute("SELECT * FROM outbound_item WHERE order_no = ?", (order_no,)).fetchall()
        any_reversed = False
        for it in items:
            raw = request.form.get(f"reverse_qty_{it['id']}", "").strip()
            if not raw:
                continue
            try:
                rq = int(raw)
            except ValueError:
                continue
            if rq <= 0:
                continue
            remaining = it["quantity"] - (it["reversed_qty"] or 0)
            if rq > remaining:
                flash(f"行 #{it['id']} 撤销 {rq} 超过剩余 {remaining}", "error")
                return redirect(url_for("pick_detail", order_no=order_no))
            # 写流水（+N, return_in）
            conn.execute(
                """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id, note)
                   VALUES (?, ?, ?, ?, ?, 'return_in', ?, ?)""",
                (it["sku_id"], it["batch_id"], it["location_id"], rq, order_no, session["user_id"], "出库撤销"),
            )
            # 回库
            ex = conn.execute(
                "SELECT id FROM inventory WHERE sku_id=? AND batch_id=? AND location_id=?",
                (it["sku_id"], it["batch_id"], it["location_id"]),
            ).fetchone()
            if ex:
                conn.execute(
                    "UPDATE inventory SET on_hand = on_hand + ?, updated_at = CURRENT_TIMESTAMP WHERE id=?",
                    (rq, ex["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO inventory (sku_id, batch_id, location_id, on_hand) VALUES (?, ?, ?, ?)",
                    (it["sku_id"], it["batch_id"], it["location_id"], rq),
                )
            conn.execute(
                "UPDATE outbound_item SET reversed_qty = COALESCE(reversed_qty,0) + ? WHERE id=?",
                (rq, it["id"]),
            )
            any_reversed = True

        if any_reversed:
            flash("已撤销并回库", "success")
        else:
            flash("未填写任何撤销数量", "error")
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
                      b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code,
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
    pg = _pagination(len(rows))
    rows = rows[pg["offset"]:pg["offset"] + pg["per_page"]]
    return render_template("damage_list.html", rows=rows, reason_label=DAMAGE_REASON_LABEL, pg=pg)


@app.route("/damage/new", methods=["GET", "POST"])
@login_required
def damage_new():
    with database.get_conn() as conn:
        # 只能对有库存的批次报损
        invs = conn.execute(
            """SELECT inv.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code
               FROM inventory inv
               JOIN sku s ON s.id = inv.sku_id
               JOIN batch b ON b.id = inv.batch_id
               JOIN location l ON l.id = inv.location_id
               WHERE inv.on_hand > 0
               ORDER BY s.code, b.id""",
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
            cur = conn.execute(
                """INSERT INTO damage_log (sku_id, batch_id, location_id, quantity, reason_type, reason_note, applicant_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (inv["sku_id"], inv["batch_id"], inv["location_id"], qty, reason_type, reason_note, session["user_id"]),
            )
            damage_id = cur.lastrowid
            # 附件保存
            import attachments as _att
            import json as _json
            try:
                paths = _att.save_files(request.files.getlist("attachments[]"), "damage", damage_id)
            except ValueError as e:
                flash(str(e), "error")
                return render_template("damage_form.html", invs=invs, reasons=DAMAGE_REASON_LABEL)
            if paths:
                conn.execute("UPDATE damage_log SET attachments=? WHERE id=?",
                             (_json.dumps(paths, ensure_ascii=False), damage_id))
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
    pg = _pagination(len(orders))
    orders = orders[pg["offset"]:pg["offset"] + pg["per_page"]]
    return render_template("stocktake_list.html", orders=orders, pg=pg)


@app.route("/stocktake/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def stocktake_new():
    """创建盘点单 = 对当前所有 on_hand > 0 的 inventory 做快照。

    v13: GET 渲染独立表单页（P1 完整向导风格），POST 创建后跳详情。
    """
    if request.method == "GET":
        with database.get_conn() as conn:
            total_inv = conn.execute("SELECT COUNT(*) AS c FROM inventory WHERE on_hand > 0").fetchone()["c"]
            wh_count = conn.execute("SELECT COUNT(*) AS c FROM warehouse").fetchone()["c"]
            area_count = 0  # v16: 存储区已砸
            loc_count = conn.execute("SELECT COUNT(*) AS c FROM location").fetchone()["c"]
            stocktakers = conn.execute(
                "SELECT id, display_name FROM user WHERE position IS NOT NULL AND TRIM(position) != '' ORDER BY display_name"
            ).fetchall()
        return render_template(
            "stocktake_form.html",
            total_inv=total_inv, wh_count=wh_count, area_count=area_count, loc_count=loc_count,
            stocktakers=stocktakers,
        )
    title = request.form.get("title", "").strip() or "盘点"
    note = request.form.get("note", "").strip()
    with database.get_conn() as conn:
        order_no = database.gen_order_no(conn, "PDD", "stocktake_order")
        conn.execute(
            "INSERT INTO stocktake_order (order_no, title, scope, status, creator_id, note) VALUES (?, ?, 'all', 'open', ?, ?)",
            (order_no, title, session["user_id"], note),
        )
        # 附件
        import attachments as _att
        import json as _json
        try:
            paths = _att.save_files(request.files.getlist("attachments[]"), "stocktake", order_no)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("stocktake_list"))
        if paths:
            conn.execute("UPDATE stocktake_order SET attachments=? WHERE order_no=?",
                         (_json.dumps(paths, ensure_ascii=False), order_no))
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
                      b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code
               FROM stocktake_item sti
               JOIN sku s ON s.id = sti.sku_id
               JOIN batch b ON b.id = sti.batch_id
               JOIN location l ON l.id = sti.location_id
               WHERE sti.order_no = ?
               ORDER BY l.storage_area, l.storage_position, s.code, b.id""",
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


# ============ 报表 Dashboard（v22 砍：经营报表已从菜单移除，路由保留向后兼容旧书签） ============

@app.route("/dashboard")
@login_required
def dashboard():
    flash("「经营报表」已下线，相关数据请到「查询分析 → 库存查询 / 库存流水 / 库存预测」查看", "info")
    return redirect(url_for("inventory_list"))


def _dashboard_disabled():
    period = request.args.get("period", "30d")
    if period not in ("30d", "12mo"):
        period = "30d"

    # 日期范围筛选（仅影响"近 N 天流水类型分布"饼图；其他实时快照不受日期影响）
    from datetime import date, timedelta
    date_from = request.args.get("date_from") or (date.today() - timedelta(days=29)).isoformat()
    date_to = request.args.get("date_to") or date.today().isoformat()

    with database.get_conn() as conn:
        # 1. 库存 TOP 10 物品
        inv_top = conn.execute(
            """SELECT s.code, s.name, COALESCE(SUM(i.on_hand), 0) AS total
               FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.id
               ORDER BY total DESC LIMIT 10"""
        ).fetchall()

        # 2. 库存按存储区分布（饼图，按 location 聚合）
        zone_dist = conn.execute(
            """SELECT (l.storage_area || ' / ' || l.storage_position) AS zone, COALESCE(SUM(i.on_hand), 0) AS total
               FROM location l LEFT JOIN inventory i ON i.location_id = l.id
               GROUP BY l.id HAVING total > 0 ORDER BY l.storage_area, l.storage_position"""
        ).fetchall()

        # 3. 流水类型分布（饼图，受日期筛选影响）
        log_type = conn.execute(
            """SELECT event_type, COUNT(*) AS cnt
               FROM stock_log
               WHERE date(occurred_at) BETWEEN ? AND ?
               GROUP BY event_type""",
            (date_from, date_to),
        ).fetchall()

        # 4. 库存健康度饼图（低于安全库存 vs 充足）
        stock_health = conn.execute(
            """SELECT
                 SUM(CASE WHEN on_hand < safety_stock AND safety_stock > 0 THEN 1 ELSE 0 END) AS low,
                 SUM(CASE WHEN on_hand >= safety_stock OR safety_stock = 0 THEN 1 ELSE 0 END) AS ok
               FROM (
                   SELECT s.id, s.safety_stock, COALESCE(SUM(i.on_hand),0) AS on_hand
                   FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
                   GROUP BY s.id
               )"""
        ).fetchone()

        # 5. 出入库趋势（折线）
        if period == "12mo":
            inout = conn.execute(
                """SELECT strftime('%Y-%m', occurred_at) AS d,
                          SUM(CASE WHEN event_type IN ('inbound','return_in') THEN delta ELSE 0 END) AS in_qty,
                          SUM(CASE WHEN event_type IN ('outbound','damage') THEN -delta ELSE 0 END) AS out_qty
                   FROM stock_log
                   WHERE date(occurred_at) >= date('now','-365 days')
                   GROUP BY d ORDER BY d"""
            ).fetchall()
        else:
            inout = conn.execute(
                """SELECT date(occurred_at) AS d,
                          SUM(CASE WHEN event_type IN ('inbound','return_in') THEN delta ELSE 0 END) AS in_qty,
                          SUM(CASE WHEN event_type IN ('outbound','damage') THEN -delta ELSE 0 END) AS out_qty
                   FROM stock_log
                   WHERE date(occurred_at) >= date('now','-29 days')
                   GROUP BY d ORDER BY d"""
            ).fetchall()

        # 6. 活跃物品 TOP 10（近 30 天出库量）
        active_sku = conn.execute(
            """SELECT s.code, s.name, SUM(-sl.delta) AS out_qty
               FROM stock_log sl JOIN sku s ON s.id = sl.sku_id
               WHERE sl.event_type IN ('outbound','damage')
                 AND date(sl.occurred_at) >= date('now','-29 days')
               GROUP BY s.id ORDER BY out_qty DESC LIMIT 10"""
        ).fetchall()

    import json as _json
    charts = {
        "inv_top": {
            "labels": [r["code"] for r in inv_top],
            "data": [r["total"] for r in inv_top],
        },
        "zone_dist": {
            "labels": [r["zone"] for r in zone_dist if r["zone"]],
            "data": [r["total"] for r in zone_dist if r["zone"]],
        },
        "log_type": {
            "labels": [{"inbound":"入库","outbound":"出库","damage":"报损","adjust":"盘点调整","return_in":"退货入","transfer":"调拨"}.get(r["event_type"], r["event_type"]) for r in log_type],
            "data": [r["cnt"] for r in log_type],
        },
        "stock_health": {
            "labels": ["低于安全库存", "库存充足"],
            "data": [stock_health["low"] or 0, stock_health["ok"] or 0],
        },
        "inout_trend": {
            "labels": [r["d"] for r in inout],
            "in_data": [r["in_qty"] for r in inout],
            "out_data": [r["out_qty"] for r in inout],
        },
        "active_sku": {
            "labels": [r["code"] for r in active_sku],
            "data": [r["out_qty"] for r in active_sku],
        },
    }
    return render_template("dashboard.html",
                           charts_json=_json.dumps(charts),
                           period=period,
                           date_from=date_from,
                           date_to=date_to)


# ============ 数据导入（v22：通用 Excel / CSV 智能导入 + DeepSeek 多表分流） ============

@app.route("/data-import", methods=["GET", "POST"], endpoint="data_import")
@role_required("admin", "manager")
def data_import():
    """两步流程：
    GET / POST(无文件)  → 上传表单
    POST(含文件)         → 解析 + LLM 智能分析（多表分流）→ 预览页
    """
    import data_importer
    targets = data_importer.TARGETS
    if request.method == "POST" and request.files.get("file"):
        f = request.files["file"]
        filename = f.filename or ""
        parsed = data_importer.read_file(f, filename)
        if parsed["errors"]:
            for e in parsed["errors"]:
                flash(e, "error")
            return render_template("data_import.html", targets=targets, stage="upload")
        if not parsed["rows"]:
            flash("文件解析成功但没有数据行", "error")
            return render_template("data_import.html", targets=targets, stage="upload")
        # LLM 分析整张表（多目标表分流）
        llm = data_importer.llm_analyze_table(parsed["headers"], parsed["rows"][:5])
        if not llm.get("available"):
            flash(f"AI 分析未启用（{llm.get('error', '未知')}），降级到机械别名识别", "warn")
            # 降级：默认全列尝试映射到 sku
            fallback_map = data_importer.auto_map_fields(parsed["headers"], "sku")
            llm = {
                "available": False,
                "columns": [
                    {"header": h, "target_table": "sku" if fallback_map.get(h) else "skip",
                     "field": fallback_map.get(h) or "", "confidence": 0.5 if fallback_map.get(h) else 0.0,
                     "reason": "机械词典识别"}
                    for h in parsed["headers"]
                ],
                "multi_table_split": False,
                "summary": "AI 不可用，使用机械识别",
                "warnings": [],
            }
        # 整理字段下拉数据
        field_options_by_table = {tk: sorted(data_importer.FIELD_ALIASES.get(tk, {}).keys())
                                  for tk in targets}
        preview_rows = parsed["rows"][:10]
        return render_template(
            "data_import.html",
            targets=targets,
            stage="preview",
            llm=llm,
            headers=parsed["headers"],
            preview_rows=preview_rows,
            total_rows=len(parsed["rows"]),
            field_options_by_table=field_options_by_table,
        ), 200
    return render_template("data_import.html", targets=targets, stage="upload")


@app.route("/data-import/commit", methods=["POST"], endpoint="data_import_commit")
@role_required("admin", "manager")
def data_import_commit():
    """接收每列的 target_table + field 映射，重新上传文件 → 多表分流写入。"""
    import data_importer
    f = request.files.get("file")
    if not f or not f.filename:
        flash("请重新上传文件", "error")
        return redirect(url_for("data_import"))
    parsed = data_importer.read_file(f, f.filename)
    if parsed["errors"]:
        for e in parsed["errors"]:
            flash(e, "error")
        return redirect(url_for("data_import"))
    # 收集每列的 target_table + field
    columns_plan = []
    for h in parsed["headers"]:
        tk = request.form.get(f"table__{h}", "skip").strip()
        field = request.form.get(f"field__{h}", "").strip()
        columns_plan.append({"header": h, "target_table": tk, "field": field})
    with database.get_conn() as conn:
        summary = data_importer.import_multi_table(parsed["rows"], columns_plan, conn)
    if summary["total_success"] > 0:
        flash(f"成功导入 {summary['total_success']} 行（跨 {len(summary['tables'])} 个表）", "success")
    if summary["total_fail"] > 0:
        flash(f"失败 {summary['total_fail']} 行，详见结果页", "warn")
    return render_template(
        "data_import.html",
        targets=data_importer.TARGETS,
        stage="result",
        summary=summary,
    )


# ============ 销量预测 ============

@app.route("/forecast", methods=["GET"], endpoint="forecast_view")
@login_required
def forecast_view():
    import forecasting
    rows = list(forecasting.list_low_stock())
    return render_template("forecast.html", rows=rows)


@app.route("/forecast/run-alert", methods=["POST"], endpoint="forecast_alert_run")
@role_required("admin", "manager")
def forecast_alert_run():
    """手动触发低库存扫描 + 通知。"""
    import notifications
    results = notifications.scan_low_stock_and_alert()
    triggered = sum(n for _, n, *_ in results)
    flash(f"已触发低库存扫描：{triggered} 项命中" if triggered else "当前没有低于安全库存的物品", "success")
    return redirect(url_for("forecast_view"))


# ============ 备份管理（管理员） ============

@app.route("/backups", methods=["GET", "POST"], endpoint="backup_admin")
@role_required("admin")
def backup_admin():
    import backup as _backup
    if request.method == "POST":
        action = request.form.get("action")
        if action == "backup_now":
            path = _backup.backup_db()
            flash(f"备份完成：{path}" if path else "warehouse.db 不存在，无法备份", "success" if path else "error")
        elif action == "restore":
            filename = request.form.get("filename", "").strip()
            if not filename:
                flash("未指定备份文件", "error")
            else:
                ok, msg = _backup.restore_db(filename)
                flash(msg, "success" if ok else "error")
        elif action == "delete_backup":
            filename = request.form.get("filename", "").strip()
            from pathlib import Path as _Path
            safe = _Path(filename).name
            if safe.startswith("warehouse_") and safe.endswith(".db"):
                target = _backup.BACKUP_DIR / safe
                if target.exists():
                    try:
                        target.unlink()
                        flash(f"已删除 {safe}", "success")
                    except Exception as e:
                        flash(f"删除失败：{e}", "error")
                else:
                    flash("文件不存在", "error")
            else:
                flash("非法文件名", "error")
        elif action == "reset_demo":
            # 危险：清空业务流水，重置 SKU/供应商，重生成 30 天 demo
            _backup.backup_db()  # 先自动备份
            import reset_demo as _reset
            try:
                _reset.reset()
                flash("演示数据已重置（清空业务流水 + 重置 SKU/供应商 + 重生成 30 天 demo + 3 张待审入库 + 5 条待审报损）", "success")
            except Exception as e:
                flash(f"重置失败：{type(e).__name__}: {e}", "error")
        return redirect(url_for("backup_admin"))
    files = _backup.list_backups()
    return render_template("backup_admin.html", files=[(f.name, f.stat().st_size, f.stat().st_mtime) for f in files])


# ============ 库存流水 ============

@app.route("/stock-log")
@login_required
def stock_log_view():
    """v20: 11 个筛选条件 + P2 风顶部 + 高级展开。

    出入库 / 物品 / 物品编号 / 仓库 / 楼栋 / 楼层 / 库位 / 时间 / 操作人 / 类型 / 责任人 / 分配部门 / 使用部门
    + 结果展示附件 + 备注
    """
    f = {
        "q":             request.args.get("q", "").strip(),  # 单号 / 备注模糊
        "event_type":    request.args.get("event_type", "").strip(),  # inbound/outbound/transfer/damage/adjust/return_in
        "sku_id":        request.args.get("sku_id", "").strip(),
        "sku_code":      request.args.get("sku_code", "").strip(),
        "warehouse_id":  request.args.get("warehouse_id", "").strip(),
        "storage_area":     request.args.get("storage_area", "").strip(),
        "storage_position": request.args.get("storage_position", "").strip(),
        "location_id":   request.args.get("location_id", "").strip(),
        "operator_id":   request.args.get("operator_id", "").strip(),
        "owner_user_id": request.args.get("owner_user_id", "").strip(),
        "alloc_dept_id": request.args.get("alloc_dept_id", "").strip(),
        "use_dept_id":   request.args.get("use_dept_id", "").strip(),
        "date_from":     request.args.get("date_from", "").strip(),
        "date_to":       request.args.get("date_to", "").strip(),
    }
    where = []
    params = []
    if f["q"]:
        where.append("(sl.source_doc LIKE ? OR sl.note LIKE ?)")
        params.extend([f"%{f['q']}%", f"%{f['q']}%"])
    if f["event_type"]:
        where.append("sl.event_type = ?"); params.append(f["event_type"])
    if f["sku_id"]:
        where.append("sl.sku_id = ?"); params.append(int(f["sku_id"]))
    if f["sku_code"]:
        where.append("s.code LIKE ?"); params.append(f"%{f['sku_code']}%")
    if f["warehouse_id"]:
        where.append("l.warehouse_id = ?"); params.append(int(f["warehouse_id"]))
    if f["storage_area"]:
        where.append("l.storage_area LIKE ?"); params.append(f"%{f['storage_area']}%")
    if f["storage_position"]:
        where.append("l.storage_position LIKE ?"); params.append(f"%{f['storage_position']}%")
    if f["location_id"]:
        where.append("sl.location_id = ?"); params.append(int(f["location_id"]))
    if f["operator_id"]:
        where.append("sl.operator_id = ?"); params.append(int(f["operator_id"]))
    if f["owner_user_id"]:
        where.append("l.resp_owner_id = ?"); params.append(int(f["owner_user_id"]))
    if f["alloc_dept_id"]:
        where.append("l.alloc_dept_id = ?"); params.append(int(f["alloc_dept_id"]))
    if f["use_dept_id"]:
        where.append("l.use_dept_id = ?"); params.append(int(f["use_dept_id"]))
    if f["date_from"]:
        where.append("date(sl.occurred_at) >= ?"); params.append(f["date_from"])
    if f["date_to"]:
        where.append("date(sl.occurred_at) <= ?"); params.append(f["date_to"])

    sql = """
        SELECT sl.id, sl.sku_id, sl.delta, sl.event_type, sl.source_doc, sl.note, sl.occurred_at,
               s.code AS sku_code, s.name AS sku_name,
               b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code, l.storage_area, l.storage_position,
               w.name AS warehouse_name,
               u.display_name AS operator_name,
               wo.name AS owner_user_name,
               wad.name AS alloc_dept_name,
               wud.name AS use_dept_name
        FROM stock_log sl
        JOIN sku s ON s.id = sl.sku_id
        JOIN batch b ON b.id = sl.batch_id
        JOIN location l ON l.id = sl.location_id
        LEFT JOIN warehouse w ON w.id = l.warehouse_id
        LEFT JOIN user u ON u.id = sl.operator_id
        LEFT JOIN wh_owner wo ON wo.id = l.resp_owner_id
        LEFT JOIN wh_alloc_dept wad ON wad.id = l.alloc_dept_id
        LEFT JOIN wh_use_dept wud ON wud.id = l.use_dept_id
        WHERE 1=1
    """
    where_clause = (" AND " + " AND ".join(where)) if where else ""
    sql += where_clause + " ORDER BY sl.id DESC LIMIT ? OFFSET ?"
    count_sql = ("SELECT COUNT(*) AS c FROM stock_log sl "
                 "JOIN sku s ON s.id = sl.sku_id "
                 "JOIN batch b ON b.id = sl.batch_id "
                 "JOIN location l ON l.id = sl.location_id "
                 "WHERE 1=1") + where_clause

    with database.get_conn() as conn:
        filtered_total = conn.execute(count_sql, params).fetchone()["c"]
        pg = _pagination(filtered_total)
        rows = conn.execute(sql, params + [pg["per_page"], pg["offset"]]).fetchall()
        # tab 计数（独立，不受筛选影响）
        tab_counts = {}
        for r in conn.execute(
            "SELECT event_type, COUNT(*) AS c FROM stock_log GROUP BY event_type"
        ).fetchall():
            tab_counts[r["event_type"]] = r["c"]
        total_count = conn.execute("SELECT COUNT(*) AS c FROM stock_log").fetchone()["c"]
        # 把每条流水的 source_doc 附件抓出来（按 source_doc 前缀判类型）
        attachments_by_doc = {}
        for r in rows:
            sd = r["source_doc"]
            if not sd or sd in attachments_by_doc:
                continue
            # 试入库单 / 出库单 / 报损 / 调拨 / 盘点
            for tbl, col in [("inbound_order", "order_no"), ("outbound_order", "order_no"),
                              ("damage_log", "id"), ("transfer_order", "order_no"),
                              ("stocktake_order", "order_no")]:
                row = conn.execute(
                    f"SELECT attachments FROM {tbl} WHERE {col}=?", (sd,)
                ).fetchone()
                if row and row["attachments"]:
                    attachments_by_doc[sd] = row["attachments"]
                    break

    return render_template(
        "stock_log.html",
        rows=rows, f=f, tab_counts=tab_counts, total_count=total_count,
        attachments_by_doc=attachments_by_doc, pg=pg,
    )


def _build_stock_log_query(date_from, date_to, sku_id, event_type, warehouse_id="", operator_id="", limit=None):
    sql = ("""SELECT sl.*, s.code AS sku_code, s.name AS sku_name,
                     b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code,
                     w.name AS warehouse_name,
                     u.display_name AS operator_name
              FROM stock_log sl
              JOIN sku s ON s.id = sl.sku_id
              JOIN batch b ON b.id = sl.batch_id
              JOIN location l ON l.id = sl.location_id
              LEFT JOIN warehouse w ON w.id = l.warehouse_id
              LEFT JOIN user u ON u.id = sl.operator_id
              WHERE 1=1""")
    params = []
    if date_from:
        sql += " AND date(sl.occurred_at) >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND date(sl.occurred_at) <= ?"
        params.append(date_to)
    if sku_id:
        sql += " AND sl.sku_id = ?"
        params.append(int(sku_id))
    if event_type:
        sql += " AND sl.event_type = ?"
        params.append(event_type)
    if warehouse_id:
        sql += " AND l.warehouse_id = ?"
        params.append(int(warehouse_id))
    if operator_id:
        sql += " AND sl.operator_id = ?"
        params.append(int(operator_id))
    sql += " ORDER BY sl.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return sql, params


# ============ 导出 ============

import exporters
from datetime import datetime as _dt


@app.route("/export/<resource>")
@login_required
def export_data(resource):
    fmt = request.args.get("format", "xlsx")
    picked_cols = request.args.getlist("col")

    if resource == "inventory":
        all_cols = [
            ("SKU编码", "code"),
            ("物品名", "name"),
            ("规格", "spec"),
            ("单位", "unit"),
            ("在仓数", "total_on_hand"),
            ("可用", lambda r: r["total_on_hand"] - r["total_reserved"]),
            ("安全库存", "safety_stock"),
        ]
        with database.get_conn() as conn:
            rows = conn.execute(
                """SELECT s.id AS sku_id, s.code, s.name, s.spec, s.unit, s.safety_stock,
                          COALESCE(SUM(i.on_hand),0) AS total_on_hand,
                          COALESCE(SUM(i.reserved),0) AS total_reserved
                   FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
                   GROUP BY s.id ORDER BY s.code"""
            ).fetchall()

    elif resource == "stock_log":
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        sku_id = request.args.get("sku_id", "").strip()
        event_type = request.args.get("event_type", "").strip()
        all_cols = [
            ("时间", "occurred_at"),
            ("类型", "event_type"),
            ("SKU", "sku_code"),
            ("物品名", "sku_name"),
            ("批次", "batch_no"),
            ("库位", "location_code"),
            ("变动", "delta"),
            ("来源单", "source_doc"),
            ("操作人", "operator_name"),
            ("备注", "note"),
        ]
        sql, params = _build_stock_log_query(date_from, date_to, sku_id, event_type, limit=50000)
        with database.get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()

    elif resource.startswith("stocktake_"):
        order_no = resource[len("stocktake_"):]
        all_cols = [
            ("SKU", "sku_code"),
            ("物品名", "sku_name"),
            ("批次", "batch_no"),
            ("库位", "location_code"),
            ("账面数", "expected_qty"),
            ("盘点数", "actual_qty"),
            ("差异", lambda r: (r["actual_qty"] or 0) - r["expected_qty"]),
            ("已处理", "diff_handled"),
        ]
        with database.get_conn() as conn:
            rows = conn.execute(
                """SELECT si.*, s.code AS sku_code, s.name AS sku_name,
                          b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS location_code
                   FROM stocktake_item si
                   JOIN sku s ON s.id = si.sku_id
                   JOIN batch b ON b.id = si.batch_id
                   JOIN location l ON l.id = si.location_id
                   WHERE si.order_no = ?
                   ORDER BY s.code""",
                (order_no,),
            ).fetchall()

    elif resource == "forecast":
        import forecasting
        rows = list(forecasting.list_low_stock())
        all_cols = [
            ("物品名", "name"),
            ("规格", "spec"),
            ("当前在仓", "current"),
            ("安全库存", "safety_stock"),
            ("缺口", "gap"),
            ("建议补货", "suggested"),
            ("操作建议", "action"),
        ]

    else:
        flash("未知导出资源", "error")
        return redirect(url_for("index"))

    columns = exporters.filter_columns(all_cols, picked_cols)
    filename = f"{resource}_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
    return exporters.export(rows, columns, fmt, filename)


# ============ 通知渠道与预警规则 ============

@app.route("/channel-config", methods=["GET", "POST"], endpoint="channel_config")
@role_required("admin", "manager")
def channel_config():
    import json as _json
    import notifications

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_channel":
            code = request.form["code"]
            enabled = 1 if request.form.get("enabled") == "on" else 0
            cfg = {}
            for k, v in request.form.items():
                if k.startswith("cfg_"):
                    cfg[k[4:]] = v.strip()
            with database.get_conn() as conn:
                conn.execute(
                    "UPDATE notification_channel SET enabled=?, config_json=?, updated_at=CURRENT_TIMESTAMP WHERE code=?",
                    (enabled, _json.dumps(cfg, ensure_ascii=False), code),
                )
            flash(f"渠道 {code} 已保存", "success")

        elif action == "test_send":
            code = request.form["code"]
            ok, det = notifications.notify(
                code, request.form.get("test_recipient", ""),
                "code_1 测试通知", "这是一条来自 code_1 仓储系统的测试消息。"
            )
            flash(f"[{code}] {'测试发送成功' if ok else '测试发送失败'}：{det}", "success" if ok else "error")

        elif action == "save_rule":
            rule_id = int(request.form["rule_id"])
            enabled = 1 if request.form.get("enabled") == "on" else 0
            channels = ",".join(request.form.getlist("channel_codes"))
            recipients = ",".join(request.form.getlist("recipient_user_ids"))
            with database.get_conn() as conn:
                conn.execute(
                    "UPDATE alert_rule SET enabled=?, channel_codes=?, recipient_user_ids=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (enabled, channels, recipients, rule_id),
                )
            flash("预警规则已保存", "success")

        elif action == "run_scan":
            results = notifications.run_all_scans()
            triggered = sum(1 for _ in results)
            total = sum(n for _, n, *_ in results)
            flash(f"扫描完成：触发 {triggered} 条规则，命中 {total} 个事件", "success")

        return redirect(url_for("channel_config"))

    with database.get_conn() as conn:
        chs = [dict(r) for r in conn.execute("SELECT * FROM notification_channel ORDER BY code").fetchall()]
        rules = conn.execute("SELECT * FROM alert_rule ORDER BY id").fetchall()
        events = conn.execute("SELECT * FROM alert_event ORDER BY id DESC LIMIT 30").fetchall()
        # v13: 过滤掉测试用户（position 空 / NULL），同时返回 position 给前端做"按岗位"快捷选择
        all_users = conn.execute(
            "SELECT id, username, display_name, role, position FROM user "
            "WHERE position IS NOT NULL AND TRIM(position) != '' "
            "ORDER BY id"
        ).fetchall()
    for c in chs:
        try:
            c["config"] = _json.loads(c["config_json"] or "{}")
        except Exception:
            c["config"] = {}
    return render_template("channel_config.html",
                           channels=chs, rules=rules, events=events,
                           all_channel_codes=[c["code"] for c in chs],
                           all_users=all_users)


# ============ 仓库管理 ============

@app.route("/warehouse")
@login_required
def warehouse_list():
    """v16: 简化为只展示名称 + 地址 + 库位数"""
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT w.id, w.name, w.address, w.created_at,
                      (SELECT COUNT(*) FROM location WHERE warehouse_id=w.id) AS loc_count
               FROM warehouse w ORDER BY w.id"""
        ).fetchall()
    pg = _pagination(len(rows))
    rows = rows[pg["offset"]:pg["offset"] + pg["per_page"]]
    return render_template("warehouse_list.html", rows=rows, pg=pg)


def _next_warehouse_code(conn):
    """v15: 仓库编号自动生成 WH000001 / WH000002 ..."""
    row = conn.execute(
        "SELECT code FROM warehouse WHERE code LIKE 'WH%' ORDER BY code DESC LIMIT 1"
    ).fetchone()
    if row and row["code"]:
        try:
            return f"WH{int(row['code'][2:]) + 1:06d}"
        except (ValueError, TypeError):
            pass
    return "WH000001"


def _save_warehouse_image(conn, files, wh_id):
    """v15: 仓库单图上传 — 复用 attachments.save_files 取首个文件存到 image_path。"""
    import attachments as _att
    if not files:
        return
    saved = _att.save_files([f for f in files if f and f.filename], "warehouse", wh_id)
    if saved:
        # 删旧图（如果有）
        old = conn.execute("SELECT image_path FROM warehouse WHERE id=?", (wh_id,)).fetchone()
        if old and old["image_path"] and old["image_path"] != saved[0]:
            old_file = _att.UPLOAD_ROOT / old["image_path"]
            if old_file.exists():
                try:
                    old_file.unlink()
                except OSError:
                    pass
        conn.execute("UPDATE warehouse SET image_path=? WHERE id=?", (saved[0], wh_id))


@app.route("/warehouse/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def warehouse_new():
    """v16: 仓库只有 name + address；分配部门/使用部门/类型/责任人 全部下放到库位"""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip()
        if not name:
            flash("仓库名称必填", "error")
            return render_template("warehouse_form.html", warehouse=None)
        try:
            with database.get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO warehouse (name, address) VALUES (?, ?)",
                    (name, address),
                )
                new_id = cur.lastrowid
            if request.form.get("inline") == "1":
                from flask import jsonify
                return jsonify({"id": new_id, "label": name})
            flash(f"仓库「{name}」已创建", "success")
            return redirect(url_for("warehouse_list"))
        except sqlite3.IntegrityError:
            if request.form.get("inline") == "1":
                from flask import jsonify
                return jsonify({"error": "仓库名已存在"}), 400
            flash("仓库名已存在", "error")
    return render_template("warehouse_form.html", warehouse=None)


@app.route("/warehouse/<int:wh_id>/edit", methods=["GET", "POST"])
@role_required("admin", "manager")
def warehouse_edit(wh_id):
    """v16: 简化为只更新 name + address"""
    with database.get_conn() as conn:
        wh = conn.execute("SELECT * FROM warehouse WHERE id=?", (wh_id,)).fetchone()
        if not wh:
            flash("仓库不存在", "error")
            return redirect(url_for("warehouse_list"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            address = request.form.get("address", "").strip()
            if not name:
                flash("仓库名称必填", "error")
            else:
                try:
                    conn.execute("UPDATE warehouse SET name=?, address=? WHERE id=?",
                                 (name, address, wh_id))
                    flash("仓库已更新", "success")
                    return redirect(url_for("warehouse_list"))
                except sqlite3.IntegrityError:
                    flash("仓库名已存在", "error")
    return render_template("warehouse_form.html", warehouse=wh)


@app.route("/warehouse/<int:wh_id>/delete", methods=["POST"])
@role_required("admin", "manager")
def warehouse_delete(wh_id):
    with database.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM location WHERE warehouse_id=?", (wh_id,)).fetchone()["c"]
        if n > 0:
            flash(f"该仓库仍含 {n} 个库位，请先删除或迁移库位再删仓库", "error")
        else:
            conn.execute("DELETE FROM warehouse WHERE id=?", (wh_id,))
            flash("仓库已删除", "success")
    return redirect(url_for("warehouse_list"))


# ============ 库位管理 ============

# v20: ZONE_TYPE_LABEL 已砸（location.zone_type 字段从表中删除，前端不再显示）
ZONE_TYPE_LABEL = {}  # 留空字典向后兼容（部分查询仍含 l.zone_type，会 SQL 报错时需处理）


@app.route("/location")
@login_required
def location_list():
    f = {
        "q":            request.args.get("q", "").strip(),
        "warehouse_id": request.args.get("warehouse_id", "").strip(),
        "wh_type_id":   request.args.get("wh_type_id", "").strip(),
        "use_dept_id":  request.args.get("use_dept_id", "").strip(),
    }
    where, params = [], []
    if f["q"]:
        kw = f"%{f['q']}%"
        where.append("(IFNULL(l.wh_code,'') LIKE ? OR IFNULL(l.storage_position,'') LIKE ? "
                     "OR IFNULL(l.storage_area,'') LIKE ? OR IFNULL(l.room_name,'') LIKE ? "
                     "OR IFNULL(wo.name,'') LIKE ?)")
        params += [kw, kw, kw, kw, kw]
    if f["warehouse_id"]:
        where.append("l.warehouse_id=?"); params.append(f["warehouse_id"])
    if f["wh_type_id"]:
        where.append("l.wh_type_id=?"); params.append(f["wh_type_id"])
    if f["use_dept_id"]:
        where.append("l.use_dept_id=?"); params.append(f["use_dept_id"])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    base = (f"FROM location l JOIN warehouse w ON w.id=l.warehouse_id "
            "LEFT JOIN wh_alloc_dept wad ON wad.id=l.alloc_dept_id "
            "LEFT JOIN wh_use_dept   wud ON wud.id=l.use_dept_id "
            "LEFT JOIN wh_type       wt  ON wt.id=l.wh_type_id "
            "LEFT JOIN wh_owner      wo  ON wo.id=l.resp_owner_id "
            f"{where_sql}")
    with database.get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c {base}", params).fetchone()["c"]
        pg = _pagination(total)
        rows = conn.execute(
            f"""SELECT l.*, w.name AS warehouse_name, wad.name AS alloc_dept_name,
                       wud.name AS use_dept_name, wt.name AS wh_type_name, wo.name AS owner_name,
                       (SELECT COUNT(*) FROM inventory WHERE location_id=l.id AND on_hand>0) AS active_skus
                {base}
                ORDER BY w.id, l.wh_code, l.storage_area, l.storage_position
                LIMIT ? OFFSET ?""",
            params + [pg["per_page"], pg["offset"]],
        ).fetchall()
        warehouses = conn.execute("SELECT id, name FROM warehouse ORDER BY id").fetchall()
        wh_types = conn.execute("SELECT id, name FROM wh_type ORDER BY name").fetchall()
        use_depts = conn.execute("SELECT id, name FROM wh_use_dept ORDER BY name").fetchall()
    return render_template("location_list.html", rows=rows, pg=pg, f=f,
                           warehouses=warehouses, wh_types=wh_types, use_depts=use_depts,
                           zone_label=ZONE_TYPE_LABEL)


@app.route("/location/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def location_new():
    with database.get_conn() as conn:
        if request.method == "POST":
            warehouse_id = int(request.form["warehouse_id"])
            storage_area = request.form.get("storage_area", "").strip()
            storage_position = request.form.get("storage_position", "").strip()
            note = request.form.get("note", "").strip()  # v19
            # v16: 4 个 picker FK
            alloc_dept_id = request.form.get("alloc_dept_id") or None
            use_dept_id = request.form.get("use_dept_id") or None
            wh_type_id = request.form.get("wh_type_id") or None
            resp_owner_id = request.form.get("resp_owner_id") or None
            if not storage_area or not storage_position:
                flash("存放区域、存放位置都必填", "error")
            else:
                try:
                    cur = conn.execute(
                        "INSERT INTO location (warehouse_id, storage_area, storage_position, "
                        "alloc_dept_id, use_dept_id, wh_type_id, resp_owner_id, note) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (warehouse_id, storage_area, storage_position,
                         int(alloc_dept_id) if alloc_dept_id else None,
                         int(use_dept_id) if use_dept_id else None,
                         int(wh_type_id) if wh_type_id else None,
                         int(resp_owner_id) if resp_owner_id else None,
                         note),
                    )
                    new_id = cur.lastrowid
                    # v19: 附件上传（复用 attachments.save_files）
                    import attachments as _att
                    import json as _json
                    try:
                        saved = _att.save_files(request.files.getlist("attachments[]"), "location", new_id)
                        if saved:
                            conn.execute("UPDATE location SET attachments=? WHERE id=?",
                                         (_json.dumps(saved, ensure_ascii=False), new_id))
                    except ValueError as e:
                        flash(str(e), "warn")
                    if request.form.get("inline") == "1":
                        from flask import jsonify
                        wh_name = conn.execute("SELECT name FROM warehouse WHERE id=?", (warehouse_id,)).fetchone()["name"]
                        return jsonify({"id": new_id, "label": f"{storage_area} / {storage_position} ({wh_name})"})
                    flash(f"库位 {storage_area} / {storage_position} 已创建", "success")
                    return redirect(url_for("location_list"))
                except sqlite3.IntegrityError:
                    if request.form.get("inline") == "1":
                        from flask import jsonify
                        return jsonify({"error": "该仓库下已有相同 存放区域+存放位置 的库位"}), 400
                    flash("该仓库下已有相同 存放区域+存放位置 的库位", "error")
        areas, positions = _storage_options(conn)
        return render_template("location_form.html", location=None, zone_label=ZONE_TYPE_LABEL,
                               area_options=areas, position_options=positions)
    return render_template("location_form.html", location=None, zone_label=ZONE_TYPE_LABEL)


@app.route("/location/<int:loc_id>/edit", methods=["GET", "POST"])
@role_required("admin", "manager")
def location_edit(loc_id):
    with database.get_conn() as conn:
        loc = conn.execute("SELECT * FROM location WHERE id=?", (loc_id,)).fetchone()
        if not loc:
            flash("库位不存在", "error")
            return redirect(url_for("location_list"))
        if request.method == "POST":
            warehouse_id = int(request.form["warehouse_id"])
            storage_area = request.form.get("storage_area", "").strip()
            storage_position = request.form.get("storage_position", "").strip()
            note = request.form.get("note", "").strip()  # v19
            alloc_dept_id = request.form.get("alloc_dept_id") or None
            use_dept_id = request.form.get("use_dept_id") or None
            wh_type_id = request.form.get("wh_type_id") or None
            resp_owner_id = request.form.get("resp_owner_id") or None
            if not storage_area or not storage_position:
                flash("存放区域、存放位置都必填", "error")
            else:
                try:
                    conn.execute(
                        "UPDATE location SET warehouse_id=?, storage_area=?, storage_position=?, "
                        "alloc_dept_id=?, use_dept_id=?, wh_type_id=?, resp_owner_id=?, note=? WHERE id=?",
                        (warehouse_id, storage_area, storage_position,
                         int(alloc_dept_id) if alloc_dept_id else None,
                         int(use_dept_id) if use_dept_id else None,
                         int(wh_type_id) if wh_type_id else None,
                         int(resp_owner_id) if resp_owner_id else None,
                         note, loc_id),
                    )
                    # v19: 追加附件（不删旧的，保留 + 追加）
                    import attachments as _att
                    import json as _json
                    try:
                        saved = _att.save_files(request.files.getlist("attachments[]"), "location", loc_id)
                        if saved:
                            existing = _att.load_attachments(loc["attachments"])
                            merged = existing + saved
                            conn.execute("UPDATE location SET attachments=? WHERE id=?",
                                         (_json.dumps(merged, ensure_ascii=False), loc_id))
                    except ValueError as e:
                        flash(str(e), "warn")
                    flash("库位已更新", "success")
                    return redirect(url_for("location_list"))
                except sqlite3.IntegrityError:
                    flash("该仓库下已有相同 存放区域+存放位置 的库位", "error")
        # 加载关联名称回填
        def _name_of(tbl, idv):
            if not idv:
                return ""
            r = conn.execute(f"SELECT name FROM {tbl} WHERE id=?", (idv,)).fetchone()
            return r["name"] if r else ""
        wh_name = ""
        if loc["warehouse_id"]:
            r = conn.execute("SELECT name FROM warehouse WHERE id=?", (loc["warehouse_id"],)).fetchone()
            wh_name = r["name"] if r else ""
        alloc_dept_name = _name_of("wh_alloc_dept", loc["alloc_dept_id"])
        use_dept_name = _name_of("wh_use_dept", loc["use_dept_id"])
        wh_type_name = _name_of("wh_type", loc["wh_type_id"])
        owner_name = _name_of("wh_owner", loc["resp_owner_id"])
        areas, positions = _storage_options(conn)
        return render_template("location_form.html", location=loc, zone_label=ZONE_TYPE_LABEL,
                               wh_name=wh_name,
                               alloc_dept_name=alloc_dept_name,
                               use_dept_name=use_dept_name,
                               wh_type_name=wh_type_name,
                               owner_name=owner_name,
                               area_options=areas, position_options=positions)
    return render_template("location_form.html", location=loc, warehouses=warehouses, areas=areas, zone_label=ZONE_TYPE_LABEL)


@app.route("/location/<int:loc_id>/delete", methods=["POST"])
@role_required("admin", "manager")
def location_delete(loc_id):
    with database.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM inventory WHERE location_id=? AND on_hand>0", (loc_id,)).fetchone()["c"]
        if n > 0:
            flash(f"该库位仍有 {n} 行库存（在仓 > 0），请先清空再删", "error")
        else:
            conn.execute("DELETE FROM location WHERE id=?", (loc_id,))
            flash("库位已删除", "success")
    return redirect(url_for("location_list"))


# ============ 存储区管理（v16 砸掉，4 路由全删；location.storage_area_id 字段也已删；表 DROP）============


# ============ 物品调拨 ============

@app.route("/transfer")
@login_required
def transfer_list():
    """v20: 列表加 楼栋/楼层/物品 摘要列。"""
    with database.get_conn() as conn:
        orders = conn.execute(
            """SELECT t.*, fwh.name AS from_wh_name, twh.name AS to_wh_name,
                      (fl.storage_area||' / '||fl.storage_position) AS from_loc_code, (tl.storage_area||' / '||tl.storage_position) AS to_loc_code,
                      uc.display_name AS creator_name,
                      (SELECT COUNT(*) FROM transfer_item WHERE order_no=t.order_no) AS line_count,
                      (SELECT COALESCE(SUM(quantity),0) FROM transfer_item WHERE order_no=t.order_no) AS total_qty
               FROM transfer_order t
               JOIN warehouse fwh ON fwh.id=t.from_warehouse_id
               JOIN warehouse twh ON twh.id=t.to_warehouse_id
               JOIN location fl ON fl.id=t.from_location_id
               JOIN location tl ON tl.id=t.to_location_id
               LEFT JOIN user uc ON uc.id=t.creator_id
               ORDER BY t.id DESC"""
        ).fetchall()
        pg = _pagination(len(orders))
        orders = orders[pg["offset"]:pg["offset"] + pg["per_page"]]
        # 每张调拨单的物品摘要
        order_summary = {}
        for o in orders:
            row = conn.execute(
                """SELECT GROUP_CONCAT(DISTINCT s.name) AS sku_names
                   FROM transfer_item ti
                   JOIN sku s ON s.id = ti.sku_id
                   WHERE ti.order_no = ?""",
                (o["order_no"],),
            ).fetchone()
            order_summary[o["order_no"]] = row
    return render_template("transfer_list.html", orders=orders, order_summary=order_summary, pg=pg)


@app.route("/transfer/new", methods=["GET", "POST"])
@login_required
def transfer_new():
    with database.get_conn() as conn:
        warehouses = conn.execute("SELECT * FROM warehouse ORDER BY id").fetchall()
        locations = conn.execute(
            "SELECT l.id, (l.storage_area || ' / ' || l.storage_position) AS code, l.warehouse_id, w.name AS wh_name FROM location l JOIN warehouse w ON w.id=l.warehouse_id ORDER BY l.storage_area, l.storage_position"
        ).fetchall()
        # 候选：所有有库存的批次
        invs = conn.execute(
            """SELECT inv.id AS inv_id, inv.sku_id, inv.batch_id, inv.location_id,
                      inv.on_hand, inv.reserved,
                      s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec,
                      b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS loc_code
               FROM inventory inv
               JOIN sku s ON s.id=inv.sku_id
               JOIN batch b ON b.id=inv.batch_id
               JOIN location l ON l.id=inv.location_id
               WHERE inv.on_hand - inv.reserved > 0
               ORDER BY s.name, b.batch_no"""
        ).fetchall()

        if request.method == "POST":
            from datetime import datetime as _dt
            transfer_date = request.form.get("transfer_date", "").strip() or _dt.now().date().isoformat()
            # v20: 仓库从库位反查（不再用户手填）
            from_loc = int(request.form["from_location_id"])
            to_loc = int(request.form["to_location_id"])
            from_loc_row = conn.execute("SELECT warehouse_id FROM location WHERE id=?", (from_loc,)).fetchone()
            to_loc_row = conn.execute("SELECT warehouse_id FROM location WHERE id=?", (to_loc,)).fetchone()
            from_wh = from_loc_row["warehouse_id"] if from_loc_row else 0
            to_wh = to_loc_row["warehouse_id"] if to_loc_row else 0
            note = request.form.get("note", "").strip()

            if from_loc == to_loc:
                flash("调出库位和调入库位不能相同", "error")
                return render_template("transfer_form.html", warehouses=warehouses, locations=locations, invs=invs)

            inv_ids = request.form.getlist("inv_id[]")
            qtys = request.form.getlist("quantity[]")
            valid = []
            for i, q in zip(inv_ids, qtys):
                if not i or not q: continue
                try:
                    qty = int(q)
                    if qty <= 0: continue
                except ValueError: continue
                inv = next((x for x in invs if x["inv_id"] == int(i) and x["location_id"] == from_loc), None)
                if not inv:
                    flash(f"明细中选的批次不在调出库位上", "error")
                    return render_template("transfer_form.html", warehouses=warehouses, locations=locations, invs=invs)
                available = inv["on_hand"] - inv["reserved"]
                if qty > available:
                    flash(f"{inv['sku_name']} 批次 {inv['batch_no']} 可调 {available}，超出 {qty}", "error")
                    return render_template("transfer_form.html", warehouses=warehouses, locations=locations, invs=invs)
                valid.append({"sku_id": inv["sku_id"], "batch_id": inv["batch_id"], "quantity": qty})

            if not valid:
                flash("至少要填一行明细", "error")
                return render_template("transfer_form.html", warehouses=warehouses, locations=locations, invs=invs)

            order_no = database.gen_order_no(conn, "DBD", "transfer_order")
            import attachments as _att
            import json as _json
            try:
                paths = _att.save_files(request.files.getlist("attachments[]"), "transfer", order_no)
            except ValueError as e:
                flash(str(e), "error")
                return render_template("transfer_form.html", warehouses=warehouses, locations=locations, invs=invs)
            conn.execute(
                """INSERT INTO transfer_order (order_no, transfer_date, from_warehouse_id, from_location_id,
                                                to_warehouse_id, to_location_id, status, creator_id, note, attachments)
                   VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)""",
                (order_no, transfer_date, from_wh, from_loc, to_wh, to_loc, session["user_id"], note,
                 _json.dumps(paths, ensure_ascii=False) if paths else None),
            )
            for v in valid:
                conn.execute(
                    "INSERT INTO transfer_item (order_no, sku_id, batch_id, quantity) VALUES (?, ?, ?, ?)",
                    (order_no, v["sku_id"], v["batch_id"], v["quantity"]),
                )
            flash(f"调拨单 {order_no} 已创建（草稿）。点详情页『执行调拨』完成实际库存搬移。", "success")
            return redirect(url_for("transfer_detail", order_no=order_no))

    return render_template("transfer_form.html", warehouses=warehouses, locations=locations, invs=invs)


@app.route("/transfer/<order_no>")
@login_required
def transfer_detail(order_no):
    with database.get_conn() as conn:
        order = conn.execute(
            """SELECT t.*, fwh.name AS from_wh_name, twh.name AS to_wh_name,
                      (fl.storage_area||' / '||fl.storage_position) AS from_loc_code, (tl.storage_area||' / '||tl.storage_position) AS to_loc_code,
                      uc.display_name AS creator_name
               FROM transfer_order t
               JOIN warehouse fwh ON fwh.id=t.from_warehouse_id
               JOIN warehouse twh ON twh.id=t.to_warehouse_id
               JOIN location fl ON fl.id=t.from_location_id
               JOIN location tl ON tl.id=t.to_location_id
               LEFT JOIN user uc ON uc.id=t.creator_id
               WHERE t.order_no=?""",
            (order_no,),
        ).fetchone()
        if not order:
            flash("调拨单不存在", "error")
            return redirect(url_for("transfer_list"))
        items = conn.execute(
            """SELECT ti.*, s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, b.batch_no
               FROM transfer_item ti
               JOIN sku s ON s.id=ti.sku_id
               JOIN batch b ON b.id=ti.batch_id
               WHERE ti.order_no=?""",
            (order_no,),
        ).fetchall()
    return render_template("transfer_detail.html", order=order, items=items)


@app.route("/transfer/<order_no>/complete", methods=["POST"])
@login_required
def transfer_complete(order_no):
    """执行调拨：从调出库位扣减 → 写出库流水 → 调入库位增加（同批次）→ 写入库流水。"""
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM transfer_order WHERE order_no=?", (order_no,)).fetchone()
        if not order or order["status"] != "draft":
            flash("只有草稿状态的调拨单可以执行", "error")
            return redirect(url_for("transfer_detail", order_no=order_no))
        items = conn.execute("SELECT * FROM transfer_item WHERE order_no=?", (order_no,)).fetchall()
        for it in items:
            # 扣调出库位
            from_inv = conn.execute(
                "SELECT id, on_hand FROM inventory WHERE sku_id=? AND batch_id=? AND location_id=?",
                (it["sku_id"], it["batch_id"], order["from_location_id"]),
            ).fetchone()
            if not from_inv or from_inv["on_hand"] < it["quantity"]:
                flash(f"调出库位库存不足（已被其他操作改变），调拨中止", "error")
                return redirect(url_for("transfer_detail", order_no=order_no))
            conn.execute(
                """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id, note)
                   VALUES (?, ?, ?, ?, ?, 'transfer', ?, ?)""",
                (it["sku_id"], it["batch_id"], order["from_location_id"], -it["quantity"],
                 order_no, session["user_id"], "调出"),
            )
            conn.execute(
                "UPDATE inventory SET on_hand = on_hand - ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (it["quantity"], from_inv["id"]),
            )
            # 加调入库位（同批次）
            conn.execute(
                """INSERT INTO stock_log (sku_id, batch_id, location_id, delta, source_doc, event_type, operator_id, note)
                   VALUES (?, ?, ?, ?, ?, 'transfer', ?, ?)""",
                (it["sku_id"], it["batch_id"], order["to_location_id"], it["quantity"],
                 order_no, session["user_id"], "调入"),
            )
            to_inv = conn.execute(
                "SELECT id FROM inventory WHERE sku_id=? AND batch_id=? AND location_id=?",
                (it["sku_id"], it["batch_id"], order["to_location_id"]),
            ).fetchone()
            if to_inv:
                conn.execute(
                    "UPDATE inventory SET on_hand = on_hand + ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (it["quantity"], to_inv["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO inventory (sku_id, batch_id, location_id, on_hand) VALUES (?, ?, ?, ?)",
                    (it["sku_id"], it["batch_id"], order["to_location_id"], it["quantity"]),
                )
        conn.execute(
            "UPDATE transfer_order SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (order_no,),
        )
    flash(f"调拨单 {order_no} 已执行：库存已搬移", "success")
    return redirect(url_for("transfer_detail", order_no=order_no))


@app.route("/transfer/<order_no>/cancel", methods=["POST"])
@login_required
def transfer_cancel(order_no):
    with database.get_conn() as conn:
        order = conn.execute("SELECT * FROM transfer_order WHERE order_no=?", (order_no,)).fetchone()
        if not order or order["status"] != "draft":
            flash("只有草稿状态的调拨单可以撤销", "error")
        else:
            conn.execute("UPDATE transfer_order SET status='cancelled' WHERE order_no=?", (order_no,))
            flash(f"调拨单 {order_no} 已撤销", "success")
    return redirect(url_for("transfer_list"))


# ============ 待审批中心 ============

@app.route("/pending-approvals")
@login_required
def pending_approvals_list():
    biz = request.args.get("type", "all")
    items = []
    with database.get_conn() as conn:
        if biz in ("all", "damage"):
            for r in conn.execute(
                """SELECT d.id, s.code AS sku_code, s.name AS sku_name, d.quantity, d.created_at,
                          u.display_name AS by_name
                   FROM damage_log d JOIN sku s ON s.id=d.sku_id
                   LEFT JOIN user u ON u.id=d.applicant_id
                   WHERE d.status='pending' ORDER BY d.id DESC""").fetchall():
                items.append({"biz": "报损", "biz_code": "damage", "id": f"DMG-{r['id']}",
                              "title": f"{r['sku_name']} {r['quantity']} 件",
                              "by": r["by_name"], "at": r["created_at"],
                              "url": url_for("damage_list")})
        if biz in ("all", "inbound"):
            for r in conn.execute(
                """SELECT o.order_no, uo.display_name AS operator_name, o.created_at, u.display_name AS by_name,
                          (SELECT COALESCE(SUM(quantity),0) FROM inbound_item WHERE order_no=o.order_no) AS qty
                   FROM inbound_order o LEFT JOIN user uo ON uo.id=o.operator_id
                   LEFT JOIN user u ON u.id=o.creator_id
                   WHERE o.status='draft' ORDER BY o.id DESC""").fetchall():
                items.append({"biz": "入库", "biz_code": "inbound", "id": r["order_no"],
                              "title": f"经手：{r['operator_name'] or '-'} · 共 {r['qty']} 件",
                              "by": r["by_name"], "at": r["created_at"],
                              "url": url_for("inbound_detail", order_no=r["order_no"])})
        if biz in ("all", "outbound"):
            for r in conn.execute(
                """SELECT o.order_no, o.receiver_desc, o.created_at,
                          (SELECT COALESCE(SUM(quantity),0) FROM outbound_item WHERE order_no=o.order_no) AS qty
                   FROM outbound_order o WHERE o.status='pending' ORDER BY o.id DESC""").fetchall():
                items.append({"biz": "出库", "biz_code": "outbound", "id": r["order_no"],
                              "title": f"{r['receiver_desc'] or '无收件方'} · 共 {r['qty']} 件",
                              "by": "-", "at": r["created_at"],
                              "url": url_for("pick_detail", order_no=r["order_no"])})
        if biz in ("all", "requisition"):
            for r in conn.execute(
                """SELECT ro.order_no, ro.applicant, ro.dept_name, ro.created_at,
                          (SELECT COALESCE(SUM(quantity),0) FROM requisition_item WHERE order_no=ro.order_no) AS qty
                   FROM requisition_order ro WHERE ro.status='submitted' ORDER BY ro.id DESC""").fetchall():
                items.append({"biz": "申领", "biz_code": "requisition", "id": r["order_no"],
                              "title": f"{r['applicant']}{' · ' + r['dept_name'] if r['dept_name'] else ''} · 共 {r['qty']} 件",
                              "by": r["applicant"], "at": r["created_at"],
                              "url": url_for("requisition_list")})
        if biz in ("all", "transfer"):
            for r in conn.execute(
                """SELECT t.order_no, t.transfer_date, t.created_at, u.display_name AS by_name,
                          fw.name AS from_wh, tw.name AS to_wh,
                          (SELECT COALESCE(SUM(quantity),0) FROM transfer_item WHERE order_no=t.order_no) AS qty
                   FROM transfer_order t
                   JOIN warehouse fw ON fw.id=t.from_warehouse_id
                   JOIN warehouse tw ON tw.id=t.to_warehouse_id
                   LEFT JOIN user u ON u.id=t.creator_id
                   WHERE t.status='draft' ORDER BY t.id DESC""").fetchall():
                items.append({"biz": "调拨", "biz_code": "transfer", "id": r["order_no"],
                              "title": f"{r['from_wh']} → {r['to_wh']} · 共 {r['qty']} 件",
                              "by": r["by_name"], "at": r["created_at"],
                              "url": url_for("transfer_detail", order_no=r["order_no"])})
        if biz in ("all", "stocktake"):
            for r in conn.execute(
                """SELECT st.order_no, st.title, st.created_at, u.display_name AS by_name,
                          (SELECT COUNT(*) FROM stocktake_item WHERE order_no=st.order_no) AS lc
                   FROM stocktake_order st LEFT JOIN user u ON u.id=st.creator_id
                   WHERE st.status='open' ORDER BY st.id DESC""").fetchall():
                items.append({"biz": "盘点", "biz_code": "stocktake", "id": r["order_no"],
                              "title": f"{r['title'] or '盘点'} · 含 {r['lc']} 行",
                              "by": r["by_name"], "at": r["created_at"],
                              "url": url_for("stocktake_detail", order_no=r["order_no"])})
        # 各类型计数
        counts = {"all": len(items)}
        for it in items:
            counts[it["biz_code"]] = counts.get(it["biz_code"], 0) + 1
    # 按时间倒序
    items.sort(key=lambda x: x.get("at") or "", reverse=True)
    return render_template("pending_approvals.html", items=items, biz=biz, counts=counts)


# ============ 通用 Picker 路由 ============

def _picker_response(rows, title, ret, target, multi, new_endpoint, new_modal_fields, current_ids=None):
    """渲染通用 picker 页。
    rows: [{"id": ..., "label": ..., "extra": ...}, ...]
    new_modal_fields: [(name, label, type), ...] 用于新建 modal
    """
    return render_template(
        "picker_base.html",
        rows=rows, title=title,
        return_path=ret, target=target, multi=multi,
        new_endpoint=new_endpoint,
        new_fields=new_modal_fields,
        current_ids=set((current_ids or "").split(",")) if current_ids else set(),
    )


@app.route("/picker/sku")
@login_required
def picker_sku():
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    multi = request.args.get("multi", "0") == "1"
    current = request.args.get("current", "")
    with database.get_conn() as conn:
        skus = conn.execute("SELECT id, code, name, spec, unit FROM sku ORDER BY name").fetchall()
    rows = [{"id": s["id"], "label": f"{s['name']}{(' (' + s['spec'] + ')') if s['spec'] else ''}",
             "extra": f"{s['code']} · 单位 {s['unit']}"} for s in skus]
    return _picker_response(rows, "物品", ret, target, multi, "sku_new",
                            [("name", "物品名 *", "text"),
                             ("spec", "规格", "text"),
                             ("unit", "单位", "text"),
                             ("safety_stock", "安全库存", "number")],
                            current)


@app.route("/picker/location")
@login_required
def picker_location():
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    multi = request.args.get("multi", "0") == "1"
    current = request.args.get("current", "")
    warehouse_id = request.args.get("warehouse_id", "").strip()
    with database.get_conn() as conn:
        sql = """SELECT l.id, (l.storage_area || ' / ' || l.storage_position) AS code, w.name AS wh_name
                 FROM location l
                 JOIN warehouse w ON w.id = l.warehouse_id"""
        params = []
        if warehouse_id:
            sql += " WHERE l.warehouse_id = ?"
            params.append(int(warehouse_id))
        sql += " ORDER BY w.id, l.storage_area, l.storage_position"
        locs = conn.execute(sql, params).fetchall()
        wh_opts = [(w["id"], w["name"]) for w in conn.execute("SELECT id, name FROM warehouse ORDER BY id").fetchall()]
    rows = [{"id": l["id"], "label": f"{l['code']} ({l['wh_name']})",
             "extra": "库位"} for l in locs]
    return _picker_response(rows, "库位", ret, target, multi, "location_new",
                            [("warehouse_id", "所属仓库 *", "select", wh_opts),
                             ("code", "库位代码 *（如 A-01-01 / 酒柜-1）", "text")],
                            current)


@app.route("/picker/warehouse")
@login_required
def picker_warehouse():
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    multi = request.args.get("multi", "0") == "1"
    current = request.args.get("current", "")
    with database.get_conn() as conn:
        whs = conn.execute(
            "SELECT id, name, address, (SELECT COUNT(*) FROM location WHERE warehouse_id=warehouse.id) AS lc FROM warehouse ORDER BY id"
        ).fetchall()
    rows = [{"id": w["id"], "label": w["name"], "extra": f"{w['address'] or '无地址'} · 含 {w['lc']} 个库位"} for w in whs]
    return _picker_response(rows, "仓库", ret, target, multi, "warehouse_new",
                            [("name", "仓库名 *", "text"),
                             ("address", "地址", "text")],
                            current)


@app.route("/picker/position")
@login_required
def picker_position():
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    multi = request.args.get("multi", "0") == "1"
    current = request.args.get("current", "")
    with database.get_conn() as conn:
        ps = conn.execute("SELECT code, label FROM position ORDER BY code").fetchall()
    rows = [{"id": p["code"], "label": p["label"], "extra": p["code"]} for p in ps]
    return _picker_response(rows, "岗位", ret, target, multi, "position_new_picker",
                            [("label", "岗位名称 *", "text"),
                             ("code", "岗位代码（留空自动）", "text")],
                            current)


@app.route("/picker/owner-party")
@login_required
def picker_owner_party():
    """v13: 物资所属方 picker（部门 / 项目 / 团队归属）"""
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    multi = request.args.get("multi", "0") == "1"
    current = request.args.get("current", "")
    with database.get_conn() as conn:
        rows_db = conn.execute("SELECT id, name, note FROM owner_party ORDER BY id").fetchall()
    rows = [{"id": r["id"], "label": r["name"], "extra": r["note"] or "-"} for r in rows_db]
    return _picker_response(rows, "物资所属方", ret, target, multi, "owner_party_new",
                            [("name", "所属方名称 *", "text"),
                             ("note", "备注", "text")],
                            current)


@app.route("/picker/owner-unit")
@login_required
def picker_owner_unit():
    """v13: 物资所属单位 picker（公司 / 法人单位归属）"""
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    multi = request.args.get("multi", "0") == "1"
    current = request.args.get("current", "")
    with database.get_conn() as conn:
        rows_db = conn.execute("SELECT id, name, note FROM owner_unit ORDER BY id").fetchall()
    rows = [{"id": r["id"], "label": r["name"], "extra": r["note"] or "-"} for r in rows_db]
    return _picker_response(rows, "物资所属单位", ret, target, multi, "owner_unit_new",
                            [("name", "单位名称 *", "text"),
                             ("note", "备注", "text")],
                            current)


@app.route("/owner-party/new", methods=["POST"], endpoint="owner_party_new")
@role_required("admin", "manager")
def owner_party_new():
    """picker 内嵌新建物资所属方（返回 JSON）。"""
    from flask import jsonify
    name = request.form.get("name", "").strip()
    note = request.form.get("note", "").strip()
    if not name:
        return jsonify({"error": "所属方名称必填"}), 400
    try:
        with database.get_conn() as conn:
            cur = conn.execute("INSERT INTO owner_party (name, note) VALUES (?, ?)", (name, note))
            return jsonify({"id": cur.lastrowid, "label": name})
    except sqlite3.IntegrityError:
        return jsonify({"error": "名称已存在"}), 400


@app.route("/owner-unit/new", methods=["POST"], endpoint="owner_unit_new")
@role_required("admin", "manager")
def owner_unit_new():
    """picker 内嵌新建物资所属单位（返回 JSON）。"""
    from flask import jsonify
    name = request.form.get("name", "").strip()
    note = request.form.get("note", "").strip()
    if not name:
        return jsonify({"error": "单位名称必填"}), 400
    try:
        with database.get_conn() as conn:
            cur = conn.execute("INSERT INTO owner_unit (name, note) VALUES (?, ?)", (name, note))
            return jsonify({"id": cur.lastrowid, "label": name})
    except sqlite3.IntegrityError:
        return jsonify({"error": "名称已存在"}), 400


# ============ v14: Generic Master Data Admin + Picker（6 张同构主数据表批量注册） ============

# (table_name, label, fk_check_pairs)
# fk_check_pairs: 删除前要检查的外键引用 — 列表 [(referencing_table, referencing_column), ...]
V14_MASTERS = [
    ("owner_admin",         "物品管理方",   [("sku", "owner_admin_id")]),
    ("item_category",       "物品类别",     [("sku", "category_id")]),
    ("item_category_major", "物品大类",     [("sku", "category_major_id")]),
    # v16: 4 字段从 warehouse 搬到 location 后，FK 检查也改 location 表
    ("wh_alloc_dept",       "分配部门",     [("location", "alloc_dept_id")]),
    ("wh_use_dept",         "使用部门",     [("location", "use_dept_id")]),
    ("wh_type",             "类型",         [("location", "wh_type_id")]),
    # v23: 责任人改为独立主数据表（不再复用 user），接入通用工厂 → /wh-owner/admin + /picker/wh-owner
    ("wh_owner",            "责任人",       [("location", "resp_owner_id")]),
]
# owner_party 已经有独立 picker，但需要补 admin 入口（列表 + 删除）
V14_EXISTING_TABLES = [
    ("owner_party", "物品所属方", [("sku", "owner_party_id")]),
]


def _register_master_admin(table, label, fk_checks):
    """工厂：给指定主数据表注册一个 admin 路由（列表 + 新建 + 删除）"""
    endpoint_admin = f"{table}_admin"
    endpoint_picker = f"picker_{table}"
    endpoint_new = f"{table}_new_inline"

    @app.route(f"/{table.replace('_', '-')}/admin", methods=["GET", "POST"], endpoint=endpoint_admin)
    @role_required("admin", "manager")
    def admin_view():
        with database.get_conn() as conn:
            if request.method == "POST":
                action = request.form.get("action")
                if action == "create":
                    name = request.form.get("name", "").strip()
                    note = request.form.get("note", "").strip()
                    if name:
                        try:
                            conn.execute(f"INSERT INTO {table} (name, note) VALUES (?, ?)", (name, note))
                            flash(f"{label}「{name}」已新增", "success")
                        except sqlite3.IntegrityError:
                            flash(f"{label}「{name}」已存在", "error")
                    else:
                        flash("名称必填", "error")
                elif action == "delete":
                    item_id = int(request.form["id"])
                    # 检查 FK 引用
                    blocked_by = []
                    for ref_table, ref_col in fk_checks:
                        n = conn.execute(
                            f"SELECT COUNT(*) AS c FROM {ref_table} WHERE {ref_col}=?", (item_id,)
                        ).fetchone()["c"]
                        if n > 0:
                            blocked_by.append(f"{ref_table} {n} 处")
                    if blocked_by:
                        flash(f"无法删除：被 {' / '.join(blocked_by)} 引用", "error")
                    else:
                        conn.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
                        flash(f"{label}已删除", "success")
                return redirect(url_for(endpoint_admin))
            rows = conn.execute(f"SELECT id, name, note, created_at FROM {table} ORDER BY id").fetchall()
        return render_template("_generic_admin.html", title=label, rows=rows)
    admin_view.__name__ = endpoint_admin

    @app.route(f"/picker/{table.replace('_', '-')}", endpoint=endpoint_picker)
    @login_required
    def picker_view():
        ret = request.args.get("return", "/")
        target = request.args.get("target", "")
        multi = request.args.get("multi", "0") == "1"
        current = request.args.get("current", "")
        with database.get_conn() as conn:
            rs = conn.execute(f"SELECT id, name, note FROM {table} ORDER BY id").fetchall()
        rows = [{"id": r["id"], "label": r["name"], "extra": r["note"] or "-"} for r in rs]
        return _picker_response(rows, label, ret, target, multi, endpoint_new,
                                [("name", "名称 *", "text"), ("note", "备注", "text")],
                                current)
    picker_view.__name__ = endpoint_picker

    @app.route(f"/{table.replace('_', '-')}/new", methods=["POST"], endpoint=endpoint_new)
    @role_required("admin", "manager")
    def new_inline():
        from flask import jsonify
        name = request.form.get("name", "").strip()
        note = request.form.get("note", "").strip()
        if not name:
            return jsonify({"error": "名称必填"}), 400
        try:
            with database.get_conn() as conn:
                cur = conn.execute(f"INSERT INTO {table} (name, note) VALUES (?, ?)", (name, note))
                return jsonify({"id": cur.lastrowid, "label": name})
        except sqlite3.IntegrityError:
            return jsonify({"error": "名称已存在"}), 400
    new_inline.__name__ = endpoint_new


# 批量注册 6 张新主数据表
for _t, _l, _fks in V14_MASTERS:
    _register_master_admin(_t, _l, _fks)

# 给已有的 owner_party 补 admin + delete 入口（picker 路由已存在不动）
def _register_owner_party_admin():
    @app.route("/owner-party/admin", methods=["GET", "POST"], endpoint="owner_party_admin")
    @role_required("admin", "manager")
    def owner_party_admin():
        with database.get_conn() as conn:
            if request.method == "POST":
                action = request.form.get("action")
                if action == "create":
                    name = request.form.get("name", "").strip()
                    note = request.form.get("note", "").strip()
                    if name:
                        try:
                            conn.execute("INSERT INTO owner_party (name, note) VALUES (?, ?)", (name, note))
                            flash(f"物品所属方「{name}」已新增", "success")
                        except sqlite3.IntegrityError:
                            flash(f"「{name}」已存在", "error")
                elif action == "delete":
                    item_id = int(request.form["id"])
                    n = conn.execute("SELECT COUNT(*) AS c FROM sku WHERE owner_party_id=?", (item_id,)).fetchone()["c"]
                    if n > 0:
                        flash(f"无法删除：被 sku {n} 处引用", "error")
                    else:
                        conn.execute("DELETE FROM owner_party WHERE id=?", (item_id,))
                        flash("已删除", "success")
                return redirect(url_for("owner_party_admin"))
            rows = conn.execute("SELECT id, name, note, created_at FROM owner_party ORDER BY id").fetchall()
        return render_template("_generic_admin.html", title="物品所属方", rows=rows)
_register_owner_party_admin()


# v20: /picker/zone-type 路由已砸（存储类型字段从 location 表删除）


@app.route("/picker/user", endpoint="picker_user")
@login_required
def picker_user():
    """v22: 通用员工 picker（入库经手 / 出库经手 等场景），不限角色 + 不限岗位。

    返回所有用户（display_name / username + position 标签），按 id 排序。
    """
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    multi = request.args.get("multi", "0") == "1"
    current = request.args.get("current", "")
    positions = _load_positions_dict()
    with database.get_conn() as conn:
        users = conn.execute(
            "SELECT id, display_name, username, position, role FROM user ORDER BY id"
        ).fetchall()
    rows = []
    for u in users:
        pos_codes = [c for c in (u["position"] or "").split(",") if c]
        pos_label = "、".join(positions.get(c, c) for c in pos_codes) or "-"
        rows.append({
            "id": u["id"],
            "label": u["display_name"] or u["username"],
            "extra": f"{pos_label}",
        })
    return _picker_response(rows, "员工", ret, target, multi, None, [], current)


# v23: 旧的 picker_wh_owner（复用 user 表）+ wh_owner_admin（跳转 user_admin）已删除。
#      责任人现为独立主数据表 wh_owner，由通用工厂 _register_master_admin 生成
#      /picker/wh-owner（picker_wh_owner）与 /wh-owner/admin（wh_owner_admin）。


@app.route("/picker/loc-filtered", endpoint="picker_loc_filtered")
@login_required
def picker_loc_filtered():
    """v20: 调拨用 - 5 重筛选选库位 picker（仓库/楼栋/楼层/库位代码/责任人）。

    require_stock=1 → 仅显示有库存的库位（用于调出）
    require_stock 缺省 → 显示所有库位（用于调入）
    """
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    require_stock = request.args.get("require_stock", "").strip() == "1"
    f = {
        "warehouse_id":     request.args.get("warehouse_id", "").strip(),
        "storage_area":     request.args.get("storage_area", "").strip(),
        "storage_position": request.args.get("storage_position", "").strip(),
        "owner_user_id":    request.args.get("owner_user_id", "").strip(),
    }
    where = []
    params = []
    if f["warehouse_id"]:
        where.append("l.warehouse_id = ?"); params.append(int(f["warehouse_id"]))
    if f["storage_area"]:
        where.append("l.storage_area LIKE ?"); params.append(f"%{f['storage_area']}%")
    if f["storage_position"]:
        where.append("l.storage_position LIKE ?"); params.append(f"%{f['storage_position']}%")
    if f["owner_user_id"]:
        where.append("l.resp_owner_id = ?"); params.append(int(f["owner_user_id"]))

    sql = """
        SELECT l.id, (l.storage_area || ' / ' || l.storage_position) AS code, l.storage_area, l.storage_position,
               w.name AS wh_name,
               wo.name AS owner_user_name,
               wt.name AS wh_type_name,
               wud.name AS use_dept_name,
               (SELECT COALESCE(SUM(on_hand),0) FROM inventory WHERE location_id = l.id) AS total_on_hand
        FROM location l
        LEFT JOIN warehouse w ON w.id = l.warehouse_id
        LEFT JOIN wh_owner wo ON wo.id = l.resp_owner_id
        LEFT JOIN wh_type wt ON wt.id = l.wh_type_id
        LEFT JOIN wh_use_dept wud ON wud.id = l.use_dept_id
        WHERE 1=1
    """
    if where:
        sql += " AND " + " AND ".join(where)
    if require_stock:
        sql += " AND (SELECT COALESCE(SUM(on_hand),0) FROM inventory WHERE location_id = l.id) > 0"
    sql += " ORDER BY w.id, l.storage_area, l.storage_position"

    with database.get_conn() as conn:
        locs = conn.execute(sql, params).fetchall()

    title = "调出库位（仅有库存）" if require_stock else "库位"
    subtitle = "按 仓库/区域/位置/责任人 筛选 · 仅显示当前有库存的库位 · 点行尾「选定」回填" if require_stock \
               else "按 仓库/区域/位置/责任人 筛选 · 点行尾「选定」回填到上一页"
    return render_template("picker_loc_filtered.html",
                           locs=locs, f=f, return_path=ret, target=target,
                           title=title, subtitle=subtitle, require_stock=require_stock)


@app.route("/picker/damage-inv", endpoint="picker_damage_inv")
@login_required
def picker_damage_inv():
    """v20: 报损选批次 picker - 可按物品名/批次号/仓库/库位筛选有库存的 inventory 行。"""
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    sku_id = request.args.get("sku_id", "").strip()
    warehouse_id = request.args.get("warehouse_id", "").strip()
    location_id = request.args.get("location_id", "").strip()
    batch_no = request.args.get("batch_no", "").strip()
    sql = """SELECT inv.id, s.name AS sku_name, s.spec AS sku_spec, s.code AS sku_code,
                    b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS loc_code, w.name AS wh_name,
                    inv.on_hand
             FROM inventory inv
             JOIN sku s ON s.id = inv.sku_id
             JOIN batch b ON b.id = inv.batch_id
             JOIN location l ON l.id = inv.location_id
             LEFT JOIN warehouse w ON w.id = l.warehouse_id
             WHERE inv.on_hand > 0"""
    params = []
    if sku_id:
        sql += " AND inv.sku_id = ?"; params.append(int(sku_id))
    if warehouse_id:
        sql += " AND l.warehouse_id = ?"; params.append(int(warehouse_id))
    if location_id:
        sql += " AND inv.location_id = ?"; params.append(int(location_id))
    if batch_no:
        sql += " AND b.batch_no LIKE ?"; params.append(f"%{batch_no}%")
    sql += " ORDER BY s.name, b.batch_no"
    with database.get_conn() as conn:
        invs = conn.execute(sql, params).fetchall()
    rows = [{
        "id": i["id"],
        "label": f"{i['sku_name']}{(' (' + i['sku_spec'] + ')') if i['sku_spec'] else ''} · 批次 {i['batch_no']}",
        "extra": f"{i['wh_name'] or '-'} · {i['loc_code']} · 在仓 {i['on_hand']}",
    } for i in invs]
    return _picker_response(rows, "可报损批次", ret, target, False, None, [], "")


@app.route("/picker/inventory")
@login_required
def picker_inventory():
    """v13: 选物品+批次 picker，按 location_id 过滤可调存货。

    用于调拨明细行：选完调出库位后，点物品选择按钮跳来这里，
    只显示该库位上 on_hand - reserved > 0 的 inventory 行。
    """
    ret = request.args.get("return", "/")
    target = request.args.get("target", "")
    location_id = request.args.get("location_id", "").strip()
    with database.get_conn() as conn:
        if not location_id:
            invs = []
        else:
            invs = conn.execute(
                """SELECT inv.id, inv.sku_id, inv.batch_id, inv.location_id,
                          inv.on_hand, inv.reserved,
                          s.code AS sku_code, s.name AS sku_name, s.spec AS sku_spec, s.unit,
                          b.batch_no, (l.storage_area || ' / ' || l.storage_position) AS loc_code
                   FROM inventory inv
                   JOIN sku s ON s.id = inv.sku_id
                   JOIN batch b ON b.id = inv.batch_id
                   JOIN location l ON l.id = inv.location_id
                   WHERE inv.location_id = ? AND (inv.on_hand - inv.reserved) > 0
                   ORDER BY s.name, b.batch_no""",
                (int(location_id),),
            ).fetchall()
    rows = [{
        "id": i["id"],
        # label 里塞"可调 N"，回填到主表单后 JS 用正则提取数量显示
        "label": f"{i['sku_name']}{(' (' + i['sku_spec'] + ')') if i['sku_spec'] else ''} · 批次 {i['batch_no']} · 可调 {i['on_hand'] - i['reserved']}",
        "extra": f"{i['unit']} · 库位 {i['loc_code']}",
    } for i in invs]
    # 不允许内嵌新建（inventory 由入库流程生成）
    return _picker_response(rows, "可调存货", ret, target, False, None, [])


@app.route("/picker/position/new-inline", methods=["POST"], endpoint="position_new_picker")
@role_required("admin", "manager")
def position_new_picker_endpoint():
    """picker 内嵌新增岗位的入口（区别于 user_admin 里的 action='create_position'）。"""
    from flask import jsonify
    label = request.form.get("label", "").strip()
    code = request.form.get("code", "").strip() or f"pos_{int(__import__('time').time() * 1000)}"
    if not label:
        return jsonify({"error": "岗位名称不能空"}), 400
    try:
        with database.get_conn() as conn:
            conn.execute("INSERT INTO position (code, label) VALUES (?, ?)", (code, label))
        return jsonify({"id": code, "label": label})
    except sqlite3.IntegrityError:
        return jsonify({"error": "岗位代码冲突"}), 400


# ============ APScheduler 启动 ============

def _start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    import notifications
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(notifications.run_all_scans, "cron", hour=9, minute=0, id="daily_alerts")
    # 每日 2 点备份 + 每月 1 日凌晨快照（批 7 提供函数）
    try:
        import backup, snapshots
        sched.add_job(backup.backup_db, "cron", hour=2, minute=0, id="daily_backup")
        sched.add_job(snapshots.snapshot_inventory, "cron", day=1, hour=1, minute=0, id="monthly_snapshot")
    except ImportError:
        pass
    sched.start()
    print("[APScheduler] 已启动：每日 09:00 预警扫描；02:00 备份；月初 01:00 库存快照")


if __name__ == "__main__":
    import os
    database.init_db()
    _start_scheduler()
    # v12: 默认用 waitress（生产级 WSGI server，多线程，Windows 兼容）
    # 想用 Flask dev server 调试，设环境变量 FLASK_DEV=1
    if os.environ.get("FLASK_DEV") == "1":
        print("[server] Flask dev server on 0.0.0.0:5000")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    else:
        from waitress import serve
        print("[server] waitress on 0.0.0.0:5000 (threads=8)")
        print("[server] LAN access: http://<your-ip>:5000/")
        serve(app, host="0.0.0.0", port=5000, threads=8)
