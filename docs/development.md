# 开发说明

从 [README](../README.md) 安装开发依赖。后端为 Python，前端是浏览器原生 ES modules，无构建步骤；前端单元测试使用 Node.js 内置测试运行器。

## 本地检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp/pytest-local
node --test (Get-ChildItem -LiteralPath tests -Filter '*.test.cjs').FullName
.\.venv\Scripts\python.exe -m compileall -q app migrations
.\.venv\Scripts\python.exe -m pip check
```

若临时目录已存在或权限异常，请换一个新的 `.tmp/pytest-*` 路径。测试使用临时 SQLite、fake 凭据、mock IMAP/模型/通知；不要把测试数据库地址改成个人数据库。

## 浏览器预览

```powershell
.\.venv\Scripts\python.exe scripts/dev/serve_workbench_preview.py
```

打开 <http://127.0.0.1:18769/workbench?view=library>。预览包含合成岗位、草稿与邮件，并替换外部 I/O；数据库保存在 `.tmp/`。请勿把预览端口与正式服务的 8765 端口混用。退出预览进程不会清理其临时数据库。

`scripts/dev/` 还保留历史浏览器检查脚本，运行前需核对选择器与当前界面是否一致；脚本存在不代表当前版本通过验收。正式集成流程见 [人工验收](manual-qa.md)。

## Git 与发布

```powershell
git config core.hooksPath .githooks
git add app extension migrations scripts tests docs README.md ARCHITECTURE.md LICENSE .gitignore .gitattributes .githooks pyproject.toml
python scripts/check_publication.py --staged
git diff --cached --stat
```

发布检查只读取 Git 中的待提交或已提交对象，不读取本地数据库、系统凭据或浏览器资料。它检查路径白名单、常见密钥格式、个人目录路径和私有数据文件，报告路径和规则名称，不回显匹配内容。规则不能替代人工审阅。

本地 pre-push hook 会检查待推送引用的完整可达历史，防止把旧私有开发分支合入公开分支再上传。新克隆需要自行启用 hook。

个人笔记、提示词 Markdown 和原型保存在 `local/`，数据库及缓存保持其运行位置并由忽略规则保护。不要使用 `git add -f`、`git push --all` 或 `--mirror` 发布本地私有资料；公开内容只从经过检查的分支推送。
