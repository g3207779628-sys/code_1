# CLAUDE.md — code_1 项目

> 这份文件每次 Claude Code 启动会自动读。
> 想让 Claude 知道什么、按什么规矩干活，都写在这里。

---

## 项目是什么

**code_1 — 预制菜 B2C 仓储管理系统** — 20 人规模的小型内部工具。

开发者（我）是新手，在金山公司实习。这个项目既是工具，也是我学习"通用全栈"的载体。

- **业态**：预制菜 B2C 单通道（天猫/京东/抖音/小程序）
- **技术栈**：Python 3.14 + Flask + SQLite + Bootstrap 5(CDN) + Bootstrap Icons + Chart.js + APScheduler
- **不要引入**：Docker、SQLAlchemy / ORM、Node.js、React、Vue、TypeScript、bcrypt（用 werkzeug 自带的就够）
- **运行方式**：Windows 本机直接 `python app.py`，浏览器访问 `http://localhost:5000`
- **数据存储**：项目目录下的 `warehouse.db`（SQLite 单文件）

---

## 启动命令

```powershell
cd C:\chat_code\code_1
python app.py
```

⚠️ Flask debug reload 在 Windows 上有 race condition——**改完多个 .py 文件后，主动 Ctrl+C 重启**，不要靠自动 reload。

---

## 设计文档（唯一真理源）

所有表结构、流程、术语定义见：
`c:\Users\32077\kclaw\openclaw\workspace\仓库管理系统设计文档.md`

不要凭记忆造表或流程，先回到设计文档查。

---

## 开发阶段（分阶段闭环）

- [x] 阶段 0：项目初始化 + Git
- [ ] 阶段 1：主数据 + 登录
- [ ] 阶段 2：入库链 + 批次管理
- [ ] 阶段 3：销售链 + FIFO 拣货
- [ ] 阶段 4：报损 + 临期预警 + 盘点
- [ ] 阶段 5：退换货 + 质检
- [ ] 阶段 6：报表 + AI 亮点

每个阶段都要有可演示的闭环。不要跨阶段写代码。

---

## 视觉风格：工业仓储风

色板（写进 static/style.css，**不要修改这些 CSS 变量**）：

```css
--color-bg:        #F4F1EA  /* 米白主背景 */
--color-bg-alt:    #E8E3D8  /* 次背景，斑马纹 */
--color-border:    #D6CFC1
--color-text:      #1F1B16  /* 深棕黑 */
--color-text-soft: #6B6259
--color-primary:   #2F4F3E  /* 主深绿，navbar/表头/主按钮 */
--color-accent:    #D97D3D  /* 暖橙，库存预警 */
--color-in:        #5C8A3F  /* 入库 badge */
--color-out:       #A8412B  /* 出库 badge */
```

字体：
- 中文：`"PingFang SC","Microsoft YaHei","Source Han Sans SC",sans-serif`
- 数字/单号：`"JetBrains Mono",Consolas,monospace`（等宽，工业感）

**禁忌**：Bootstrap 默认蓝、渐变背景、emoji 当图标、border-radius > 4px。

---

## 工程约定（必守）

- SQL **必须**用 `?` 占位符，绝不字符串拼接（防注入）
- 数据库写操作**必须**用 `with get_conn() as conn:` 包事务
- **任何库存变动必须先写 stock_log 再更新 inventory**——双轨制（快照 + 流水）
- **前后端必须配套**：加了字段就要在前端模板里显示
- 改完多个 .py 文件后**提醒我 Ctrl+C 重启 Flask**
- 不要主动创建 README、文档、测试文件（除非用户明确要）
- 不要写代码注释（代码本身要清晰）
- 异常类：`sqlite3.IntegrityError`，**不是** `conn.IntegrityError`

---

## 怎么和我沟通

- 我是新手，**先生活类比、再技术细节**
- 我重视"为什么"，不只是"怎么做"
- 步骤说清楚：做完看到什么、出错怎么办
- **主导节奏**：不要一次性做 5 件事，做完一件汇报一件
- 出 bug 别自己反复改，先告诉我现状，**一起判断**再动
- 不需要恭维、不需要 emoji
- **中文回答**

---

## Git 协作约定

- commit 信息：英文小写 + 动词开头（`feat:` 新功能、`fix:` 修 bug、`docs:` 改文档、`chore:` 杂项）
- **小步提交**：每完成一个独立功能就 commit 一次
- 大功能开 feature 分支（如 `feature/fifo-picking`），完成后合并回 main
- 推送前先 `git status` 确认状态

---

## 参考项目

`c:\Users\32077\kclaw\openclaw\workspace\warehouse\` 是上一个练手项目，里面有验证过的模式可以借鉴：
- `role_required(*allowed_roles)` 权限装饰器
- 单据号生成 `generate_xxx_order_no()`
- 工业风样式 `static/style.css`
- 模板继承 `templates/base.html`

但**不要照抄表结构**——code_1 的表结构以"仓库管理系统设计文档 v0.2"为准。
