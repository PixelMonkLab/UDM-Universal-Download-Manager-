@echo off
title UDM
cd /d "%~dp0"
echo.
echo === UDM ===

rem Already running? Just open it.
curl -s -m 2 http://127.0.0.1:5757/api/ping 2>nul | find "UDM" >nul && (echo UDM is already running. & start "" "http://127.0.0.1:5757" & exit /b 0)

rem 1. Find Python 3.10+ (prefer the "py" launcher; skip the Microsoft Store stub)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/windows/
  echo IMPORTANT: on the first install screen tick "Add python.exe to PATH".
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)

rem 2. Create the private environment (first time only)
if not exist ".venv\Scripts\python.exe" (
  echo Setting up - first time only, about a minute...
  %PY% -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
)

rem 3. Install / update packages
echo Checking for updates...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -q --upgrade -r requirements.txt || (echo Installing failed. Check your internet and try again. & pause & exit /b 1)

rem 4. Run
".venv\Scripts\python.exe" app.py
pause
