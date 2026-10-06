@echo off
setlocal
set "REPO_ROOT=%~dp0"
where powershell.exe >nul 2>&1
if errorlevel 1 (
	echo PowerShell was not found. Run this script on Windows PowerShell.
	exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%scripts\setup.ps1" %*
exit /b %ERRORLEVEL%
