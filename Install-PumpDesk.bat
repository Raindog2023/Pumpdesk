@echo off
setlocal
cd /d "%~dp0"
title PumpDesk installer
echo === PumpDesk installer ===
where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python installed. CLOSE this window and double-click Install-PumpDesk.bat again.
  pause
  exit /b
)
set "PY=py -3"
py -3.12 -V >nul 2>nul && set "PY=py -3.12"
echo Creating private Python environment...
if not exist .venv %PY% -m venv .venv || goto :err
.venv\Scripts\python -m pip install --upgrade pip -q
echo Installing packages...
.venv\Scripts\python -m pip install -r requirements.txt -q || goto :err
if not exist .env copy .env.example .env >nul
echo Creating Desktop and Start Menu shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\make-shortcuts.ps1"
echo.
echo Done! Notepad will open .env: paste your Telegram token/chat id (and Helius key), save, close.
echo Then start PumpDesk from the desktop icon.
notepad .env
pause
exit /b
:err
echo.
echo Something failed above. Screenshot this window and send it to your assistant.
pause
