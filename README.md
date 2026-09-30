# Local Job Assistant · 岗位工作台

本地优先的个人求职管理工具。把招聘网页、岗位资料、投递进度和招聘邮件整理在一个工作台中。

基于 FastAPI、SQLite、原生 JavaScript 和 Chrome Manifest V3 扩展。无需 Docker、Redis 或前端构建步骤。

## 功能

- **岗位库**：公司与岗位管理、搜索、多条件筛选、类别标签、添加时间、列表/卡片切换、批量整理与归档。
- **投递跟进**：快捷更新状态、状态历史、撤销；可配置查看详情或点击投递链接后的自动记录行为。
- **网页导入**：扩展读取用户选择的招聘页面，模型提取结构化资料，经确认后入库；也可先存草稿再整理。
- **招聘邮件**：IMAP 增量同步、规则预筛、模型识别、岗位关联与待确认进度建议。
- **提醒与设置**：桌面/Push 通知、多模型与用量、自动健康检查、按模型使用 HTTP/SOCKS 代理、显示偏好。

工作台提供待投递、已投递、待面试、1面、2面、3面、HR面、拒绝八种快捷状态；旧入口和历史记录仍保留兼容状态。点击“去投递”只记录点击行为，不代表已在招聘网站成功提交申请。

## 快速开始

需要 Python 3.11+；Windows 为主要使用环境。Node.js 仅用于开发时运行前端测试。

```powershell
git clone https://github.com/YHonc/local-job-assistant.git
cd local-job-assistant
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765/workbench?view=library>。Windows 也可双击根目录 `一键启动.bat`，脚本会准备环境、执行迁移并打开浏览器。升级代码后先停止旧服务，再重新启动。

默认数据库是当前工作目录的 `local_job_assistant.db`。如需更换，在迁移和启动前同时设置 `DATABASE_URL`，例如 `sqlite:///./data/jobs.db`，并先创建 `data` 目录。迁移会写入数据库，升级前请停止服务并自行备份。

应用面向本机单用户使用，没有面向公网的登录鉴权。保持监听 `127.0.0.1`，使用一个 Uvicorn 进程；不要添加多个 `--workers`。邮箱、通知与模型健康调度运行在应用进程内。

## 配置模型与扩展

1. 进入工作台「设置」，添加模型、API 地址与密钥。模型测试会调用相应服务，可能产生费用。
2. 在 Chrome 打开 `chrome://extensions`，开启开发者模式，加载本项目的 `extension/` 目录。
3. 在招聘页点击扩展，核对提取结果后确认添加。修改扩展源码后需重新加载。
4. 如需代理，先在 AI 模型设置中保存地址，例如 `http://127.0.0.1:7890`，再为具体模型开启代理。代理失败不会自动回退直连。

支持 OpenAI 兼容、Anthropic 兼容和 Gemini 原生模型接口；其他 Provider 通过 LiteLLM 适配，能力依供应商而异。邮箱同步需要 IMAP 授权码；通知按需配置。详见 [扩展说明](extension/README.md)。

## 数据与隐私

业务数据保存在本地 SQLite；API Key、邮箱授权码和 Push 地址通过操作系统 keyring 保存。**本地优先不等于完全离线**：开启模型提取或邮件识别时，相应文本会发给配置的模型服务；启用 Push 时通知内容会发给配置的服务。

仓库不包含个人岗位库、邮件、数据库、凭据、截图或个人提示词 Markdown。新克隆的项目从空数据库开始。`.gitignore` 默认忽略 Markdown，仅明确放行公开说明文档；个人提示词、开发计划和交接笔记放在 `local/` 下。内置通用解析逻辑属于应用源码，仍随项目提供。

详情见 [隐私与发布边界](docs/privacy.md)。

## 目录与开发

```text
app/           后端、模板、前端脚本与样式
extension/     Chrome 扩展
migrations/    Alembic 数据库迁移
scripts/       启动、开发预览和发布检查
tests/         使用合成数据与 fake 服务的测试
docs/          公开开发、验收和隐私说明
local/         私人资料与历史原型（本地保留，Git 忽略）
```

开发流程见 [开发说明](docs/development.md)，模块关系见 [架构说明](ARCHITECTURE.md)，实际集成检查见 [人工验收](docs/manual-qa.md)。

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp/pytest-local
node --test (Get-ChildItem -LiteralPath tests -Filter '*.test.cjs').FullName
python scripts/check_publication.py --staged
```

提交代码前启用本地推送检查：`git config core.hooksPath .githooks`。新增公开 Markdown 需要同时更新忽略规则和发布检查脚本白名单，并审阅正文。

## 开源协议

项目采用 [MIT License](LICENSE)。第三方依赖遵循各自的许可证；招聘网页、邮件与用户导入数据不属于本项目授权的源码。
