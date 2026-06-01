# code_1 项目交接文档

> 面向**接手的开发 / 运维**。读完这份你应能独立跑起来、部署、回滚，并避开已知的坑。
> 最后更新：2026-05-29。代码当前版本：git `db0c30c`（已推送 GitHub，本地与云端同步）。

---

## 1. 这是什么

**园区物资仓储管理系统**——面向约 20 人内部使用，管理武汉园区的物业/办公物资（入库、出库、库存、库位、盘点、报损、领用申请）。

- **技术栈**：Python 3.11+（开发用 3.14）、Flask + Jinja2 + SQLite，前端纯 HTML/CSS/原生 JS（**无前端框架，无 Bootstrap**）。
- **数据规模**（真实数据，已导入）：585 个物品(SKU) / 140 个库位（84 个园区真实库位 + 56 个旧派生库位挂在「待归位」）/ 603 条库存 / 2 个仓库（武汉仓库、待归位）。

### 由两个独立进程组成（重要）

| 进程 | 文件 | 端口 | 用途 | 登录 | 暴露范围 |
|---|---|---|---|---|---|
| 后台 | `app.py` | **5000** | 仓管全功能后台 | 需登录 | 建议仅内网 |
| 领用门户 | `apply_app.py` | **5001** | 免登录公开页，员工扫码提领用申请 | 免登录 | 可对员工/公网 |

两进程**共用同一个 `warehouse.db`**；门户只能写一条领用申请，读不到也改不了库存，故可安全对外。

---

## 2. 怎么跑 / 怎么部署 / 用什么网址访问

### 本地启动
```powershell
cd C:\chat_code\code_1
pip install -r requirements.txt
python app.py          # 后台   → 0.0.0.0:5000
python apply_app.py    # 门户   → 0.0.0.0:5001
```
两个进程默认用生产级 **waitress**；调试想用 Flask 开发服务器设环境变量 `FLASK_DEV=1`。

### 访问网址
- **服务器本机自测**：`http://localhost:5000`（后台）/ `http://localhost:5001`（门户）
- **给同事用**：`http://<服务器IP>:5000` 和 `http://<服务器IP>:5001`（服务器上 `ipconfig`/`ip addr` 查 IP）
- **配了 Nginx 域名后**：用域名，端口可省（见 DEPLOY.md 第六节）

### 部署到公司服务器
照 **`DEPLOY.md`**（同目录）做：环境/依赖/进程守护(systemd 或 Windows NSSM)/反向代理/备份/最小部署清单。
- 代码会自动建 `uploads/`、`backups/` 目录，全新拉取不报错。
- `serve.ps1` 是**开发机临时看门狗**（写死了开发者本机路径），**生产别用**，用 systemd/NSSM。

### 管理员登录
默认种子账号：`admin` / `admin123`（首次登录后建议改密；账号定义见 `database.py` 的 `SEED_USERS`）。

---

## 3. 数据与还原点（出事怎么恢复）

> **关键认知：git 只存代码，不存数据。** `warehouse.db` 和 `uploads/` 都在 `.gitignore` 里，推 GitHub 带不走。完整还原点 = **代码(GitHub) + 数据快照(单独存)**。

- **代码**：GitHub `https://github.com/g3207779628-sys/code_1`（分支 `main`）。
- **数据快照**：`C:\chat_code\code_1_数据备份\20260529_最终导入版\`（`warehouse.db` + `uploads/` 629 张图，约 1.1G）。⚠️ 这份和原项目在同一块 C 盘，**建议再拷一份到 U盘/网盘**才算真备份。
- **程序内置备份**：每天 02:00 自动把 `warehouse.db` 拷到 `backups/`（APScheduler，进程跑着就生效）。

### 出事回滚（代码被改坏 / 数据被弄乱）
```powershell
cd C:\chat_code\code_1
git fetch origin; git reset --hard origin/main            # 代码回到云端最新版
# 再把数据快照里的 warehouse.db 和 uploads\ 拷回项目根目录覆盖
```

---

## 4. 已知的坑 + 最近修复 + 待办（最值钱的一节）

### ⚠️ 坑 1：改了代码必须重启进程
生产用 waitress，**运行中的进程把代码/模板揣在内存里**，改了 `.py`/模板**不重启就不生效**。重启后台：停掉占用 5000 的进程，重新 `python app.py`（serve.ps1 看门狗会在 30s 内自动拉起；或手动起）。

### ⚠️ 坑 2：GitHub 推送要走 Clash 代理（国内）
直接 `git push` 报 `schannel: failed to receive handshake`。本仓库 `.git/config` 已配好解法：`http.proxy=http://127.0.0.1:7897` + `https.proxy` 同值 + `http.version=HTTP/1.1` + **SSL 后端保持 schannel**（换 openssl 反而 `unexpected eof`）。节点不稳时多重试两次即可。要求本机 Clash 在 `127.0.0.1:7897` 监听。

### ⚠️ 坑 3：外部通知发送绝不能走系统代理
飞书/企业微信/阿里云短信等都是**国内服务**。Windows 上 `requests` 会自动套用「系统代理」(Clash 写的注册表项)，把国内请求甩去国外节点 → TLS 中断。`notifications.py` 已用一个 `trust_env=False` 的会话(`_HTTP`)统一直连解决。**新增任何外部 HTTP 调用都要用 `_HTTP`，别用裸 `requests`。**

### ⚠️ 坑 4：飞书 webhook 的格式与成功判定（已修，留记录）
飞书自定义机器人要的 body 是 `{"msg_type":"text","content":{"text":...}}`，发 `{"text":...}` 会被飞书 `code:19002` 拒收。且响应判定要按 `code/errcode/StatusCode` 任一≠0 判失败（旧代码"无 errcode 就算成功"会把拒收误报成"已发送"）。均已在 `notifications.py` 修复。配置在 `notification_channel` 表（webhook 渠道，preset=feishu）；若机器人开了"签名校验"，在 config 里加 `secret` 即会自动带 `timestamp+sign`。

### ⚠️ 坑 5：危险脚本别在生产跑
- 🔴 `wipe_all_data.py` —— **清空整库**，生产千万别碰。
- 一次性数据迁移脚本（已跑完，留作历史）：`import_real_data.py` / `import_filee_locations.py` / `merge_to_wuhan.py`。
- 造测试数据脚本：`seed_*.py` / `reset_*.py` / `_verify_batch2.py`。
- 提交时**不要 `git add -A`**，按文件挑（历史上混过 `.secret`/db 备份；现已 gitignore，但仍保持习惯）。

### 📌 已知待办 / 未决事项
1. **物品↔真实库位的归位**：56 个旧派生库位的物品现挂在「待归位」仓库，尚未一一指派到 84 个园区真实库位。原因：源表(行政部门记录)位置描述随意，自动匹配不可靠（详见 [`.planning`/记忆或与用户确认]）。需人工或半自动指派。
2. **低库存预警测试数据**：为测预警，给 5 个物品(方巾纸/海绵擦/氧净/玻璃扎壶/保温壶)把 `safety_stock` 设成 25（实际库存 5）。**若不需要，记得把它们的 safety_stock 改回 0 或设成真实预警值。**
3. **飞书最终确认**：代码侧已发 `code:0 success`；需在飞书群确认确实收到（排除机器人被移出群/看错群）。
4. **数据异地备份**：把数据快照拷到本机以外（U盘/网盘/另一台机）。
5. **GitHub 仓库可见性**：确认是否设为 Private（公司内部系统源码）。

---

## 5. 目录速览
```
code_1/
├─ app.py              后台主程序(5000)，含路由+APScheduler定时(09:00预警/02:00备份/月初快照)
├─ apply_app.py        领用门户(5001)
├─ database.py         表结构(SCHEMA)+种子数据+迁移
├─ notifications.py    6渠道通知+预警扫描(低库存/待处理报损)
├─ attachments.py / backup.py / snapshots.py / exporters.py / forecasting.py / data_importer.py
├─ templates/          62个Jinja2页面模板(后台全靠它渲染，勿删)
├─ static/             CSS/JS
├─ uploads/            物品图+库位图(运行时数据，不在git)
├─ backups/            自动备份输出(不在git)
├─ warehouse.db        SQLite数据库(不在git)
├─ requirements.txt    依赖清单
├─ DEPLOY.md           部署说明(给运维)
└─ HANDOFF.md          本文件
```
> 注：早期 UI 设计稿 `design/` 目录与 AI 协作规则 `CLAUDE.md` 已移出仓库，归档在 `C:\Users\32077\Documents\Obsidian Vault\code_1-归档\`。

---

## 6. 一句话上手
拉代码 → `pip install -r requirements.txt` → 放数据包(warehouse.db + uploads)到根目录 → `python app.py` + `python apply_app.py` → 浏览器开 `服务器IP:5000`/`:5001` → 用 admin/admin123 登录。改完代码记得重启进程。出事 `git reset --hard origin/main` + 拷回数据快照。
