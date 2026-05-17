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

## 开发阶段

- [x] 阶段 0：项目初始化 + Git
- [ ] 阶段 1：主数据 + 登录
- [ ] 阶段 2：入库链 + 批次管理
- [ ] 阶段 3：销售链 + FIFO 拣货
- [ ] 阶段 4：报损 + 临期预警 + 盘点
- [ ] 阶段 5：退换货 + 质检
- [ ] 阶段 6：报表 + AI 亮点
