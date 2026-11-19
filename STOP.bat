@echo off
schtasks /End /TN MarketplaceSniper >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object { $_.CommandLine -like '*mpwatch.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Stopped. Double-click START.bat to start again.
"%SystemRoot%\System32\timeout.exe" /t 4 >nul
