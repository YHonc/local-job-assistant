param(
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
$appUrl = "http://127.0.0.1:8765/"
$healthUrl = "http://127.0.0.1:8765/api/health"

function Write-Step([string]$message) {
    Write-Host "`n==> $message" -ForegroundColor Cyan
}

function Assert-LastExitCode([string]$message) {
    if ($LASTEXITCODE -ne 0) {
        throw "$message（退出码：$LASTEXITCODE）"
    }
}

function Test-AppHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Test-LocalPort {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync("127.0.0.1", 8765)
        if (-not $connection.Wait(700)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Open-AppPage {
    if ($NoBrowser) {
        return
    }
    try {
        $browserStartInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $browserStartInfo.FileName = $appUrl
        $browserStartInfo.UseShellExecute = $true
        [System.Diagnostics.Process]::Start($browserStartInfo) | Out-Null
    }
    catch {
        Write-Warning "浏览器未能自动打开，请手动访问 $appUrl"
    }
}

function New-ProjectVirtualEnvironment {
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        & $pyLauncher.Source -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) {
            & $pyLauncher.Source -3.11 -m venv $venvDirectory
            Assert-LastExitCode "创建 Python 虚拟环境失败"
            return
        }
    }

    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "未找到 Python。请先安装 Python 3.11 或更高版本。"
    }
    & $pythonCommand.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "需要 Python 3.11 或更高版本。"
    }
    & $pythonCommand.Source -m venv $venvDirectory
    Assert-LastExitCode "创建 Python 虚拟环境失败"
}

Set-Location -LiteralPath $projectRoot
Write-Host "Local Job Assistant 一键启动" -ForegroundColor Green
Write-Host "项目目录：$projectRoot"

if (Test-AppHealth) {
    Write-Host "服务已经运行：$appUrl" -ForegroundColor Green
    Open-AppPage
    exit 0
}

if (Test-LocalPort) {
    throw "端口 8765 已被其他程序占用。请释放该端口后重试。"
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Step "首次运行：创建 .venv"
    New-ProjectVirtualEnvironment
}

Write-Step "检查运行依赖"
$dependencyCheck = @'
import sys
modules = [
    'fastapi', 'uvicorn', 'sqlalchemy', 'pydantic', 'alembic', 'jinja2',
    'litellm', 'httpx', 'socksio', 'keyring', 'imap_tools', 'apscheduler', 'apprise', 'app',
]
if sys.platform.startswith('win'):
    modules.append('win32api')
for module in modules:
    __import__(module)
'@
& $venvPython -c $dependencyCheck
if ($LASTEXITCODE -ne 0) {
    Write-Step "安装项目依赖（首次运行可能需要几分钟）"
    & $venvPython -m pip install -e $projectRoot
    Assert-LastExitCode "安装项目依赖失败，请检查网络或 Python 环境"
}

Write-Step "执行数据库迁移"
& $venvPython -m alembic upgrade head
Assert-LastExitCode "数据库迁移失败"

if ($CheckOnly) {
    Write-Host "`n启动前检查通过。" -ForegroundColor Green
    exit 0
}

Write-Step "启动本地服务"
$serverStartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$serverStartInfo.FileName = $venvPython
$serverStartInfo.Arguments = "-m uvicorn app.main:app --host 127.0.0.1 --port 8765"
$serverStartInfo.WorkingDirectory = $projectRoot
$serverStartInfo.UseShellExecute = $false
$serverProcess = [System.Diagnostics.Process]::new()
$serverProcess.StartInfo = $serverStartInfo
if (-not $serverProcess.Start()) {
    throw "无法创建本地服务进程。"
}

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($serverProcess.HasExited) {
            throw "服务启动过程中退出（退出码：$($serverProcess.ExitCode)）。"
        }
        if (Test-AppHealth) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) {
        throw "服务在 15 秒内未通过健康检查。"
    }

    Write-Host "`n启动成功：$appUrl" -ForegroundColor Green
    Write-Host "保持此窗口开启；按 Ctrl+C 可停止服务。"
    Open-AppPage
    $serverProcess.WaitForExit()
    if ($serverProcess.ExitCode -ne 0) {
        throw "服务异常退出（退出码：$($serverProcess.ExitCode)）。"
    }
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        try {
            $serverProcess.Kill()
            $serverProcess.WaitForExit()
        }
        catch {
            Write-Warning "未能自动停止服务进程，请检查端口 8765。"
        }
    }
    if ($null -ne $serverProcess) {
        $serverProcess.Dispose()
    }
}
