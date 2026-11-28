@echo off
title Build UDM.exe
cd /d "%~dp0"
echo.
echo === Building the UDM desktop app (takes 5-15 minutes) ===
echo.

rem Find Python 3.10+ (only needed on THIS computer, to build the app once)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.10 or newer is needed to BUILD the app ^(not to use it^).
  echo Install it from https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH".
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)

if not exist ".buildenv\Scripts\python.exe" (
  echo Setting up build tools...
  %PY% -m venv .buildenv || (echo Could not create the build environment. & pause & exit /b 1)
)
echo Installing parts...
".buildenv\Scripts\python.exe" -m pip install -q --upgrade pip >nul 2>nul
".buildenv\Scripts\python.exe" -m pip install -q --upgrade -r requirements.txt -r requirements-desktop.txt nuitka pyinstaller || (echo Installing failed. Check your internet. & pause & exit /b 1)

echo Building...
".buildenv\Scripts\python.exe" tools\build_native.py || (echo Build failed - see the messages above. & pause & exit /b 1)

echo Making a zip you can share...
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\UDM' -DestinationPath 'UDM-Windows.zip'"

echo.
echo ============================================================
echo  Done!
echo  Your app:   dist\UDM\UDM.exe   (double-click it)
echo  To share:   send UDM-Windows.zip
echo              (keep the whole UDM folder together - the
echo               .exe needs the _internal folder next to it)
echo ============================================================
explorer "dist\UDM"
pause
