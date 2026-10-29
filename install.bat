@echo off
setlocal
cd /d "%~dp0"
set "LOGIC_SELF=%~f0"
set "LOGIC_TEMPPS=%TEMP%\logic-installer-%RANDOM%-%RANDOM%.ps1"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=$env:LOGIC_SELF; $out=$env:LOGIC_TEMPPS; $raw=[System.IO.File]::ReadAllText($p); $marker='::---PS_START---'; $i=$raw.IndexOf($marker,[System.StringComparison]::Ordinal); if($i -lt 0){exit 90}; $nl=$raw.IndexOf([char]10,$i); if($nl -lt 0){exit 91}; $body=$raw.Substring($nl+1); [System.IO.File]::WriteAllText($out,$body,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 (
    echo [ERROR] Could not extract the installer. Check that this file is intact.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LOGIC_TEMPPS%"
set "RC=%ERRORLEVEL%"
del "%LOGIC_TEMPPS%" >nul 2>&1

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Installer failed with exit code %RC%.
    pause
    exit /b %RC%
)
echo.
echo [LOGIC] Installer finished. Review the messages above before using LOGIC.
pause
exit /b 0

::---PS_START---
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Section([string]$Text) {
    Write-Host ''
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $current = $env:Path
    $env:Path = (@($machine, $user, $current) -join ';')
    $extras = @(
        "$env:ProgramFiles\nodejs",
        "$env:APPDATA\npm",
        "$env:LOCALAPPDATA\Programs\Python\Python313",
        "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts",
        "$env:ProgramFiles\Python313",
        "$env:ProgramFiles\Python313\Scripts"
    )
    foreach ($p in $extras) {
        if ($p -and (Test-Path $p) -and (($env:Path -split ';') -notcontains $p)) {
            $env:Path += ";$p"
        }
    }
}
function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Add-UserPath([string]$Directory) {
    if (-not $Directory -or -not (Test-Path $Directory)) { return }
    $existing = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($existing -split ';' | Where-Object { $_ })
    if (-not ($parts | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') })) {
        $newPath = (@($parts) + $Directory) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Write-Host "Added to your user PATH: $Directory" -ForegroundColor Green
    }
}
function Invoke-WingetInstall([string]$Id, [string]$Name) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "winget is unavailable. Install $Name manually, then run this installer again."
    }
    Write-Host "Installing $Name using winget..."
    & $winget.Source install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    $code = $LASTEXITCODE
    Refresh-Path
    if ($code -ne 0) {
        Write-Host "winget returned exit code $code; checking whether $Name is nevertheless installed..." -ForegroundColor Yellow
    }
}
function Find-Python313 {
    Refresh-Path
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.13 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,13) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $py.Source; Prefix = @('-3.13') }
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,13) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $python.Source; Prefix = @() }
        }
    }
    return $null
}
function Set-IniValue([string]$Content, [string]$Key, [string]$Value) {
    $pattern = '(?im)^\s*' + [regex]::Escape($Key) + '\s*=.*$'
    if ([regex]::IsMatch($Content, $pattern)) {
        return [regex]::Replace($Content, $pattern, "$Key=$Value")
    }
    if ($Content -notmatch '(?im)^\s*\[Everything\]\s*$') {
        $Content = $Content.TrimEnd() + "`r`n`r`n[Everything]`r`n"
    }
    $section = [regex]::Match($Content, '(?im)^\s*\[Everything\]\s*$')
    if (-not $section.Success) { return $Content.TrimEnd() + "`r`n$Key=$Value`r`n" }
    $nextSection = [regex]::Match($Content.Substring($section.Index + $section.Length), '(?im)^\s*\[.+\]\s*$')
    if ($nextSection.Success) {
        $insertAt = $section.Index + $section.Length + $nextSection.Index
        return $Content.Insert($insertAt, "$Key=$Value`r`n")
    }
    return $Content.TrimEnd() + "`r`n$Key=$Value`r`n"
}

try {
    $scriptPath = $env:LOGIC_SELF
    if (-not $scriptPath) { throw 'Could not determine the installer file path.' }
    $scriptDir = Split-Path -Parent $scriptPath
    $requirements = Join-Path $scriptDir 'requirements.txt'
    $dashboard = Join-Path $scriptDir 'setup_dashboard.html'
    $tui = Join-Path $scriptDir 'logic_tui.py'

    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "requirements.txt was not found beside the installer: $requirements"
    }
    if (-not (Test-Path -LiteralPath $tui -PathType Leaf)) {
        throw "logic_tui.py was not found beside the installer: $tui"
    }

    # Elevate the extracted PowerShell script itself. Wait for it to finish so
    # the BAT wrapper does not delete the temporary script before it runs.
    if (-not (Test-IsAdmin)) {
        Write-Host 'Requesting administrator privileges through Windows UAC...' -ForegroundColor Yellow
        $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        $child = Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList $args -Verb RunAs -Wait -PassThru
        exit $child.ExitCode
    }

    Write-Host 'LOGIC setup: checking prerequisites and installing dependencies.' -ForegroundColor Cyan
    Write-Host "LOGIC folder: $scriptDir"

    Write-Section '1/7 Python 3.13'
    $pythonInfo = Find-Python313
    if (-not $pythonInfo) {
        Invoke-WingetInstall -Id 'Python.Python.3.13' -Name 'Python 3.13'
        $pythonInfo = Find-Python313
    }
    if (-not $pythonInfo) {
        throw 'Python 3.13 is still unavailable. Install Python 3.13 from https://www.python.org/downloads/ and enable the Python launcher, then rerun this installer.'
    }
    $pythonExe = $pythonInfo.Exe
    $pythonPrefix = @($pythonInfo.Prefix)
    & $pythonExe @pythonPrefix --version
    if ($LASTEXITCODE -ne 0) { throw 'Python version check failed.' }

    Write-Section '2/7 Node.js LTS and npm'
    Refresh-Path
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Invoke-WingetInstall -Id 'OpenJS.NodeJS.LTS' -Name 'Node.js LTS'
    }
    Refresh-Path
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'npm is unavailable after installing Node.js. Restart Windows if PATH has not refreshed, then rerun the installer.' }
    & $npm.Source --version
    if ($LASTEXITCODE -ne 0) { throw 'npm version check failed.' }

    Write-Section '3/7 Python virtual environment and dependencies'
    $venv = Join-Path $scriptDir '.venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        & $pythonExe @pythonPrefix -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
    }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip in the LOGIC virtual environment.' }
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Python requirements installation failed. Review the pip error above.' }
    & $venvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium installation failed.' }
    Write-Host 'Python dependencies and Playwright Chromium installed.' -ForegroundColor Green

    Write-Section '4/7 9Router'
    Refresh-Path
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    & $npm.Source install --global 9router
    if ($LASTEXITCODE -ne 0) { throw 'Installing 9Router failed. Review the npm error above.' }
    $npmPrefix = (& $npm.Source config get prefix | Select-Object -Last 1).Trim()
    if ($npmPrefix -and (Test-Path $npmPrefix)) { Add-UserPath $npmPrefix }
    Refresh-Path
    $router = Get-Command 9router.cmd -ErrorAction SilentlyContinue
    if (-not $router) {
        $router = Get-Command 9router -ErrorAction SilentlyContinue
    }
    if (-not $router) {
        Write-Host '9Router installed, but its command was not found in this session. Open a new terminal after setup.' -ForegroundColor Yellow
    } else {
        Write-Host "9Router command found: $($router.Source)" -ForegroundColor Green
    }

    Write-Section '5/7 Everything search'
    $everythingCandidates = @(
        (Join-Path $env:ProgramFiles 'Everything\Everything.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Everything\Everything.exe')
    ) | Where-Object { $_ -and (Test-Path $_) }
    $everythingExe = $everythingCandidates | Select-Object -First 1
    if (-not $everythingExe) {
        Invoke-WingetInstall -Id 'voidtools.Everything' -Name 'Everything search'
        $everythingCandidates = @(
            (Join-Path $env:ProgramFiles 'Everything\Everything.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Everything\Everything.exe')
        ) | Where-Object { $_ -and (Test-Path $_) }
        $everythingExe = $everythingCandidates | Select-Object -First 1
    }
    if (-not $everythingExe) {
        throw 'Everything was not found after installation. Install it from https://www.voidtools.com/ and rerun this installer.'
    }

    Write-Section '6/7 Configure Everything HTTP server'
    # The HTTP server can expose filenames/search results to devices on the
    # network. Enable it only because the original LOGIC setup requires it.
    # Do not forward its port on your router or expose it to the public internet.
    $appdataFolder = Join-Path $env:APPDATA 'Everything'
    $appdataIni = Join-Path $appdataFolder 'Everything.ini'
    $installIni = Join-Path (Split-Path -Parent $everythingExe) 'Everything.ini'
    $ini = $null
    if (Test-Path $appdataIni) { $ini = $appdataIni }
    elseif (Test-Path $installIni) { $ini = $installIni }
    else {
        New-Item -ItemType Directory -Path $appdataFolder -Force | Out-Null
        $ini = $appdataIni
        [System.IO.File]::WriteAllText($ini, "[Everything]`r`n", [System.Text.Encoding]::UTF8)
    }
    $content = Get-Content -LiteralPath $ini -Raw
    if ($null -eq $content) { $content = '' }
    $content = Set-IniValue -Content $content -Key 'allow_http_server' -Value '1'
    $content = Set-IniValue -Content $content -Key 'http_server_port' -Value '80'
    [System.IO.File]::WriteAllText($ini, $content, [System.Text.Encoding]::UTF8)
    Write-Host "Configured Everything HTTP settings in: $ini" -ForegroundColor Green

    Stop-Process -Name 'Everything' -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700
    Start-Process -FilePath $everythingExe
    Write-Host 'Everything restarted. Confirm its HTTP server is enabled in Everything options if your version stores settings elsewhere.' -ForegroundColor Yellow

    Write-Section '7/7 Install the logic command'
    $binDir = Join-Path $env:LOCALAPPDATA 'LOGIC\bin'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $shim = Join-Path $binDir 'logic.cmd'
    $shimContent = "@echo off`r`nsetlocal`r`ncd /d `"$scriptDir`"`r`n`"$venvPython`" `"$tui`" %*`r`nexit /b %errorlevel%`r`n"
    [System.IO.File]::WriteAllText($shim, $shimContent, [System.Text.Encoding]::ASCII)
    Add-UserPath $binDir

    Write-Section 'Setup dashboard'
    if (Test-Path -LiteralPath $dashboard) {
        Start-Process -FilePath $dashboard
    } else {
        Write-Host "Dashboard not found (optional): $dashboard" -ForegroundColor Yellow
    }

    Write-Host ''
    Write-Host 'INSTALLATION COMPLETED' -ForegroundColor Green
    Write-Host "Python environment: $venv"
    Write-Host "Launcher: $shim"
    Write-Host 'Open a NEW Command Prompt or PowerShell window after setup.'
    Write-Host 'Run: 9router'
    Write-Host 'Then open http://localhost:20128 and finish the provider/model/API-token setup.'
    Write-Host 'Finally run: logic'
    Write-Host ''
    Write-Host 'Security note: Everything HTTP on port 80 is not intended for public internet exposure.' -ForegroundColor Yellow
    exit 0
}
catch {
    Write-Host ''
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No success status was reported. Fix the error above and rerun the installer.' -ForegroundColor Yellow
    exit 1
}
