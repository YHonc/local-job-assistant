# JOB 平台 · Local Job Assistant

**从发现岗位到跟进面试，在一个工作台里管理求职进度。**

Local Job Assistant 是一个在本机运行的个人求职管理工具，配套 Chrome 浏览器扩展。你可以随手收藏招聘网页，整理岗位信息、记录投递状态，并连接 AI 模型和邮箱，让招聘信息更容易查找和跟进。

[快速开始](#快速开始) · [浏览器扩展](#浏览器扩展) · [开发文档](docs/development.md) · [反馈问题](https://github.com/YHonc/local-job-assistant/issues) · [MIT License](LICENSE)

## 功能亮点

- **随手收集岗位** — 通过 Chrome 扩展保存当前招聘页面，或加入 AI 解析队列，提取公司、岗位、地点与职位描述。
- **集中整理信息** — 在岗位库中搜索、筛选、分类和打标签，切换列表或卡片视图，批量整理与归档。
- **跟进投递进度** — 从待投递到多轮面试，记录每次状态变化，查看历史并撤销操作。
- **整理招聘邮件** — 通过 IMAP 增量同步邮件，筛选、清洗并关联岗位，确认进度建议后更新投递状态。
- **灵活配置 AI** — 管理多个模型、任务路由、提取提示词和代理，查看模型健康状态与调用用量。
- **按需接收提醒** — 为测评、笔试、面试和 Offer 等招聘事件配置桌面或 Push 通知。

工作台包含 **岗位库 · 待整理 · 投递跟进 · 邮件与提醒 · 设置** 五个视图。可以先使用基础岗位管理，再逐步连接模型、邮箱和通知服务。

## 快速开始

准备 **Python 3.11+** 和 **Git**，在终端中下载项目：

```powershell
git clone https://github.com/YHonc/local-job-assistant.git
cd local-job-assistant
```

### Windows 一键启动

双击项目根目录的 **`一键启动.bat`**。首次运行会自动创建虚拟环境、安装依赖、初始化数据库并打开工作台。

启动后访问 **<http://127.0.0.1:8765/workbench?view=library>**，点击“新增岗位”添加第一条记录。

保持启动窗口开启，按 `Ctrl+C` 停止服务。

<details>
<summary><strong>手动安装与启动（PowerShell）</strong></summary>

在项目根目录执行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

默认数据库为 `local_job_assistant.db`，也可以通过环境变量 `DATABASE_URL` 指定存储位置。

</details>

### 开始使用

1. **添加岗位**：手动填写公司和职位信息，或通过浏览器扩展采集。
2. **整理资料**：在“待整理”补充字段、核对分类，再进入岗位库管理。
3. **记录投递**：更新申请状态，在“投递跟进”查看当前进度与历史。

## 浏览器扩展

浏览招聘网站时，可以直接把当前页面保存到工作台。

1. 启动本地工作台，在 Chrome 中打开 `chrome://extensions`。
2. 开启“开发者模式”，点击“加载已解压的扩展程序”，选择项目中的 `extension` 文件夹。
3. 打开招聘页面，点击扩展图标，选择一种收集方式：

| 收集方式 | 适合的场景 |
| --- | --- |
| **保存到待整理** | 先保存来源与正文，稍后补充资料 |
| **加入解析队列** | 使用已配置的 AI 模型提取字段，核对结果后加入岗位库 |

扩展支持后台队列、任务恢复、重复候选提示和批量添加。更新扩展文件后，在扩展管理页点击“重新加载”即可。

更多操作见 [Chrome 扩展使用说明](extension/README.md)。

## 连接模型、邮箱与提醒

在工作台的“设置”中按需启用：

| 集成 | 配置内容 | 用途 |
| --- | --- | --- |
| **AI 模型** | 模型名称、API 地址、密钥与任务路由 | 网页岗位解析、邮件识别与清洗 |
| **招聘邮箱** | IMAP 地址、邮箱账号与授权码 | 增量同步邮件，关联岗位并生成进度建议 |
| **消息提醒** | 通知渠道、目标与策略 | 接收招聘事件提醒，查看发送与重试记录 |

模型通过 LiteLLM / HTTPX 接入；邮箱提供 QQ、163 预设及自定义 IMAP 配置。网页提取提示词支持编辑、保存模板和恢复默认。

应用面向本机个人使用，岗位与邮件资料保存在 SQLite，密钥与授权信息保存在系统凭据存储中。模型解析、邮件同步和 Push 通知使用你配置的外部服务。详见 [数据与隐私说明](docs/privacy.md)。

## 开发

| 部分 | 技术 |
| --- | --- |
| 后端 | Python、FastAPI、SQLAlchemy |
| 数据库 | SQLite、Alembic |
| 前端 | Jinja2、原生 JavaScript ES modules、CSS |
| 浏览器扩展 | Chrome Manifest V3 |

```text
app/          后端模块、页面模板、前端脚本与样式
extension/    Chrome 扩展
migrations/   数据库迁移
scripts/      启动、预览与开发工具
tests/        后端与前端测试
docs/         开发与使用文档
```

安装开发依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

前端使用原生 ES modules，无需打包构建；前端测试使用 Node.js 内置测试运行器。

| 文档 | 内容 |
| --- | --- |
| [总体开发指南](docs/development.md) | 核心链路、数据契约、源码入口、环境与测试命令 |
| [架构说明](ARCHITECTURE.md) | 模块职责、数据流和运行方式 |
| [人工验收清单](docs/manual-qa.md) | 工作台、扩展与外部集成的检查流程 |
| [数据与隐私说明](docs/privacy.md) | 数据存储、外部服务和发布约定 |

## 参与贡献

欢迎提交 [Issue](https://github.com/YHonc/local-job-assistant/issues) 反馈问题或建议，也欢迎通过 Pull Request 改进功能与文档。

反馈问题时，请附上复现步骤、运行环境和相关截图；提交代码前，运行对应模块的测试。开发流程见 [开发指南](docs/development.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。
