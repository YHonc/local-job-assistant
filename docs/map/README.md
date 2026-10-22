# 项目地图：生成、发布与更新

在线入口：<https://yhonc.github.io/local-job-assistant/map/>。

## 内容与证据边界

本地图依据 JOB 平台已有源码、公开 README、架构文档和开发指南生成，包含 Chrome 扩展、迁移及测试。个人采集 skill、批次处理、真实数据库、邮件、凭据和导出不参与分析。

`.ua/knowledge-graph.json` 是原始图谱；`.ua/.understandignore` 记录范围；`.ua/meta.json` 记录分析元数据。静态展示副本在本目录。文件、类和函数可定位到源码，但不代表动态调用追踪；`tested_by` 表示测试关联，不等于测试运行成功。

## 工具来源

- 上游：<https://github.com/Egonex-AI/Understand-Anything>
- 版本：2.9.7；固定修订：`b8c807c632743ab695ec43cb654a0f1efe216bc8`。
- 本项目本地安装位置：`.agents/skills/understand/`、`.agents/skills/understand-dashboard/`。
- 本地工具源码：`.tools/understand-anything/understand-anything-plugin/`，不随产品提交。

本次采用技能的扫描、导入解析、批次划分、结构提取、语义分析、分层、导览、图结构校验及指纹流程。主代理负责脚本执行和文件写入，只读分析代理提供语义材料。中文输出，自动提交更新关闭。

## 更新图谱

在安装了技能的本地编码客户端中，按 `.ua/.understandignore` 范围运行 `understand --full --language zh --no-auto-update`。核心业务改动后重新分析并审阅关系；不要仅更新日期或提交号冒充地图已更新。

新克隆不自带运行时。使用技能安装器将上游 `understand-anything-plugin/skills/understand` 与 `understand-dashboard` 安装到项目 `.agents/skills/`；克隆上述固定工具修订到 `.tools/understand-anything/`。调用脚本时设置 `PLUGIN_ROOT` 为工具的 `understand-anything-plugin` 目录，避免复制安装后的相对路径解析错误。

## 构建静态页面

需要 Python 3.11+、Node.js 22+ 和 pnpm 10+，执行：

```powershell
python scripts/build_project_map.py
python scripts/validate_project_map.py
python -m http.server 18770 --bind 127.0.0.1 --directory docs
```

然后访问 `http://127.0.0.1:18770/map/`。构建脚本使用固定上游修订、安装锁定依赖、构建官方 dashboard 的静态模式，并复制图谱和许可文件；不启动 JOB 平台、不读取业务数据库、不调用模型。构建静态页面不会重新分析源码，必须先完成图谱更新。

GitHub Pages 使用 `main` 分支的 `/docs` 目录。更新后将图谱、静态页面、构建与校验工具一并提交；README 的在线链接指向 `/map/`。仓库内 HTML 文件预览不会直接运行应用，交互展示由 Pages 提供。

## 静态页面限制与许可

公开页面支持图谱浏览、搜索、分层与导览，不需要 JOB 平台后端。官方静态模式不提供本机源码预览及本地操作；查看源码请使用 GitHub，或启动本地 `understand-dashboard`。

图谱是公开源码的静态分析，时间戳、来源提交和源码指纹用于识别范围，不能证明运行时行为或部署状态。页面只包含审核后的图谱和工具资源，不能把私有源码或数据放进图谱再发布。

保留 JOB 平台的 [MIT License](../../LICENSE) 和本目录的 [第三方声明](THIRD_PARTY_NOTICES.md)。第三方库仍适用各自许可，不以项目 MIT 声明替代依赖声明。
