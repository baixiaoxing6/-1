@echo off
setlocal
cd /d %~dp0

if not exist assets mkdir assets
if not exist logs mkdir logs

python --version
if errorlevel 1 (
  echo Python is not available. Please install Python 3.11+ and add it to PATH.
  pause
  exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  pause
  exit /b 1
)

pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name LocalScreenCam ^
  --add-data "config.json;." ^
  --add-data "assets;assets" ^
  --add-data "logs;logs" ^
  --hidden-import pyvirtualcam ^
  --hidden-import pyvirtualcam.camera ^
  --hidden-import pyvirtualcam.backends.obs ^
  --hidden-import pyvirtualcam.backends.unitycapture ^
  --hidden-import win32gui ^
  --hidden-import win32con ^
  --hidden-import win32api ^
  main.py

if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Build complete: dist\LocalScreenCam\LocalScreenCam.exe
echo Copy the whole dist\LocalScreenCam folder to the target machine.
echo OBS Studio Virtual Camera or Unity Capture must be installed on the target machine.
pause
