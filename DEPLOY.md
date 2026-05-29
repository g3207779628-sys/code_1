# 部署说明（给运维 / IT）

> 一句话：这是一套 **Flask + SQLite** 仓库管理系统，**两个独立进程**，纯 Python，无外部数据库依赖。
> 把代码放到服务器、装好依赖、补上数据文件、用进程守护把两个进程拉起来即可。约 20 人内网使用。

---

## 一、系统由两个进程组成

| 进程 | 文件 | 端口 | 用途 | 是否要登录 | 暴露范围 |
|---|---|---|---|---|---|
| 后台 | `app.py` | **5000** | 仓管全功能后台（出入库、库存、库位、报表） | 要登录 | **建议只在内网** |
| 领用门户 | `apply_app.py` | **5001** | 免登录公开页，给员工手机扫码提领用申请 | 不登录 | 可暴露到员工能访问的网段/公网 |

两个进程 **共用同一个 `warehouse.db`** 通信（门户只往申请表写一条记录，读不到库存、改不了数据，因此即便公开也安全）。

二者都已默认使用 **waitress**（生产级 WSGI 服务器，多线程，跨 Windows/Linux），**不是** Flask 开发服务器。

---

## 二、你会收到两样东西

1. **代码仓库**（git）—— `app.py` / `apply_app.py` / `templates/` / `static/` / `requirements.txt` 等，本文件也在其中。
2. **数据包**（单独传，不在 git 里）—— 两项，必须放回项目根目录：
   - `warehouse.db` （约 0.6 MB，全部业务数据：物品/库位/出入库等）
   - `uploads/` 文件夹 （约 **1.1 GB / 629 个文件**，物品图与库位图）

> ⚠️ 关键：`warehouse.db` 和 `uploads/` 被 `.gitignore` 排除，**git 里没有**。只部署 git 仓库的话，系统会是空的、图片全裂。务必把数据包解压到项目根目录。

---

## 三、环境要求

- **Python 3.11+**（开发与测试在 3.14；3.10 以下的旧语法可能不兼容）
- 无需 MySQL/PostgreSQL/Redis —— 数据库就是项目目录里的 `warehouse.db`（SQLite 单文件）
- 依赖见 `requirements.txt`：Flask / Werkzeug / APScheduler / openpyxl / requests / waitress

```bash
# 建议用虚拟环境
python -m venv .venv
# Linux:   source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 四、启动（手动验证用）

在项目根目录分别启动两个进程：

```bash
python app.py          # 后台   → 监听 0.0.0.0:5000
python apply_app.py    # 领用门户 → 监听 0.0.0.0:5001
```

- 启动时会自动建表（幂等，不会动已有数据）、自动生成 `.secret`（会话密钥，丢了会重新生成，仅导致已登录用户需重新登录一次）。
- 验证：浏览器打开 `http://服务器IP:5000/`（后台登录页）和 `http://服务器IP:5001/`（门户申请页）。

> 调试时若想用 Flask 开发服务器（带详细报错），设环境变量 `FLASK_DEV=1` 再启动。生产请勿设。

---

## 五、进程守护（开机自启 + 崩溃自拉，二选一按服务器系统）

手动启动的进程关掉 SSH 就没了，必须用系统的进程管理器托管。

### Linux —— systemd（推荐）

`/etc/systemd/system/wms-admin.service`：

```ini
[Unit]
Description=WMS Admin (app.py)
After=network.target

[Service]
WorkingDirectory=/opt/code_1
ExecStart=/opt/code_1/.venv/bin/python /opt/code_1/app.py
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/wms-portal.service`：同上，把 `app.py` 换成 `apply_app.py`、Description 改成 Portal。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wms-admin wms-portal
sudo systemctl status wms-admin wms-portal   # 查看运行状态
```

### Windows Server —— 注册为服务（用 NSSM）

```powershell
# 下载 nssm.exe 后：
nssm install WMS-Admin  "C:\path\to\python.exe" "C:\path\to\code_1\app.py"
nssm set     WMS-Admin  AppDirectory "C:\path\to\code_1"
nssm start   WMS-Admin

nssm install WMS-Portal "C:\path\to\python.exe" "C:\path\to\code_1\apply_app.py"
nssm set     WMS-Portal AppDirectory "C:\path\to\code_1"
nssm start   WMS-Portal
```

> 仓库里的 `serve.ps1` 是开发机上用的临时看门狗脚本（写死了开发者本机路径），**生产环境不要用**，请用上面的 systemd / NSSM。

---

## 六、反向代理（可选，但建议）

直接暴露 5000/5001 也能用。若要 HTTPS / 走域名 / 标准 80·443 端口，前面架一层 Nginx：

```nginx
# 后台（建议限内网访问）
server {
    listen 80;
    server_name wms.内网域名;
    location / { proxy_pass http://127.0.0.1:5000; proxy_set_header Host $host; }
}
# 领用门户（员工扫码用）
server {
    listen 80;
    server_name apply.公司域名;
    location / { proxy_pass http://127.0.0.1:5001; proxy_set_header Host $host; }
}
```

HTTPS 用 Let's Encrypt（certbot）免费证书即可。

---

## 七、数据备份（重要）

`warehouse.db` 和 `uploads/` 是**唯一的数据真相**，且不在 git 里，服务器一旦损坏无处可恢复。

- 程序内置：每天 **凌晨 2:00** 自动备份数据库到 `backups/`（APScheduler 定时任务，进程跑着就生效）。
- 仍建议在服务器层面再加一层：定时把整个项目目录（含 `warehouse.db` + `uploads/`）同步/快照到另一台机器或对象存储。

---

## 八、安全注意

- **后台（5000）建议只在内网开放**，或加 IP 白名单——它是有登录态的完整管理后台。
- **门户（5001）可对员工网段/公网开放**——它设计上只能写一条申请单，读不到也改不了库存。
- `.secret` / `.deepseek_key` 等敏感文件已在 `.gitignore` 中，不会进版本库。
- 默认无 HTTPS，公网暴露的门户请务必经 Nginx 套上 HTTPS。

---

## 九、最小部署清单（给运维照着勾）

- [ ] 拉取代码到服务器（如 `/opt/code_1` 或 `C:\app\code_1`）
- [ ] `python -m venv .venv` 并 `pip install -r requirements.txt`
- [ ] 把数据包里的 `warehouse.db` 和 `uploads/` 放到项目根目录
- [ ] 手动 `python app.py` / `python apply_app.py` 各跑一次，浏览器验证 5000 / 5001 能打开
- [ ] 用 systemd / NSSM 注册两个服务，设为开机自启
- [ ]（可选）Nginx 反向代理 + HTTPS
- [ ] 确认每日备份生效，并在服务器层加一层异地备份
