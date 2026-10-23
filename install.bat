@echo off
:: Single-file Hybrid Launcher & Installer for LOGIC
setlocal EnableDelayedExpansion

:: Check admin rights and elevate if needed
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [LOGIC] Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c ""%~f0""' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
title LOGIC Environment Setup

powershell -NoProfile -ExecutionPolicy Bypass -Command "[ScriptBlock]::Create((Get-Content -LiteralPath '%~f0' -Raw -Encoding UTF8) -replace '^@echo off[\s\S]*?::---PS_START---[\r\n]+','').Invoke()"

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Installation failed or encountered an error.
    pause
    exit /b %errorlevel%
)

echo.
echo [LOGIC] All the required softwares have been automatically installed.
echo [LOGIC] If you encountered errors, read the setup_dashboard.html instructions to complete the setup.
pause
exit /b

::---PS_START---
<#
    install.ps1
    LOGIC — full dependency installer and bootstrap.

    What it does:
      - Self-elevates via UAC
      - Checks/installs Python 3.13 and Node.js if missing (via winget, with manual fallback)
      - Global pip install of requirements.txt
      - Installs Playwright Chromium
      - Installs 9Router (npm global)
      - Detects/installs Everything, enables HTTP server, restarts it
      - Drops a `logic` command on PATH that cd's into the repo and launches the TUI
      - Opens setup_dashboard.html and prints next steps

    Requires: requirements.txt and setup_dashboard.html sitting next to this script.
#>

# ============================================================
# 1. SELF-ELEVATE
# ============================================================
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Requesting administrator privileges..." -ForegroundColor Yellow
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ErrorActionPreference = "Stop"
Write-Host "=== LOGIC Installer ===" -ForegroundColor Cyan
Write-Host ""

$ScriptDir       = $PSScriptRoot
$RequirementsTxt = Join-Path $ScriptDir "requirements.txt"
$DashboardHtml   = Join-Path $ScriptDir "setup_dashboard.html"

if (-not (Test-Path $RequirementsTxt)) {
    Write-Host "ERROR: requirements.txt not found next to install.ps1." -ForegroundColor Red
    exit 1
}

# ============================================================
# HELPER: ensure a command exists, install via winget if not
# ============================================================
function Ensure-Command {
    param(
        [string]$Command,
        [string]$WingetId,
        [string]$ManualUrl,
        [string]$FriendlyName
    )

    if (Get-Command $Command -ErrorAction SilentlyContinue) {
        Write-Host "  $FriendlyName found." -ForegroundColor Green
        return $true
    }

    Write-Host "  $FriendlyName not found." -ForegroundColor Yellow

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "  winget unavailable. Install manually:" -ForegroundColor Yellow
        Write-Host "    $ManualUrl" -ForegroundColor Yellow
        return $false
    }

    Write-Host "  Installing $FriendlyName via winget..."
    winget install -e --id $WingetId --silent --accept-package-agreements --accept-source-agreements
    
    # Aggressively refresh PATH from Machine, User, and default locations
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path","Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("Path","User")
    $extraPaths  = @(
        "C:\Program Files\nodejs",
        "$env:APPDATA\npm",
        "C:\Program Files\Python313",
        "C:\Program Files\Python313\Scripts",
        "$env:LOCALAPPDATA\Programs\Python\Python313",
        "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts"
    )
    $env:Path = ($machinePath + ";" + $userPath + ";" + ($extraPaths -join ";"))
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

# ============================================================
# 2. PYTHON 3.13
# ============================================================
Write-Host "[1/7] Checking Python..." -ForegroundColor Cyan
if (-not (Ensure-Command -Command "python" -WingetId "Python.Python.3.13" -ManualUrl "https://www.python.org/downloads/release/python-31312/" -FriendlyName "Python 3.13.12")) {
    Write-Host "  ERROR: Python unavailable. Install from python.org (check 'Add python.exe to PATH')." -ForegroundColor Red
    exit 1
}
Write-Host "  Python: $((python --version) 2>&1)" -ForegroundColor Green

# ============================================================
# 3. NODE.JS / NPM
# ============================================================
Write-Host ""
Write-Host "[2/7] Checking Node.js..." -ForegroundColor Cyan
if (-not (Ensure-Command -Command "npm" -WingetId "OpenJS.NodeJS.LTS" -ManualUrl "https://nodejs.org/" -FriendlyName "Node.js (LTS)")) {
    Write-Host "  ERROR: Node.js/npm unavailable. Install from nodejs.org." -ForegroundColor Red
    exit 1
}
Write-Host "  Node: $((node --version) 2>&1)" -ForegroundColor Green

# ============================================================
# 4. GLOBAL PIP INSTALL
# ============================================================
Write-Host ""
Write-Host "[3/7] Installing Python dependencies globally..." -ForegroundColor Cyan

python -m pip install --upgrade pip
python -m pip install -r $RequirementsTxt

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: pip install failed." -ForegroundColor Red
    exit 1
}

Write-Host "  Installing Playwright Chromium..."
python -m playwright install chromium

Write-Host "  Python dependencies installed." -ForegroundColor Green

# ============================================================
# 5. INSTALL 9ROUTER
# ============================================================
Write-Host ""
Write-Host "[4/7] Installing 9Router..." -ForegroundColor Cyan

$npmResult = npm install -g 9router 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  9Router installed." -ForegroundColor Green
} else {
    Write-Host "  9Router install failed:" -ForegroundColor Red
    Write-Host "  $npmResult" -ForegroundColor Red
    exit 1
}

# ============================================================
# 6. EVERYTHING (detect, install if missing)
# ============================================================
Write-Host ""
Write-Host "[5/7] Checking Everything..." -ForegroundColor Cyan

$EverythingCandidates = @(
    "C:\Program Files\Everything",
    "C:\Program Files (x86)\Everything"
)

$EverythingInstallDir = $null
foreach ($candidate in $EverythingCandidates) {
    if (Test-Path (Join-Path $candidate "Everything.exe")) {
        $EverythingInstallDir = $candidate
        break
    }
}

if ($EverythingInstallDir) {
    Write-Host "  Everything already installed at $EverythingInstallDir" -ForegroundColor Green
    Write-Host "  Skipping install. Will ensure HTTP server is enabled." -ForegroundColor Gray
} else {
    Write-Host "  Everything not found. Installing..." -ForegroundColor Yellow

    $EverythingSetupUrl   = "https://www.voidtools.com/Everything-1.4.1.1026.x64-Setup.exe"
    $EverythingSetupPath  = "$env:TEMP\Everything-Setup.exe"
    $EverythingInstallDir = "C:\Program Files\Everything"

    Write-Host "  Downloading..."
    Invoke-WebRequest -Uri $EverythingSetupUrl -OutFile $EverythingSetupPath -UseBasicParsing

    Write-Host "  Installing silently..."
    $installArgs = "/S -install-options `"-app-data -install-service -install-run-on-system-startup -uninstall-desktop-shortcut -uninstall-quick-launch-shortcut -disable-update-notification`" /D=$EverythingInstallDir"
    $proc = Start-Process -FilePath $EverythingSetupPath -ArgumentList $installArgs -Wait -PassThru
    if ($proc.ExitCode -eq 0) {
        Write-Host "  Everything installed." -ForegroundColor Green
    } else {
        Write-Host "  Everything installer exited with code $($proc.ExitCode)." -ForegroundColor Yellow
    }

    Remove-Item $EverythingSetupPath -Force -ErrorAction SilentlyContinue
}

# ============================================================
# 7. ENABLE EVERYTHING HTTP SERVER + RESTART
# ============================================================
Write-Host ""
Write-Host "[6/7] Enabling Everything HTTP server..." -ForegroundColor Cyan

# Look in %APPDATA% first (standard if -app-data was used), then install folder
$appdataFolder = Join-Path $env:APPDATA "Everything"
$appdataIni    = Join-Path $appdataFolder "Everything.ini"
$installIni    = Join-Path $EverythingInstallDir "Everything.ini"

Stop-Process -Name "Everything" -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

if (Test-Path $appdataIni) {
    $EverythingIni = $appdataIni
} elseif (Test-Path $installIni) {
    $EverythingIni = $installIni
} else {
    # Brand new clean machine: create the Everything folder and initial INI with HTTP enabled
    if (-not (Test-Path $appdataFolder)) {
        New-Item -ItemType Directory -Path $appdataFolder -Force | Out-Null
    }
    $EverythingIni = $appdataIni
    Set-Content -Path $EverythingIni -Value "[Everything]`r`nallow_http_server=1`r`nhttp_server_port=80" -Encoding UTF8
    Write-Host "  Created initial Everything.ini with HTTP server enabled." -ForegroundColor Green
}

if (Test-Path $EverythingIni) {
    $iniContent = Get-Content $EverythingIni -Raw
    if ($iniContent -match 'allow_http_server=0') {
        $iniContent = $iniContent -replace 'allow_http_server=0', 'allow_http_server=1'
        Set-Content -Path $EverythingIni -Value $iniContent -Encoding UTF8
        Write-Host "  allow_http_server updated to 1 ($EverythingIni)." -ForegroundColor Green
    } elseif ($iniContent -match 'allow_http_server=1') {
        Write-Host "  allow_http_server verified ($EverythingIni)." -ForegroundColor Green
    } else {
        Add-Content -Path $EverythingIni -Value "`r`nallow_http_server=1"
        Write-Host "  allow_http_server appended to INI." -ForegroundColor Green
    }
}

$EverythingExe = Join-Path $EverythingInstallDir "Everything.exe"
if (Test-Path $EverythingExe) {
    Start-Process -FilePath $EverythingExe -ErrorAction SilentlyContinue
    Write-Host "  Everything restarted." -ForegroundColor Green
}

# ============================================================
# 8. INSTALL 'logic' PATH SHIM
# ============================================================
Write-Host ""
Write-Host "[7/7] Installing 'logic' command..." -ForegroundColor Cyan

$ShimDir  = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
$ShimPath = Join-Path $ShimDir "logic.cmd"
$TuiPath  = Join-Path $ScriptDir "logic_tui.py"

if (-not (Test-Path $ShimDir)) {
    New-Item -ItemType Directory -Path $ShimDir -Force | Out-Null
}

if (-not (Test-Path $TuiPath)) {
    Write-Host "  WARNING: logic_tui.py not found at $TuiPath" -ForegroundColor Yellow
}

# The shim cd's into the repo folder before launching.
# This makes CWD-relative paths in logic.py (logic_tools, screenshot.wav, etc.)
# work no matter where the user runs 'logic' from.
$shimContent = @"
@echo off
cd /d "$ScriptDir"
python "$TuiPath" %*
"@

Set-Content -Path $ShimPath -Value $shimContent -Encoding ASCII

Write-Host "  Installed: $ShimPath" -ForegroundColor Green

# ============================================================
# 9. LAUNCH SETUP DASHBOARD
# ============================================================
Write-Host ""
Write-Host "Opening setup dashboard..." -ForegroundColor Cyan

if (Test-Path $DashboardHtml) {
    Start-Process $DashboardHtml
} else {
    Write-Host "  WARNING: setup_dashboard.html not found." -ForegroundColor Yellow
    Write-Host "  Expected at: $DashboardHtml" -ForegroundColor Yellow
}

# ============================================================
# 10. SUMMARY
# ============================================================
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " INSTALLATION COMPLETE" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""

Write-Host " What the installer just did:" -ForegroundColor White
Write-Host "   [x] Installed Python 3.13 (if missing)" -ForegroundColor Gray
Write-Host "   [x] Installed Node.js + npm (if missing)" -ForegroundColor Gray
Write-Host "   [x] Installed all Python dependencies globally" -ForegroundColor Gray
Write-Host "   [x] Installed Playwright Chromium browser" -ForegroundColor Gray
Write-Host "   [x] Installed 9Router (npm global)" -ForegroundColor Gray
Write-Host "   [x] Installed / detected Everything" -ForegroundColor Gray
Write-Host "   [x] Enabled Everything's HTTP server" -ForegroundColor Gray
Write-Host "   [x] Registered the 'logic' command on PATH" -ForegroundColor Gray
Write-Host ""

Write-Host " What YOU need to do now (in order):" -ForegroundColor Yellow
Write-Host ""
Write-Host "   1. The setup dashboard should have opened in your browser." -ForegroundColor White
Write-Host "      If not, open setup_dashboard.html manually." -ForegroundColor Gray
Write-Host "        - Pick your LOGIC folder" -ForegroundColor Gray
Write-Host "        - Fill in your API keys" -ForegroundColor Gray
Write-Host "        - Click 'Ping All' to verify services" -ForegroundColor Gray
Write-Host "        - Click 'Save / Export .env'" -ForegroundColor Gray
Write-Host ""
Write-Host "   2. Open a NEW Command Prompt window and run:" -ForegroundColor White
Write-Host "        9router" -ForegroundColor Cyan
Write-Host "      This starts the 9Router gateway." -ForegroundColor Gray
Write-Host ""
Write-Host "   3. Open this link in your browser:" -ForegroundColor White
Write-Host "        http://localhost:20128" -ForegroundColor Cyan
Write-Host "      In the 9Router dashboard:" -ForegroundColor Gray
Write-Host "        - Go to Providers, add one (Google AI Studio is free)" -ForegroundColor Gray
Write-Host "        - Paste the API key the dashboard told you about" -ForegroundColor Gray
Write-Host "        - Go to Models, copy the model alias you want" -ForegroundColor Gray
Write-Host "        - Go to API Tokens, create one, paste into the dashboard" -ForegroundColor Gray
Write-Host ""
Write-Host "   4. Launch LOGIC from any terminal:" -ForegroundColor White
Write-Host "        logic" -ForegroundColor Cyan
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
