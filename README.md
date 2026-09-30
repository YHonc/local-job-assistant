# Local Job Assistant · 岗位工作台

把散落在招聘网站、浏览器标签页和邮箱里的求职信息，整理成一个清楚、好用的个人工作台。

从收藏岗位、整理资料，到记录投递和跟进面试，Local Job Assistant 帮你把每一步连起来。应用在本机运行，使用 SQLite 保存数据，可按需接入 AI 模型、招聘邮箱和消息提醒。

## 能做什么

### 收集岗位，随时整理

在招聘网页中点击 Chrome 扩展，即可提取公司、岗位、地点和职位描述，核对后加入岗位库。也可以先保存到“待整理”，之后再补充资料。

### 建立自己的岗位库

集中管理公司与岗位，通过关键词、职位类别、地点、招聘类型和投递状态查找机会。支持标签、添加时间、列表与卡片视图，以及批量整理和归档。

### 跟进每一次投递

直接在岗位列表中更新投递状态，查看变更记录，或撤销一次操作。工作台提供以下快捷状态：

`待投递` · `已投递` · `待面试` · `1面` · `2面` · `3面` · `HR面` · `拒绝`

### 整理招聘邮件与提醒

连接 IMAP 邮箱后，增量同步招聘邮件，按规则筛选，再通过模型识别重要信息、关联岗位并生成进度建议。测评、笔试、面试和 Offer 等消息可以配合桌面或 Push 提醒查看。

### 按自己的习惯使用

管理多个 AI 模型，查看调用用量，配置任务分配、健康检查和网络代理。通过显示偏好调整主题、信息展示和交互行为。

## 快速开始

准备好 **Python 3.11+** 和 **Git**。推荐在 Windows 下使用一键启动。

### 1. 下载项目

```powershell
git clone https://github.com/YHonc/local-job-assistant.git
cd local-job-assistant
```

### 2. 启动工作台

双击项目根目录的 **`一键启动.bat`**。首次启动会自动创建 Python 环境、安装依赖、初始化数据库，并打开浏览器。

工作台地址：<http://127.0.0.1:8765/workbench?view=library>

保持启动窗口开启即可使用，按 `Ctrl+C` 停止服务。更新项目后，重新启动即可加载新版本。

<details>
<summary>手动安装与启动</summary>

在项目目录运行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

默认数据库文件为项目目录中的 `local_job_assistant.db`。

</details>

### 3. 添加第一个岗位

点击岗位库右上角的 **“新增岗位”**，填写公司与岗位信息后保存。模型、邮箱和提醒可以在需要时再配置。

## 用浏览器扩展收集岗位

1. 在工作台「设置」中添加 AI 模型，填写模型名称、API 地址和密钥。
2. 在 Chrome 打开 `chrome://extensions`，开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择本项目的 `extension` 文件夹。
4. 打开招聘页面，点击扩展，将当前页加入解析队列。
5. 核对提取结果，确认添加；也可以选择“保存到待整理”，稍后处理。

支持 OpenAI 兼容、Anthropic 兼容和 Gemini 原生接口，并通过 LiteLLM 接入其他 Provider。扩展支持后台解析队列、多任务恢复和批量添加，详见 [扩展使用说明](extension/README.md)。

## 连接邮箱与通知

在「设置」中添加邮箱账户，填写 IMAP 信息与邮箱授权码，设置同步时间范围和筛选规则。支持 QQ、163 预设及自定义 IMAP 邮箱。

同步后的信息可以在 **“邮件与提醒”** 中查看、整理和关联。按需配置桌面通知或 Push 服务，及时跟进有时间要求的招聘安排。

## 技术与开发

后端使用 **FastAPI + SQLAlchemy + SQLite**，数据库迁移使用 **Alembic**；前端使用 **Jinja2 + 原生 JavaScript**，浏览器扩展采用 **Manifest V3**。

```text
app/           后端、模板、前端脚本与样式
extension/     Chrome 扩展
migrations/    Alembic 数据库迁移
scripts/       启动与开发工具
tests/         后端与前端测试
docs/          项目文档
```

欢迎通过 Issue 反馈使用体验和功能建议，也欢迎提交 Pull Request。

- [开发说明](docs/development.md)：环境配置、测试与开发预览
- [架构说明](ARCHITECTURE.md)：模块职责与数据流
- [人工验收](docs/manual-qa.md)：主要功能检查流程
- [数据与隐私说明](docs/privacy.md)：数据存储与外部服务

## 开源协议

本项目采用 [MIT License](LICENSE)。
