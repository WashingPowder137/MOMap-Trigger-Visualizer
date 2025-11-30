@echo off
setlocal

REM 当前仓库根目录（假设 bat 放在仓库根）
set "ROOT=%~dp0"

REM 在 PowerShell 中切到 ROOT，然后运行 Python 客户端
powershell -NoExit -ExecutionPolicy Bypass ^
  -Command "Set-Location '%ROOT%'; python '.\tools\open_trigger_graphs.py'"

endlocal
