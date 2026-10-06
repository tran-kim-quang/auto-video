@echo off
setlocal
set "REPO_ROOT=%~dp0"
set "PYTHON=%REPO_ROOT%.venv\Scripts\python.exe"
set "PYTHONW=%REPO_ROOT%.venv\Scripts\pythonw.exe"
if not exist "%PYTHON%" (
  echo Virtual environment not found. Run setup.bat first.
  exit /b 1
)
if /I "%~1"=="batch" goto :batch
if /I "%~1"=="--batch" goto :batch
if /I "%~1"=="console" goto :console
if /I "%~1"=="--console" goto :console
start "Pyramid Video Workflow" /D "%REPO_ROOT%" "%PYTHONW%" -m video_workflow.ui %*
exit /b 0

:console
cd /D "%REPO_ROOT%"
"%PYTHON%" -m video_workflow.ui %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
exit /b %ERRORLEVEL%

:batch
cd /D "%REPO_ROOT%"
if "%~2"=="" (
  "%PYTHON%" -m video_workflow.batch_cli --source-root "%USERPROFILE%\Documents\gen_video" --assets-dir "%REPO_ROOT%test\logo_and_outro"
  exit /b %ERRORLEVEL%
)
"%PYTHON%" -m video_workflow.batch_cli %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
exit /b %ERRORLEVEL%
