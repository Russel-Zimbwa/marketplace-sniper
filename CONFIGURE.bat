@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Not installed yet - double-click SETUP.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" setup_wizard.py
if errorlevel 1 (pause & exit /b 1)
call "%~dp0STOP.bat" >nul
call "%~dp0START.bat"
