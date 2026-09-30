@echo off
setlocal
set "REPO_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%scripts\setup.ps1" %*
exit /b %ERRORLEVEL%
