@echo off
setlocal
set "REPO_ROOT=%~dp0"
set "PYTHON=%REPO_ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Virtual environment not found. Run setup.bat first.
  exit /b 1
)
"%PYTHON%" "%REPO_ROOT%scripts\build_windows.py"
exit /b %ERRORLEVEL%
