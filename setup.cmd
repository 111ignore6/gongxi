@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PAUSE_ON_EXIT=1"
:scan_args
if "%~1"=="" goto args_scanned
if /i "%~1"=="--no-pause" set "PAUSE_ON_EXIT=0"
shift
goto scan_args
:args_scanned
set "RESULT=0"
where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python。请安装 Python 3.11 或更高版本并启用 PATH。
  set "RESULT=1"
  goto finish
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo 需要 Python 3.11 或更高版本。
  set "RESULT=1"
  goto finish
)
if exist ".venv\" goto validate_existing_venv
goto create_venv
:validate_existing_venv
if not exist ".venv\Scripts\python.exe" (
  echo 现有 .venv 不完整，缺少 Scripts\python.exe；未覆盖该目录。请检查后再重试。
  set "RESULT=1"
  goto finish
)
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo 现有 .venv Python 无法运行或低于 3.11；已保留且未覆盖该目录。请检查后再重试。
  set "RESULT=1"
  goto finish
)
goto install_requirements
:create_venv
python -m venv .venv
if errorlevel 1 (
  echo 创建 .venv 失败。
  set "RESULT=1"
  goto finish
)
if not exist ".venv\Scripts\python.exe" (
  echo 创建后未找到 .venv\Scripts\python.exe；请检查 .venv。
  set "RESULT=1"
  goto finish
)
:install_requirements
if exist "requirements-lock.txt" (
  ".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
) else (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 (
  echo 安装失败。请检查 Python、网络和 requirements.txt。
  set "RESULT=1"
  goto finish
)
echo 共席运行环境已安装到 .venv。
:finish
if "%PAUSE_ON_EXIT%"=="1" pause
exit /b %RESULT%
