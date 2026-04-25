@echo off
setlocal
cd /d %~dp0

set PYTHON_CMD=
py -3 --version >nul 2>&1
if %errorlevel%==0 set PYTHON_CMD=py -3

if "%PYTHON_CMD%"=="" (
  python --version >nul 2>&1
  if %errorlevel%==0 set PYTHON_CMD=python
)

if "%PYTHON_CMD%"=="" (
  echo Python 3.12+ not found.
  echo Install Python from https://www.python.org/downloads/windows/ and enable "Add Python to PATH".
  pause
  exit /b 1
)

%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto fail
call .venv\Scripts\activate
python -m pip install --upgrade pip
if errorlevel 1 goto fail
pip install -r requirements.txt
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
