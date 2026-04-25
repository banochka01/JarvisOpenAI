@echo off
setlocal
cd /d %~dp0

set PYTHON_CMD=
py -3.12 --version >nul 2>&1
if %errorlevel%==0 set PYTHON_CMD=py -3.12

if "%PYTHON_CMD%"=="" (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
  if %errorlevel%==0 set PYTHON_CMD=python
)

if "%PYTHON_CMD%"=="" (
  echo Python 3.12 not found.
  echo This project is pinned to Python 3.12 on Windows.
  echo Python 3.14 can make pip build Rust packages from source and fail.
  echo Install Python 3.12 from https://www.python.org/downloads/release/python-3128/
  echo During install enable "Add Python to PATH" and install the py launcher.
  pause
  exit /b 1
)

%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto fail
call .venv\Scripts\activate
python -m pip install --upgrade pip
if errorlevel 1 goto fail
python -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto fail
if not exist .env copy .env.example .env
echo.
echo Installed. Edit .env, then run run_windows.bat
pause
exit /b 0

:fail
echo.
echo Install failed. Check the error above.
pause
exit /b 1
