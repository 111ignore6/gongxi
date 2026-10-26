@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\runtime.py start %*
) else (
  python scripts\runtime.py start %*
)
set "RESULT=%ERRORLEVEL%"
set "PAUSE_ON_ERROR=1"
:scan_args
if "%~1"=="" goto args_scanned
if /i "%~1"=="--json" set "PAUSE_ON_ERROR=0"
if /i "%~1"=="--no-browser" set "PAUSE_ON_ERROR=0"
shift
goto scan_args
:args_scanned
if not "%RESULT%"=="0" if "%PAUSE_ON_ERROR%"=="1" pause
exit /b %RESULT%
