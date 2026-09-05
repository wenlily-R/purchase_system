# 正成能源采购系统 — Agent 工作守则

本文件由 Hermes 自动注入每个在此目录工作的会话。目的：让任何新会话（无论用什么模型）都能立即接手采购系统的开发/运维，无需用户重复交代背景。

## 一、必读技能（每次会话先加载，按顺序）

1. `purchase-system-dev` — 功能开发/演进：新功能表结构、前后端改动、端到端验证、全部版本演进实录（V11.13 起）
2. `purchase-system-ops` — 日常运维：启动、公网隧道、Excel 导入、代码自检、钉钉附件同步
3. `procurement-system-planning` — 系统建设全生命周期与历史坑

加载方式：用 skill_view 读取。技能内含完整开发史（版本号/改动/验证/坑），新会话读完即可无缝继续。

## 二、项目铁律（用户明确要求，违反=返工）

- **固定流程**：patch → `.venv/bin/python check_code.py` 自检 → 端到端实测 → git commit。每版必须真实验证。
- **复杂度红线**：任何实现不得比领导方案更复杂（用户原话「更复杂可不行」）。先给专业意见再实施，方案可暂存。
- **测试数据纪律**：测试单统一标记（含【测试】），清理按标记全删；**清理后必须复核**（待审批单应为 0）；凡走审批流的测试单，验证完立刻清审批实例+钉钉实例，否则用户手机收到真实审批推送。
- **后台进程纪律**：守护进程（app_watchdog/tunnel_guard）必须用独立后台方式启动，**绝不能挂在长 CLI 会话里**（2026-08-24 烧钱事故：绑会话 6 天、2080 次 API 调用、费用 63 元）。长会话不用就退出。
- **中文交流**：用户是中文/拼音输入，回复用简体中文，专业概念用大白话+打比方。

## 三、系统状态（2026-09-05 双机对齐确认）

- 双机路径：Mac(生产) `/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system`；Windows(开发) `C:\Users\35322\Desktop\purchase_system`
- 前端单文件 `templates/index.html`（HTML/CSS/JS 同文件），后端 `app.py`，venv `.venv/bin/python`
- 版本：V11.219（双机已对齐 GitHub main=fa4a6cd；旧版备份分支 remote-old-backup 勿删）；当前模型：xgxg 分身已切 Agnes AI（agnes-2.5-flash, custom provider）
- 菜单顺序约定：采购管理 = 采购申请 → 三方询价 → 采购订单 → 合同管理 → 供应商（三方询价在采购订单上方，勿改）
- 权限方案：界面统一（业务菜单全显示+无权限页面提示），后端数据过滤兜底，系统设置仅管理员
- 进程：Flask 用 `lsof -i :5899` 查 PID，重启按 PID 精准杀（勿用 pkill 模式匹配，会误杀新进程）

## 三之补、表结构迁移纪律（2026-09-05 起强制，双机都必须遵守）

数据库 `data/purchase.db` 不进 git（各机独立），代码才同步。**改表结构 = 必须写迁移文件**，只在自己库手工 ALTER 会让另一台代码跑起来报错：

1. 谁改了表结构（新表/新列/改列），在 `migrations/` 下新建 `YYYYMMDD_说明.sql` 写 ALTER/CREATE 语句，随代码一起 commit + push
2. 另一台 `git pull` 后执行 `python migrate_db.py`（Mac 用 `python3`），自动应用未执行的迁移（有备份+去重记录，可重复跑）
3. 迁移文件只写结构变更，不写业务数据；加列用 `ALTER TABLE 表名 ADD COLUMN 列名 类型`
4. 判断代码是否依赖新结构：pull 后先跑 migrate_db.py 再重启系统，顺序不能反
5. 忘了写迁移文件的信号：系统报 "no such column/table" —— 立即补迁移文件，别绕道改代码

## 三之补2、发版到公网链接（2026-09-05 用户确认，温温在 Windows 端开发）

- **统一公网链接（唯一对外地址）**：`http://erp.firmamental.work:3388/`（生产=邢果 Mac，稳定域名；lhr.life / vicp.fun 等动态地址只作应急，不作为日常发版目标）
- 用户预期：Windows 端改完功能 → 走完下面流程 → **用户浏览器刷新 `http://erp.firmamental.work:3388/` 即看到新功能**（代码层）
- Windows 端标准动作（ran 分身执行）：改代码 → 验证（check_code.py + 实测）→ git commit + push → 生成下方「Mac 执行指令」给用户转发（ran 不能直连 Mac Hermes，靠用户转述）
- **给 Mac 的标准发版指令**（用户转发给 Mac 的 xpgx 分身）：

---
cd <代码目录>
git pull origin main
python3 migrate_db.py
ps aux | grep -E "watchdog|app.py" | grep -v grep    # 服务在跑则确认已重启到最新代码; 没自动拉就手动重启 app.py
curl -s http://127.0.0.1:5899/ | head -c 120         # 本机服务正常
---
- 注意：表结构类改动依赖迁移纪律（migrate_db.py 自动应用 migrations/ 下的 .sql）；纯代码改动不需要迁移

## 四、用户上下文（新会话必知）

- 用户是采购系统负责人温丽（admin 账号），非技术背景，专业术语要讲白话
- 协作模式：同事向日葵远程连此 Mac 直接改文件，watchdog 自动重启生效
- 上线节奏：8/27-31 内部试运行 → 9/1-5 领导进场（先演示后操作）
- 待办：8/25 加人（等名单）、8/26 真实物资数据（等库房清单）、红冲含税细节（等领导下午拍板）
- 钉钉：当前接「创新科技公司」，上线前切厂子钉钉（模板/应用重配，用户方案=临时管理员配置后转让领导）

## 五、凭据安全

钉钉 AppSecret、飞书 app_secret、API key 等一律不写入文档/技能/记忆，[REDACTED] 处理。需要时从 .env 或 sys_config 读取。
