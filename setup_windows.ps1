# Marketplace Sniper - Windows setup. Double-click SETUP.bat (it runs this).
# Safe to run again: it updates everything and keeps your settings.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "Marketplace Sniper setup"

function Say($msg, $color = "White") { Write-Host $msg -ForegroundColor $color }
function Fail($msg) { Say "`n$msg" Red; Read-Host "`nPress Enter to close"; exit 1 }
function Yes($question, $default = $true) {
    $hint = if ($default) { "(Y/n)" } else { "(y/N)" }
    $a = Read-Host "$question $hint"
    if (-not $a) { return $default }
    return $a.Trim().ToLower().StartsWith("y")
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

Say "=================================================================" Cyan
Say " MARKETPLACE SNIPER - setup (takes about 5 minutes)" Cyan
Say "=================================================================" Cyan

# --- 1. Python 3.10+ -------------------------------------------------------
function Find-Python {
    foreach ($cand in @("py", "python")) {
        if (Get-Command $cand -ErrorAction SilentlyContinue) {
            try { $v = & $cand -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { $v = $null }
            if ($v -and [version]$v -ge [version]"3.10") { return $cand }
        }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Say "`nPython isn't installed. Installing it now..." Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 --exact --source winget --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $py = Find-Python
    }
    if (-not $py) {
        Fail "Couldn't install Python automatically.`nInstall it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run SETUP.bat again."
    }
}
Say "`n[1/6] Python: $(& $py --version)" Green

# Stop a running copy if this is a re-install
Stop-ScheduledTask -TaskName "MarketplaceSniper" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*mpwatch.main*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# --- 2. Packages and browser ------------------------------------------------
Say "`n[2/6] Installing the app's packages..."
if (-not (Test-Path .venv)) { & $py -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip -q
& $vpy -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) { Fail "Installing packages failed - check your internet connection and run SETUP.bat again." }
Say "      done" Green
Say "`n[3/6] Downloading the browser it uses to look at Marketplace..."
& $vpy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Say "      Browser download failed - it will use your installed Chrome or Edge instead." Yellow }
else { Say "      done" Green }

# --- 3. Your settings --------------------------------------------------------
Say "`n[4/6] Your settings" Cyan
& $vpy setup_wizard.py
if ($LASTEXITCODE -ne 0) { Fail "Setup was not finished. Run SETUP.bat again when you're ready." }

# --- 4. Optional free photo check (Ollama) ----------------------------------
Say "`n[5/6] Optional: free AI photo check" Cyan
Say "      A small AI model on this computer looks at each deal's photo and warns you about"
Say "      catalogue pictures or the wrong item. Free and private, but a ~5 GB download, and"
Say "      it works best with 16 GB of memory. You can skip it - everything else still works."
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (Test-Path $ollama) {
    Say "      Ollama is already installed." Green
    & $ollama pull gemma3:4b
} elseif (Yes "      Install the photo check?" $false) {
    winget install --id Ollama.Ollama --exact --source winget --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
    Start-Sleep 5
    if (Test-Path $ollama) {
        Say "      Downloading the model (3.4 GB)..."
        & $ollama pull gemma3:4b
    } else { Say "      Couldn't install Ollama - skipping. Everything else works without it." Yellow }
} else { Say "      Skipped." }

# --- 5. Run in the background, start with Windows ---------------------------
Say "`n[6/6] Starting it in the background..." Cyan
$pyw = "$PSScriptRoot\.venv\Scripts\pythonw.exe"
$registered = $false
try {
    $action    = New-ScheduledTaskAction -Execute $pyw -Argument "-m mpwatch.main" -WorkingDirectory $PSScriptRoot
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                 -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
                 -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "MarketplaceSniper" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName "MarketplaceSniper"
    $registered = $true
} catch {
    # No permission for scheduled tasks: use the Startup folder instead
    $startup = [Environment]::GetFolderPath("Startup")
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$startup\Marketplace Sniper.lnk")
    $lnk.TargetPath = $pyw; $lnk.Arguments = "-m mpwatch.main"; $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.Save()
    Start-Process -FilePath $pyw -ArgumentList "-m", "mpwatch.main" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
Say "      Running, and it will start by itself whenever you log in." Green

if (Yes "`n      Keep this computer awake while it's plugged in (so it never misses a deal)?" $true) {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    if (Yes "      Keep running with the lid closed too?" $false) {
        powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
        powercfg /setactive SCHEME_CURRENT
    }
}

Say "`n=================================================================" Green
Say " All done!" Green
Say " In a minute your bot will message you. Then just type what you"
Say " want to it, for example:   xbox series s   or   iphone 13 under 5000"
Say ""
Say " Stop it:      double-click STOP.bat"
Say " Start again:  double-click START.bat"
Say " Change setup: double-click CONFIGURE.bat"
Say " Problems:     open the logs folder, file mpwatch.log"
Say "=================================================================" Green
Read-Host "`nPress Enter to close"
