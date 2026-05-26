# code_1 — 预制菜 B2C 仓储管理系统

一个面向 20 人规模、预制菜 B2C 业态的仓储管理系统。基于 Flask + SQLite + Bootstrap 5。

## 快速启动

```powershell
pip install -r requirements.txt
python app.py
```

浏览器访问 http://localhost:5000

## 设计文档

完整业务流程、数据库表结构、MVP 功能清单见 [仓库管理系统设计文档 v0.2](../../Users/32077/kclaw/openclaw/workspace/仓库管理系统设计文档.md)。

## 开发阶段（MVP 已闭环）

- [x] 阶段 0：项目初始化 + Git
- [x] 阶段 1：主数据 + 登录（SKU / 仓库 / 库位 / 供应商 / 客户 / 用户角色）
- [x] 阶段 2：入库链 + 批次管理（入库单审批 → 批次入账 → 库存流水）
- [x] 阶段 3：销售链 + FIFO 拣货（销售单 → 自动按批次 FIFO 分配 → 拣货 → 出库）
- [x] 阶段 4：报损 + 临期预警 + 盘点
- [x] 阶段 5：退换货 + 质检（receive → qc → refund / reject）
- [x] 阶段 6：报表 Dashboard + AI 自然语言查询

## 当前模块（v2）

- **左侧二级悬浮菜单**：5 组（驾驶舱 / 业务 / 库存 / 主数据 / 系统），鼠标悬停一级弹出二级
- **角色与权限**：3 角色 × 11 岗位；菜单权限持久化到 `menu_permission` 表，管理员在「系统 → 用户与权限」勾选授权；新用户/重置密码后首次登录强制改密
- **业务链**：入库 / 销售 / FIFO 拣货 / 退换货（receive → qc → refund） / 报损 / 临期预警 / 盘点
- **库存**：库存查询（按 SKU 汇总）/ 库存流水（带日期 + SKU + 事件类型筛选）/ 商品明细页（按时间筛选入库 / 出库流水）
- **销量预测**：移动平均 + 安全系数算法，按近 1/3/6 月日均出库估下月控制量，支持按存储区筛选
- **报表导出**：库存 / 库存流水 / 盘点单 / 销量预测 均支持 CSV / Excel 导出，可选导出列
- **预警通知（6 渠道）**：站内通知 / 邮件 SMTP / 阿里云短信 / 企业微信 / QQ 机器人 (OneBot) / WPS Webhook；可在「系统 → 通知渠道」配置凭证 + 测试发送 + 配规则
- **存储区**：1 / 2 / 3 / 4 区（v2 替代旧的常温 / 冷藏 / 冷冻）
- **Dashboard**：按存储区库存月度趋势（12 个月 / 4 年切换）+ 库存 TOP 10 + 4 区分布 + 流水类型 + 临期分级
- **自动备份**：APScheduler 每日 02:00 拷贝 `warehouse.db` 到 `backups/`，保留 30 份；管理员页可手动备份

## 启动

开发：`python app.py`（自带 APScheduler 每日 09:00 预警 / 02:00 备份 / 月初 01:00 快照）

生产：`waitress-serve --port=5000 app:app`

## UI 改造资料

`design/` 目录下有两版重设计方案：
- v1：Ant Design Pro 风（`UI_redesign_v1_ant_pro.md` + 各岗位 mockup）
- v2：5 种成熟商业风格样板（Stripe / 建筑 / 新闻 / 餐厅 / 奢侈品），见 `UI_redesign_v2_styles.md`
