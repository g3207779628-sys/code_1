# CLAUDE.md — code_1 项目

> 这份文件每次 Claude Code 启动会自动读。
> 想让 Claude 知道什么、按什么规矩干活，都写在这里。

---

## 项目是什么

**code_1 — 金山仓储**：办公用品 + 老板茶水/酒柜的内部仓储管理系统，20 人左右使用规模。


## 文档可信度声明

> 本文件的章节有两类可信度，使用前注意区分：
> - **稳定章节**（视觉规范 / 工程约定 / 沟通偏好 / Git 协作）→ **以本文件为准**
> - **易变章节**（菜单 / 字段 / 路由 / 表结构）→ **以代码为准**（[app.py NAV_TREE](app.py)、[database.py SCHEMA](database.py)），本文档对应段可能过时
>
> 改完大改造后，**最后一步**必须同步修订本文件对应段。

---

## 菜单结构

6 个一级菜单，鼠标悬停弹出二级（CSS `:hover`，无 JS）。**以 [app.py NAV_TREE](app.py) 为真理源**，下面是 v15 快照：

```
日常使用：     工作台 / 待审批中心
日常操作：     入库 / 出库 / 调拨 / 报损 / 盘点
查询分析：     库存查询 / 库存流水 / 经营报表 / 库存预测
物品数据维护： 物品 / 物品所属方 / 物品管理方 / 物品类别 / 物品大类
仓库数据维护： 仓库 / 仓库分配部门 / 仓库使用部门 / 仓库类型 / 责任人 / 库位
系统：         用户与权限 / 岗位管理 / 通知渠道 / 数据备份 / 修改密码
```




---

## 视觉规范（v8 ui_white · 纯白 + 橙红 · 紧凑 C 风）

**色板**（CSS 变量在 `static/style.css` `:root`，来源 `design/ui_white_shared.css`）：

| 用途 | 变量 | 色值 |
|---|---|---|
| 主背景（body / container 工作区） | `--bg-soft` | `#F7F8FA` 软底 |
| 卡片 / sider / header | `--bg-card` / `--bg-base` | `#FFFFFF` 纯白 |
| 输入框 | `--bg-input` | `#FAFAFB` 极浅灰 |
| 浅橙底（active / 弹层） | `--bg-accent-soft` | `#FFF0EA` |
| 主文字 | `--ink` | `#1A1A1A` 接近黑 |
| 辅文字 | `--ink-quiet` | `#6E6E6E` |
| **橙红 accent**（按钮/active/链接/关键数字） | `--accent` | `#FF4500` |
| 橙红 hover | `--accent-hover` | `#D63A00` |
| 边框 | `--line` | `#ECECEC` |
| 输入框边框 | `--line-input` | `#E0E0E0` |
| 状态：成功 / 错误 / 警告 / 信息 | `--ok` / `--err` / `--warn` / `--info` | `#16A34A` / `#DC2626` / `#EA8600` / `#1F6FEB` |

**字体**：`-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif`；mono：`"SF Mono", "JetBrains Mono", Consolas`
**全局基字号**：13px（C 风紧凑）

**布局规格**：
- **Sider 宽度**：200px（v7 220 → v8 200 紧凑）
- **Header 高度**：52px（v7 56 → v8 52）
- **Container max-width**：1340px + `margin: 0 auto` 全局水平居中

**核心组件规则**：
- **路由分层（v9）**：
  - `/`（endpoint=`welcome`）→ 公共/已登录双角色通用首页。**未登录访客**看公共 hero + "进入管理系统"按钮；**已登录用户**看带 sider 的中央 hero（无按钮），从菜单"日常使用 → 工作台"进 dashboard
  - `/workbench`（endpoint=`index`）→ 按 `session.position` 分发到 11 个 `dashboard_*.html`。endpoint 仍是 `index`，所有 `url_for('index')` 不变
  - `/dashboard`（endpoint=`dashboard`）→ 经营报表（不受 v9 影响）
- **Welcome hero（v9 通用首页）**：大字 `WELCOME` 描边/装饰用 `--accent-deep`（`#D63A00` 深一档），按钮用 `--accent`（`#FF4500` 主题色）。CSS 类：`.v9-hero` / `.v9-hero-pre` / `.v9-hero-main` / `.v9-hero-sub` / `.v9-hero-meta`；公共版整页布局 `.v9-welcome-public`；已登录版居中包裹 `.v9-welcome-authed`
- **侧栏**：白底 + 一级菜单悬停（CSS `:hover`）弹出二级 + sider-brand 纯文字"金山仓储"（无图标）
- **active 状态**：左侧粗橙红边线 + 浅橙底 `#FFF0EA` + 橙红字
- **输入框**：极浅灰底 `#FAFAFB` + 1px `#E0E0E0` 边框 + focus 时橙红边框 + 浅橙阴影圈
- **主按钮**：橙红 `#FF4500` 填充 + 白字 + 圆角 4px
- **小卡片必居中（重要约定）**：
  - 登录 / 改密等 max-width ≤ 480px 的单卡片页面 → 用 `.center-card`（默认 480px）或 `.center-card.narrow`（400px，登录用）
  - 业务表单（入库 / 出库 / 调拨 / 报损 / 仓库 / 库位 / 商品等）→ 用 `.form-wrap`（max-width 920px + margin auto）
  - 兜底：`.container > .card:only-child` 自动 max-width 720px 居中（单卡片小表单不用包 wrap 也居中）
- **提示消息 3 套组合**：
  - 成功 → **Toast 浮窗**（右上角 3 秒自动消失）
  - 错误/警告/信息 → **顶部横幅**（左侧粗色边 + 圆形图标 + ×手动关闭）
  - 表单字段校验 → **内联消息** `<span class="inline-msg success|error|warn|info">提示</span>`

**字体层级

> 任何卡片 / 表格 / 图表里三层字体大小**严格递减**：描述 > 表头 > 数据。违例必修。

1. **图表 / 卡片描述（最大）** — 卡片头副标题、`page-sub`、`.head .meta`、图表 caption / 轴 label → **15px / 14px** + 主色 `--ink` + 字重 500
2. **第一行 / 表头 / label（中等）** — `<th>`、字段 `<label>`、KPI label → **13px** + `--ink-soft` + 不粗体
3. **里面的数据（最小）** — `<td>` 单元格、tag chip、mono 数字、辅助说明 → **12px** + `--ink-quiet` / `--ink`

写新模板前对照检查；如有冲突先以"描述 > 表头 > 数据"为准统一调，不要让数据反而比描述字大。

**组件主题一致性检查（任何新组件 / 新模板提交用户前必做）**：

- 颜色：仅用 CSS 变量（`--accent` / `--accent-deep` / `--ink*` / `--bg-*` / `--line*`）；不允许写裸色值 `#FF4500` / `#FFFFFF`（写裸值=断绝主题统改能力）
- 字体：用项目已声明字体栈，不引入新 font-family
- 圆角：≤ 8px（小卡片 6px，按钮 4px）
- 间距：用 4 的倍数（8 / 12 / 16 / 20）不要随手 7px / 11px / 13px
- 提交前自查：能不能在 `static/style.css` 找到同款 class 复用？能复用就不要 inline style 重写一遍

**禁忌**（不要这样做）：
- ❌ 暖米白底（旧 v7 `#FBFAF7`） — 现在用纯白 `#FFFFFF` 和软底 `#F7F8FA`
- ❌ 暖橙 `#D97737`（旧 v7） — 现在用橙红 `#FF4500`
- ❌ 深色 sidebar（旧 Stripe/Ant Pro 风）
- ❌ 蓝色 / 深绿（旧版本的色板）
- ❌ border-radius > 12px
- ❌ 装饰性 emoji 当图标用
- ❌ 渐变背景（除欢迎页 hero / 登录页 radial-gradient）
- ❌ sider-brand 加任何图标方块（只用文字"金山仓储"）
- ❌ **小卡片靠左贴 sider** — 必须用 `.center-card` 或 `.form-wrap` 居中

**视觉规范完整 mockup**：`design/ui_white_index.html`（入口） · `design/ui_white_c_*.html`（方案 C 7 个代表页：dashboard / inventory / stock_log / welcome / login / password_change / inbound_form）

---

## 启动命令

```powershell
cd C:\chat_code\code_1
python app.py
```

⚠️ Flask debug reload 在 Windows 上有 race condition — **改完多个 .py 文件后，主动 Ctrl+C 重启**，不要靠自动 reload。

⚠️ 重启前**先 netstat 查 5000 端口占用 + Stop-Process 杀干净**，否则旧版代码会继续响应请求，让你以为改动没生效。

---

## 关键模块文件

- **核心**：`app.py`（2000+ 行单文件路由）/ `database.py`（SCHEMA + SEED + _migrate）
- **业务模块**：`exporters.py`（CSV/Excel 导出）/ `notifications.py`（通知 + 预警扫描）/ `forecasting.py`（销量预测）/ `snapshots.py`（月度库存快照）/ `backup.py`（自动备份）/ `attachments.py`（多文件上传）
- **数据脚本**：`reset_themed.py`（主题数据彻底重置）/ `seed_demo.py`（仅补 demo 流水）
- **设计稿**：`design/perms_index.html`（权限管理 UI 4 套方案对比入口）

---

## 关键技术细节

- **库存双轨**：任何变动**必先写 `stock_log` 再更新 `inventory`**（流水做审计、快照做查询）
- **FIFO 拣货**（v4 起）：按 `batch.id ASC` 出库（早进先出），算法在 `app.py:_allocate_fifo`。已砍 `batch.expiry_date` / `production_date` 字段，不再做临期预警
- **通知渠道**（v6 砍到 3 个）：站内通知（默认启用）/ 邮件 SMTP / 短信（阿里云） — 短信和邮件"只写接口"，代码完整但凭证未填。剩余 5 个渠道的 `_send_*` 函数留在 `notifications.py`，需要时只要在 `SEED_CHANNELS` 加行 + 模板加 details 块即可恢复
- **附件统一存储**：`uploads/{中文类型}/{order_no}/`（入库单 / 出库单 / 报损单 / 盘点单 / 调拨单 5 类）；JSON 数组存到对应表的 `attachments` 字段；下载走 `/uploads/<path>` 路由（需登录）
- **Picker 框架**（v6 加）：5 个 picker 页面（sku / location / warehouse / position / supplier） + `static/js/picker.js`（sessionStorage 暂存原表单 → URL 回跳填充）。支持单选 / 多选（`?multi=1`）+ 内嵌新建

---

## 工程约定（必守）

- SQL **必须**用 `?` 占位符，绝不字符串拼接（防注入）
- 数据库写操作**必须**用 `with get_conn() as conn:` 包事务
- **任何库存变动必须先写 stock_log 再更新 inventory** — 双轨制（快照 + 流水）
- **前后端必须配套**：加了字段就要在前端模板里显示
- 改完多个 .py 文件后**提醒我 Ctrl+C 重启 Flask**，并先杀端口 5000 上的旧进程
- 不要主动创建 README、文档、测试文件（除非用户明确要）
- 不要写代码注释（代码本身要清晰）
- 异常类：`sqlite3.IntegrityError`，**不是** `conn.IntegrityError`
- **改 SQLite CHECK / 重建表** 时必须 `PRAGMA legacy_alter_table = ON`，否则 `ALTER TABLE RENAME` 会把别的表 FK 引用也改掉，留下孤儿引用（v2 升级时踩过坑，详见 `_migrate()` 中的修复步）
- 加菜单要在 `app.py:NAV_TREE` 注册；想给某岗位/预设默认带上，要同步进 `SEED_ROLE_PRESETS`
- APScheduler 启动**只能**在 `__main__` 入口（`use_reloader=False`），否则 debug=True 会双开调度器
- **下拉框能避免就避免**（v6 用户偏好）：用 picker 跳新页选 + 内嵌新建；不要 inline modal、不要 select multiple
- **任务执行顺序（v22 起硬约定）**：后到的需求**后执行**，不打断在做的任务。除非用户明确说"先做这个"或者属于「计划讨论 / 澄清疑问」，否则按 todo 队列从早到晚做完。**全部做完才汇报**，不要做一个汇报一个让用户等待。

---

## 设计文档（唯一真理源）

- **业务/表结构原始定义**：`c:\Users\32077\kclaw\openclaw\workspace\仓库管理系统设计文档.md`
- **当前实际表结构**：`database.py` 里的 `SCHEMA`（以代码为准，文档可能滞后）
- **UI 方案样品**：`design/perms_index.html`（权限 UI 4 方案对比）/ `design/mockup_*.html`（各岗位 dashboard）

不要凭记忆造表或流程，先回 `database.py` 和设计文档查。

---

## 怎么和我沟通

- 
- 我重视"为什么"，不只是"怎么做"
- 步骤说清楚：做完看到什么、出错怎么办
- **节奏由我定**：能一气呵成的让我说一气呵成；没说就做完一件汇报一件
- 出 bug 别自己反复改，先告诉我现状，**一起判断**再动
- 不需要恭维、不需要 emoji
- **中文回答**

### 我喜欢的提问 / 你喜欢的回答模板

"**给 XX 看 / 给 XX 用** + **做什么** + **放在哪** + **给我几种** + **沿用 / 参考什么**"

→ 我说"给我几种 / 几个方案"时，做 3-4 个对比，配 index 入口页 + 横向对比表 + 选型建议。

---

