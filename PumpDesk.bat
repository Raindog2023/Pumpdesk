@echo off
cd /d "%~dp0"
title PumpDesk - close this window to stop
if not exist .venv\Scripts\python.exe (
  echo PumpDesk is not installed yet. Run Install-PumpDesk.bat first.
  pause
  exit /b
)
.venv\Scripts\python -m pumpdesk
if errorlevel 1 pause
