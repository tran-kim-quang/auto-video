@echo off
setlocal
set "REPO_ROOT=%~dp0"
set "PYTHONW=%REPO_ROOT%.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW%" (
  echo Virtual environment not found. Run setup.bat first.
  exit /b 1
)
start "Pyramid Video Workflow" /D "%REPO_ROOT%" "%PYTHONW%" -m video_workflow.ui
exit /b 0
