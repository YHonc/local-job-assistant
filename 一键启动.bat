@echo off
chcp 65001 >nul
setlocal
title Local Job Assistant
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1" %*
set "JOB_ASSISTANT_EXIT=%ERRORLEVEL%"

if not "%JOB_ASSISTANT_EXIT%"=="0" (
  echo.
  echo 启动失败，错误码：%JOB_ASSISTANT_EXIT%
  echo 请查看上方错误信息；按任意键关闭窗口。
  pause >nul
)

exit /b %JOB_ASSISTANT_EXIT%
