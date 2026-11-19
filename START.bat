@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Not installed yet - double-click SETUP.bat first.
  pause
  exit /b 1
)
schtasks /Run /TN MarketplaceSniper >nul 2>&1
if errorlevel 1 start "" "%~dp0.venv\Scripts\pythonw.exe" -m mpwatch.main
echo Marketplace Sniper is running in the background. Log: logs\mpwatch.log
"%SystemRoot%\System32\timeout.exe" /t 4 >nul
