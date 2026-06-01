# 部署操作手册（Windows Server · 新手逐步版）

> 适用：你一个人，把 `code_1` 园区物资仓储系统部署到一台 **Windows Server**，让同事通过浏览器访问。
> 用法：从上往下，一步一步照做。每步都写了「该看到什么」和「出错怎么办」。
> 先做【阶段 1～6】把**内网**跑通（当天能上线）；【阶段 7】公网+HTTPS 以后再做。

---

## 阶段 0｜在你自己的电脑上，先打个包（10 分钟）

服务器需要两样：**代码** + **数据**（数据库和图片不在 git 里，必须一起带）。最省事是把整个项目压成一个 zip。

1. 为了让包小一点，先删掉不需要带的临时文件（可选）：
   - 打开 `C:\chat_code\code_1`，删掉里面的 `__pycache__` 文件夹和 `backups` 文件夹里的旧备份（这些服务器会自动重建，不用带）。
2. 右键 `code_1` 文件夹 → 「压缩成 ZIP / 发送到→压缩文件夹」，得到 `code_1.zip`（里面已包含 `warehouse.db` 和 `uploads/`，约 1.2G）。
3. 这个 zip 待会儿要拷到服务器。

> 为什么不用 git 拉？服务器上 git 连 GitHub 在国内可能要配代理，对新手是额外的坑。**一个 zip 拷过去最省心**，代码和数据一次到位。

---

## 阶段 1｜远程连上服务器（5 分钟）

1. 在你自己电脑按 `Win + R`，输入 `mstsc`，回车 → 打开「远程桌面连接」。
2. 「计算机」填导师给你的**服务器 IP**，点「连接」。
3. 输入导师给的**账号、密码**，登录。
4. **看到什么**：屏幕变成服务器的 Windows 桌面 = 连上了，你现在操作的就是那台服务器。

> 传文件的准备：在「远程桌面连接」点开前，点左下「显示选项」→「本地资源」→「详细信息」→ 勾上「驱动器」。这样连上后，在服务器的「此电脑」里能看到你本机的硬盘，直接拖文件过去。

**出错怎么办**：连不上 → 99% 是 IP/账号密码不对，或公司要求先连 VPN。问导师要正确的 IP 和是否需要 VPN。

---

## 阶段 2｜在服务器上装 Python（10 分钟）

1. 先看服务器有没有装：在服务器上按 `Win + R` → 输 `powershell` → 回车，打开蓝色窗口，输入：
   ```powershell
   python --version
   ```
   - 显示 `Python 3.11`（或更高）→ 已装好，**跳到阶段 3**。
   - 显示「不是内部命令」或版本低于 3.11 → 继续往下装。
2. 服务器上打开浏览器，去 `https://www.python.org/downloads/` 下载 **Python 3.12** 的 Windows 安装包。
3. 双击安装，**第一屏最下面务必勾选 ☑ "Add python.exe to PATH"**，再点 "Install Now"。
4. 装完关掉 PowerShell 重新开一个，再输 `python --version` 确认显示出版本号。

**出错怎么办**：装完还说"不是内部命令" → 多半是没勾"Add to PATH"，重装一遍记得勾上。

---

## 阶段 3｜把项目和数据放上服务器（看网速，10~40 分钟）

1. 把你阶段 0 做好的 `code_1.zip` 拷到服务器：
   - 用阶段 1 勾的「驱动器」：在服务器「此电脑」里找到你本机硬盘，把 `code_1.zip` 拖到服务器的 `C:\` 下；
   - 或直接复制粘贴（Ctrl+C 本机文件 → 服务器桌面 Ctrl+V），1.2G 会传一会儿，耐心等。
2. 在服务器上把 `C:\code_1.zip` **右键 → 全部解压** 到 `C:\`，得到 `C:\code_1`（确认里面有 `app.py`、`warehouse.db`、`uploads` 文件夹）。

**该看到什么**：`C:\code_1\` 里有 `app.py`、`apply_app.py`、`templates`、`warehouse.db`、`uploads`。

---

## 阶段 4｜装依赖 + 手动先跑起来验证（10 分钟）

1. 在服务器 PowerShell 里进项目目录、装依赖：
   ```powershell
   cd C:\code_1
   pip install -r requirements.txt
   ```
   等它装完（出现一堆 Successfully installed ... 就对了）。
2. 先手动起**后台**，验证能跑：
   ```powershell
   python app.py
   ```
   **该看到**：`[server] waitress on 0.0.0.0:5000 ...`。这个窗口**别关**。
3. 在服务器上打开浏览器，访问 `http://localhost:5000` → 看到登录页 = 成功。用 `admin` / `admin123` 登录试试。
4. 验证 OK 后，回 PowerShell 窗口按 `Ctrl + C` 停掉（阶段 5 会换成自动运行）。

**出错怎么办**：
- `pip 不是内部命令` → Python 没装好/没加 PATH，回阶段 2。
- 报缺某个库 → 再跑一次 `pip install -r requirements.txt`。
- 页面打不开 → 确认那个 `python app.py` 窗口还开着、没报错。

---

## 阶段 5｜设成"开机自启 + 崩溃自动重启"（用 NSSM，15 分钟）

手动跑的窗口一关进程就没了。用 NSSM 把两个进程注册成 Windows 服务，让它常驻。

1. 服务器浏览器去 `https://nssm.cc/download` 下载 nssm，解压，把里面 `win64\nssm.exe` 拷到 `C:\code_1\` 下（方便找）。
2. PowerShell 里执行（**先查 python 的完整路径**）：
   ```powershell
   (Get-Command python).Source
   ```
   记下它输出的路径，比如 `C:\Python312\python.exe`（下面用 <PY> 代替）。
3. 注册**后台服务**（5000）：
   ```powershell
   cd C:\code_1
   .\nssm.exe install WMS-Admin <PY> C:\code_1\app.py
   .\nssm.exe set WMS-Admin AppDirectory C:\code_1
   .\nssm.exe start WMS-Admin
   ```
4. 注册**领用门户服务**（5001）：
   ```powershell
   .\nssm.exe install WMS-Portal <PY> C:\code_1\apply_app.py
   .\nssm.exe set WMS-Portal AppDirectory C:\code_1
   .\nssm.exe start WMS-Portal
   ```
5. 验证：浏览器再开 `http://localhost:5000` 和 `http://localhost:5001`，都能开 = 两个服务跑起来了。以后服务器重启它们会自动起，崩了也自动拉。

> 把 `<PY>` 换成第 2 步查到的真实路径。例：`.\nssm.exe install WMS-Admin C:\Python312\python.exe C:\code_1\app.py`

**出错怎么办**：`start` 后端口没起来 → `.\nssm.exe edit WMS-Admin` 打开图形界面检查 Path/AppDirectory 是否填对。

---

## 阶段 6｜放行防火墙 + 让同事访问（10 分钟）

1. PowerShell **以管理员身份**运行（开始菜单搜 PowerShell→右键→以管理员身份运行），开放两个端口：
   ```powershell
   New-NetFirewallRule -DisplayName "WMS-5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
   New-NetFirewallRule -DisplayName "WMS-5001" -Direction Inbound -Protocol TCP -LocalPort 5001 -Action Allow
   ```
2. 查服务器的内网 IP：
   ```powershell
   ipconfig
   ```
   找「IPv4 地址」，比如 `192.168.1.50`。
3. **让同事在自己电脑浏览器访问**：
   - 后台：`http://192.168.1.50:5000`
   - 领用门户：`http://192.168.1.50:5001`（可生成二维码贴墙上给员工扫）

**该看到什么**：换一台公司内网的电脑，能打开上面网址 = 内网部署完成 ✅。

**出错怎么办**：本机能开、别人打不开 → 防火墙端口没放行（回第 1 步，注意要管理员运行），或公司网络隔离了这台机器（问 IT）。

---

## 阶段 7｜公网 + HTTPS（第二阶段，可日后做）

⚠️ 国内做公网网站有道硬门槛，**尽早让公司启动**：

1. **域名**：买/申请一个域名（如 `wms.公司.com`）。
2. **ICP 备案**：域名要解析到国内服务器对外提供服务，**必须先备案**（走公司主体，约 1~2 周），否则会被拦。**这步最耗时，先推进。**
3. **装 Nginx**（Windows 版）做反向代理：把域名的 80/443 转发到本机 5000/5001。
4. **HTTPS 证书**：用云厂商免费证书或 Let's Encrypt，配到 Nginx。

> 这一阶段等内网跑顺、域名备案下来后再做。到时把这阶段单独发我，我给你 Nginx 配置和证书的具体步骤。

---

## ✅ 最终验收清单（逐条打勾）

- [ ] 远程桌面能连上服务器
- [ ] `python --version` ≥ 3.11
- [ ] `C:\code_1` 里有代码 + `warehouse.db` + `uploads`
- [ ] `pip install -r requirements.txt` 成功
- [ ] 服务器本机 `localhost:5000` / `5001` 能打开
- [ ] NSSM 两个服务 WMS-Admin / WMS-Portal 已 start
- [ ] 防火墙放行 5000/5001
- [ ] 另一台内网电脑用 `服务器IP:5000` 能访问
- [ ]（第二阶段）域名备案 → Nginx → HTTPS

---

## 日常维护小抄

- **改了代码后**：重新打包代码拷上去覆盖，然后 `.\nssm.exe restart WMS-Admin`（和 WMS-Portal）让新代码生效。
- **看服务状态**：`.\nssm.exe status WMS-Admin`。
- **数据备份**：程序每天 02:00 自动备份到 `C:\code_1\backups\`；建议你再定期把 `warehouse.db` 和 `uploads\` 拷到另一台机/网盘。
- **管理员账号**：`admin` / `admin123`，登录后尽快改密。
