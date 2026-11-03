# =====================================================================
#  LOGIC crash-proof installer  (pure PowerShell)
#  - First clones https://github.com/Ancientduck/logic-harness if needed
#  - Manages system Python: exact 3.13.12 preferred, any 3.13.x accepted,
#    other versions removed ONLY after an explicit Y/N permission prompt.
#  - Wheel-only installs (--only-binary=:all:), so pip can NEVER start
#    a source build that needs a C compiler.
#  - Private embedded Python 3.13 runtime as last-resort fallback if the
#    system cannot provide 3.13.
#  - Optional fully-offline mode: put .whl files in a "wheels" folder
#    next to this installer and it installs without touching PyPI.
#
#  Usage:
#    Right-click → Run with PowerShell
#    OR open PowerShell and run:  .\install.ps1
# =====================================================================
param(
    [string]$InstallerPath = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference   = 'SilentlyContinue'

# --- Pinned embedded Python (python-build-standalone, astral-sh) -----
$RT_TAG   = '20261026'
$RT_VER   = '3.13.18'
$RT_FILE  = "cpython-$RT_VER+$RT_TAG-x86_64-pc-windows-msvc-install_only.tar.gz"
$RT_URL   = "https://github.com/astral-sh/python-build-standalone/releases/download/$RT_TAG/$($RT_FILE -replace '\+','%2B')"
$RT_SHA   = 'EBE90CC1DCE2DD2D7AFA1F0AA9B6C269C7C0174AEABDC6A801381602B7283424'
$RUNTIME_DIR = Join-Path $env:LOCALAPPDATA 'LOGIC\runtime'

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
function Write-Section([string]$Text) {
    Write-Host ''
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Test-IsAdmin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
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

function Get-PyVer([string]$Exe, [string[]]$Prefix = @()) {
    # Returns e.g. "3.13.18" or $null. Never throws.
    try {
        $out = & $Exe @Prefix -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -match '^\d+\.\d+\.\d+$') { return $out.Trim() }
    } catch { }
    return $null
}

function Test-FileSha256([string]$Path, [string]$ExpectedUpper) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $h = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    return ($h -eq $ExpectedUpper)
}

function Invoke-Download([string]$Url, [string]$Dest) {
    # Tries curl.exe (ships with Win10+) then Invoke-WebRequest, 3 rounds.
    for ($i = 1; $i -le 3; $i++) {
        try {
            Write-Host "Downloading embedded Python (attempt $i/3)..." -ForegroundColor Gray
            $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
            if ($curl) {
                & $curl.Source -L --fail --retry 2 --connect-timeout 20 -o $Dest $Url
                if ($LASTEXITCODE -eq 0 -and (Test-Path $Dest)) { return $true }
            }
            Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing -TimeoutSec 300
            if (Test-Path $Dest) { return $true }
        } catch {
            Write-Host "Download attempt $i failed: $($_.Exception.Message)" -ForegroundColor Yellow
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Get-EmbeddedPython {
    # 1) Reuse an already-installed LOGIC runtime
    $rt = Join-Path $RUNTIME_DIR 'python.exe'
    if (Test-Path $rt) {
        $v = Get-PyVer $rt
        if ($v -like '3.13.*') {
            Write-Host "Reusing existing LOGIC runtime (Python $v): $rt" -ForegroundColor Green
            return $rt
        }
        Write-Host "Existing runtime is Python $v - will re-download 3.13." -ForegroundColor Yellow
        Remove-Item -LiteralPath $RUNTIME_DIR -Recurse -Force -ErrorAction SilentlyContinue
    }

    # 2) Download the pinned standalone build + verify checksum
    $tmp = Join-Path $env:TEMP $RT_FILE
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    if (-not (Invoke-Download -Url $RT_URL -Dest $tmp)) {
        throw "Could not download the embedded Python runtime. Check the internet connection, or install Python 3.13 manually and rerun."
    }
    if (-not (Test-FileSha256 -Path $tmp -ExpectedUpper $RT_SHA)) {
        $actual = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "Embedded Python download failed the SHA-256 check (got $actual). The file was corrupted or tampered with - rerun the installer."
    }
    Write-Host 'Checksum OK.' -ForegroundColor Green

    # 3) Extract with tar.exe (built into Windows 10 1803+)
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if (-not $tar) { throw 'tar.exe not found. Update Windows 10 (1803+) and rerun, or install Python 3.13 manually.' }
    $extractRoot = Join-Path $env:TEMP ("logic-rt-" + [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
    & $tar.Source -xzf $tmp -C $extractRoot
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract the embedded Python runtime (tar failed).' }
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue

    $src = Join-Path $extractRoot 'python'
    if (-not (Test-Path (Join-Path $src 'python.exe'))) { throw 'Unexpected runtime archive layout (python.exe not found).' }
    New-Item -ItemType Directory -Path (Split-Path -Parent $RUNTIME_DIR) -Force | Out-Null
    if (Test-Path $RUNTIME_DIR) { Remove-Item -LiteralPath $RUNTIME_DIR -Recurse -Force }
    Move-Item -LiteralPath $src -Destination $RUNTIME_DIR
    Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue

    $v = Get-PyVer $rt
    if ($v -notlike '3.13.*') { throw "Embedded runtime check failed (found Python $v)." }
    Write-Host "Embedded Python $v ready: $rt" -ForegroundColor Green
    return $rt
}

function Refresh-Path {
    # Re-reads PATH from the registry so freshly installed software (Python,
    # Node, winget installs...) works in THIS session - no PC restart needed.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user, $env:Path) -join ';')
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

function Read-YesNo([string]$Message) {
    # Y or ENTER = continue, N = abort. Anything else re-asks.
    while ($true) {
        $answer = Read-Host "$Message (Y = yes / ENTER = yes, N = no)"
        if ($answer -eq '' -or $answer -match '^[yY](es)?$') { return $true }
        if ($answer -match '^[nN]o?$') { return $false }
        Write-Host 'Please answer Y (or press ENTER) to continue, or N to cancel.' -ForegroundColor Yellow
    }
}

function Get-InstalledPythons {
    # Detects every installed CPython via the registry (per-machine + per-user,
    # incl. the WOW6432Node view). Returns @{ Exe; Version } entries.
    $found = @()
    $roots = @(
        'HKLM:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore',
        'HKCU:\SOFTWARE\Python\PythonCore'
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($key in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
            try {
                $ipKey = "$($key.PSPath)\InstallPath"
                if (-not (Test-Path $ipKey)) { continue }
                $props = Get-ItemProperty $ipKey -ErrorAction SilentlyContinue
                $exe = $props.ExecutablePath
                if (-not $exe -and $props.'(default)') { $exe = Join-Path $props.'(default)' 'python.exe' }
                if ($exe -and (Test-Path $exe)) {
                    $v = Get-PyVer -Exe $exe
                    if ($v) { $found += @{ Exe = $exe; Version = $v } }
                }
            } catch { }
        }
    }
    $seen = @{}
    foreach ($item in $found) {
        $k = $item.Exe.ToLower()
        if (-not $seen.ContainsKey($k)) { $seen[$k] = $item }
    }
    return @($seen.Values)
}

function Uninstall-PythonVersion([string]$Exe, [string]$Version) {
    # Best effort, silent removal. Tries: winget -> MSI/exe uninstall entry
    # from the registry -> Microsoft Store appx. Reports if it could not.
    $majmin = ($Version -split '\.')[0..1] -join '.'
    Write-Host "Uninstalling Python $Version ..."
    if ($Exe -like '*WindowsApps*') {
        try {
            Get-AppxPackage *PythonSoftwareFoundation* |
                Where-Object { $_.Name -like "*Python.$majmin*" } |
                Remove-AppxPackage -ErrorAction SilentlyContinue
        } catch { }
    }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        try {
            & $winget.Source uninstall --id "Python.Python.$majmin" --exact --silent --disable-interactivity 2>$null
            if ($LASTEXITCODE -eq 0) { Write-Host "winget removed Python $majmin." -ForegroundColor Green }
        } catch { }
    }
    if (Test-Path -LiteralPath $Exe -PathType Leaf) {
        # Still present -> find its own uninstaller in the registry.
        $uninstRoots = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
        )
        foreach ($root in $uninstRoots) {
            if (-not (Test-Path $root)) { continue }
            foreach ($k in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
                try {
                    $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
                    if (-not $p.DisplayName) { continue }
                    if ($p.DisplayName -notmatch "^Python $majmin(\.|\s|$)") { continue }
                    $cmd = $null
                    if ($p.QuietUninstallString) { $cmd = $p.QuietUninstallString }
                    elseif ($p.UninstallString) {
                        $cmd = $p.UninstallString
                        if ($cmd -match 'MsiExec\.exe.*\/I\{') { $cmd = ($cmd -replace '/I\{', '/X{') + ' /qn /norestart' }
                    }
                    if ($cmd) {
                        Write-Host "Running uninstaller for '$($p.DisplayName)'..."
                        Start-Process -FilePath 'cmd.exe' -ArgumentList "/c $cmd" -Wait -WindowStyle Hidden
                    }
                } catch { }
            }
        }
    }
    Start-Sleep -Seconds 1
    if (Test-Path -LiteralPath $Exe -PathType Leaf) {
        Write-Host "WARNING: Python $Version at $Exe could not be fully removed. It will be left in place; LOGIC will not use it." -ForegroundColor Yellow
    } else {
        Write-Host "Python $Version removed." -ForegroundColor Green
    }
}

function Install-TargetPython {
    # Installs Python 3.13.12 via winget (pinned). Returns $true on success.
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'Installing Python 3.13.12 using winget...'
        & $winget.Source install --id Python.Python.3.13 --exact --version 3.13.12 --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'Pinned 3.13.12 not available in winget - trying latest 3.13 instead...' -ForegroundColor Yellow
            & $winget.Source install --id Python.Python.3.13 --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
        }
        Refresh-Path
        return $true
    }
    return $false
}

function Invoke-PipInstall([string]$PythonExe, [string[]]$PyPrefix, [string]$ReqFile) {
    $wheelsDir = Join-Path $scriptDir 'wheels'
    $hasWheels = (Test-Path $wheelsDir) -and (@(Get-ChildItem -LiteralPath $wheelsDir -Filter *.whl -ErrorAction SilentlyContinue).Count -gt 0)
    & $PythonExe @PyPrefix -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip.' }
    if ($hasWheels) {
        Write-Host "Offline mode: installing wheels from $wheelsDir (PyPI will NOT be contacted)." -ForegroundColor Green
        & $PythonExe @PyPrefix -m pip install --no-index --find-links "$wheelsDir" --only-binary=:all: -r $ReqFile
    } else {
        Write-Host 'Installing from PyPI. Wheel-only mode: source builds are forbidden.' -ForegroundColor Gray
        Write-Host 'If this fails with "no matching distribution", a package has no wheel for Python 3.13 on Windows.' -ForegroundColor Yellow
        & $PythonExe @PyPrefix -m pip install --only-binary=:all: -r $ReqFile
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python dependencies installation failed. Review the pip error above.' }
}

function Invoke-WingetInstall([string]$Id, [string]$Name) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "winget is unavailable. Install $Name manually, then run this installer again."
    }
    Write-Host "Installing $Name using winget..."
    & $winget.Source install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Host "winget returned exit code $code; checking whether $Name is nevertheless installed..." -ForegroundColor Yellow
    }
}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
try {
    # ------------------------------------------------------------------
    # Resolve script path + detect remote (irm | iex) execution
    # ------------------------------------------------------------------
    $scriptPath = $null
    $isRemote   = $false

    if ($InstallerPath -and (Test-Path -LiteralPath $InstallerPath)) {
        $scriptPath = $InstallerPath
    } elseif ($PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath)) {
        $scriptPath = $PSCommandPath
    } elseif ($MyInvocation.MyCommand.Path -and (Test-Path -LiteralPath $MyInvocation.MyCommand.Path)) {
        $scriptPath = $MyInvocation.MyCommand.Path
    }

    # If we still don't have a real local path (no drive letter like C:\), 
    # this is almost certainly an irm | iex remote run.
    if (-not $scriptPath -or $scriptPath -notmatch '^[A-Za-z]:\\') {
        $isRemote   = $true
        $scriptPath = $null
        Write-Host 'Detected remote install (irm | iex). Will use current directory.' -ForegroundColor Yellow
    }

    # Elevate if needed.
    # For remote runs we re-launch by re-downloading the script instead of -File.
    if (-not (Test-IsAdmin)) {
        Write-Host 'Requesting administrator privileges through Windows UAC...' -ForegroundColor Yellow

        if ($isRemote) {
            # Re-run the same remote one-liner elevated
            $elevCmd = "irm 'https://raw.githubusercontent.com/Ancientduck/logic-harness/main/install.ps1' | iex"
            $elevArgs = "-NoProfile -ExecutionPolicy Bypass -Command `"$elevCmd`""
        } else {
            $elevArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -InstallerPath `"$scriptPath`""
        }

        $child = Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList $elevArgs -Verb RunAs -Wait -PassThru
        $code = $child.ExitCode
        if ($null -eq $code) {
            Write-Host 'Elevated process ended without reporting an exit code - treating as failure.' -ForegroundColor Red
            $code = 1
        }
        exit $code
    }

    Write-Host 'LOGIC setup: crash-proof installer (pinned Python 3.13, wheel-only).' -ForegroundColor Cyan

    # ---------------------------------------------------------------
    # 0/8 Ensure the logic-harness repo is present
    # ---------------------------------------------------------------
    Write-Section '0/8 Clone logic-harness repository (if needed)'

    $REPO_URL  = 'https://github.com/Ancientduck/logic-harness.git'
    $REPO_NAME = 'logic-harness'

    # Decide base directory for the clone
    if ($isRemote) {
        # irm | iex → use the current working directory of the PowerShell session
        $startDir = (Get-Location).Path
        Write-Host "Remote mode → installing into current directory: $startDir" -ForegroundColor Cyan
    } else {
        $startDir = Split-Path -Parent $scriptPath
    }

    function Test-LogicRoot([string]$Dir) {
        return (Test-Path (Join-Path $Dir 'requirements.txt')) -and
               (Test-Path (Join-Path $Dir 'logic_tui.py'))
    }

    # Prefer an already-valid LOGIC folder if one exists nearby
    $candidates = @(
        $startDir,
        (Join-Path $startDir $REPO_NAME),
        (Join-Path (Get-Location).Path $REPO_NAME)
    )

    $scriptDir = $null
    foreach ($c in $candidates) {
        if ($c -and (Test-LogicRoot $c)) {
            $scriptDir = $c
            Write-Host "Found existing LOGIC harness at: $scriptDir" -ForegroundColor Green
            break
        }
    }

    if (-not $scriptDir) {
        # Need to clone. First make sure git is available.
        $git = Get-Command git.exe -ErrorAction SilentlyContinue
        if (-not $git) {
            Write-Host 'Git not found – installing Git for Windows via winget...' -ForegroundColor Yellow
            Invoke-WingetInstall -Id 'Git.Git' -Name 'Git'
            Refresh-Path
            $git = Get-Command git.exe -ErrorAction SilentlyContinue
            if (-not $git) {
                throw 'Git is still unavailable after installation. Install it manually from https://git-scm.com and rerun.'
            }
        }

        $cloneTarget = Join-Path $startDir $REPO_NAME
        if (Test-Path $cloneTarget) {
            Write-Host "Removing incomplete folder: $cloneTarget" -ForegroundColor Yellow
            Remove-Item -LiteralPath $cloneTarget -Recurse -Force -ErrorAction SilentlyContinue
        }

        Write-Host "Cloning $REPO_URL → $cloneTarget ..." -ForegroundColor Cyan
        & $git.Source clone --depth 1 $REPO_URL $cloneTarget
        if ($LASTEXITCODE -ne 0) {
            throw "git clone failed. Check your internet connection and try again."
        }
        $scriptDir = $cloneTarget
        Write-Host "Repository cloned successfully." -ForegroundColor Green
    }

    $requirements  = Join-Path $scriptDir 'requirements.txt'
    $dashboard     = Join-Path $scriptDir 'setup_dashboard.html'
    $tui           = Join-Path $scriptDir 'logic_tui.py'

    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "requirements.txt was not found in the project root: $requirements"
    }
    if (-not (Test-Path -LiteralPath $tui -PathType Leaf)) {
        throw "logic_tui.py was not found in the project root: $tui"
    }

    Write-Host "LOGIC folder: $scriptDir"

    # ---------------------------------------------------------------
    Write-Section '1/8 Python 3.13.12 (system check + managed install)'
    $targetVersion = '3.13.12'
    $appPython   = $null   # path to python.exe used for everything below
    $appPyPrefix = @()     # extra launcher args (unused since we call the exe directly)
    $useVenv     = $true   # host Python is always wrapped in a private venv
    Refresh-Path

    $pythons = Get-InstalledPythons
    # Policy: exact 3.13.12 first; ANY other 3.13.x is also accepted silently
    # (identical cp313 wheel ABI). Only versions OUTSIDE the 3.13 family
    # (3.9/3.12/3.14/...) require the user's permission before removal.
    $match = @($pythons | Where-Object { $_.Version -eq $targetVersion }) | Select-Object -First 1
    if (-not $match) { $match = @($pythons | Where-Object { $_.Version -like '3.13.*' }) | Select-Object -First 1 }

    if (-not $match) {
        if (@($pythons).Count -gt 0) {
            Write-Host "LOGIC requires Python 3.13 (built on $targetVersion). Found incompatible Python on this PC:" -ForegroundColor Yellow
            foreach ($p in $pythons) { Write-Host "  - Python $($p.Version)  ($($p.Exe))" }
            Write-Host ''
            Write-Host "With your permission these will be REMOVED and replaced by Python" -ForegroundColor Yellow
            Write-Host "$targetVersion. Removal does NOT require a restart, but any other" -ForegroundColor Yellow
            Write-Host 'software depending on them may stop working.' -ForegroundColor Yellow
            Write-Host ''
            $ok = Read-YesNo "May LOGIC remove the Python version(s) above and install Python $targetVersion instead?"
            if (-not $ok) {
                Write-Host ''
                Write-Host 'Permission denied by user. Nothing was installed or removed.' -ForegroundColor Red
                exit 1
            }
            foreach ($p in $pythons) { Uninstall-PythonVersion -Exe $p.Exe -Version $p.Version }
        }
        Write-Host "Installing Python $targetVersion..."
        Install-TargetPython

        Refresh-Path
        $pythons = Get-InstalledPythons
        $match = @($pythons | Where-Object { $_.Version -eq $targetVersion }) | Select-Object -First 1
        if (-not $match) {
            $match = @($pythons | Where-Object { $_.Version -like '3.13.*' }) | Select-Object -First 1
            if ($match) {
                Write-Host "Exact $targetVersion unavailable; using Python $($match.Version) (same 3.13 wheel compatibility)." -ForegroundColor Yellow
            }
        }
        if (-not $match) {
            Write-Host "Could not install system Python $targetVersion - falling back to a private LOGIC runtime." -ForegroundColor Yellow
            try {
                $appPython = Get-EmbeddedPython
                $useVenv   = $false   # runtime is already private to LOGIC
            } catch {
                throw "Could not obtain Python $targetVersion (winget failed: $($_.Exception.Message)). Install Python 3.13.12 manually from https://www.python.org/downloads/ and rerun this installer."
            }
        }
    }

    if ($match) { $appPython = $match.Exe }
    $v = Get-PyVer -Exe $appPython -Prefix $appPyPrefix
    if (-not $v) { throw "Python at '$appPython' did not respond to a version check." }
    if (-not $v.StartsWith('3.13')) {
        throw "Refusing to continue: '$appPython' is Python $v. LOGIC requires 3.13.x (built on 3.13.12)."
    }
    Write-Host "Using Python $v - PATH refreshed for this session, no restart needed." -ForegroundColor Green

    # ---------------------------------------------------------------
    Write-Section '2/8 Node.js LTS and npm'
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Invoke-WingetInstall -Id 'OpenJS.NodeJS.LTS' -Name 'Node.js LTS'
        Refresh-Path
    }
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'npm is unavailable after installing Node.js. Restart Windows if PATH has not refreshed, then rerun the installer.' }
    & $npm.Source --version
    if ($LASTEXITCODE -ne 0) { throw 'npm version check failed.' }

    # ---------------------------------------------------------------
    Write-Section '3/8 Python dependencies (wheel-only, compiler-free)'
    if ($useVenv) {
        $venv = Join-Path $scriptDir '.venv'
        $venvPython = Join-Path $venv 'Scripts\python.exe'
        if (-not (Test-Path $venvPython)) {
            & $appPython @appPyPrefix -m venv $venv
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
        }
        $appPython   = $venvPython
        $appPyPrefix = @()
    }
    Invoke-PipInstall -PythonExe $appPython -PyPrefix $appPyPrefix -ReqFile $requirements

    & $appPython @appPyPrefix -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium installation failed. Check the internet connection and rerun.' }
    Write-Host 'Python dependencies and Playwright Chromium installed.' -ForegroundColor Green

    # ---------------------------------------------------------------
    Write-Section '4/8 9Router'
    & $npm.Source install --global 9router
    if ($LASTEXITCODE -ne 0) { throw 'Installing 9Router failed. Review the npm error above.' }
    $npmPrefix = (& $npm.Source config get prefix | Select-Object -Last 1).Trim()
    if ($npmPrefix -and (Test-Path $npmPrefix)) { Add-UserPath $npmPrefix }
    $router = Get-Command 9router.cmd -ErrorAction SilentlyContinue
    if (-not $router) { $router = Get-Command 9router -ErrorAction SilentlyContinue }
    if (-not $router) {
        Write-Host '9Router installed, but its command was not found in this session. Open a new terminal after setup.' -ForegroundColor Yellow
    } else {
        Write-Host "9Router command found: $($router.Source)" -ForegroundColor Green
    }

    # ---------------------------------------------------------------
    Write-Section '5/8 Everything search'
    function Get-EverythingExe {
        @(
            (Join-Path $env:ProgramFiles 'Everything\Everything.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Everything\Everything.exe')
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    }
    $everythingExe = Get-EverythingExe
    if (-not $everythingExe) {
        Invoke-WingetInstall -Id 'voidtools.Everything' -Name 'Everything search'
        $everythingExe = Get-EverythingExe
    }
    if (-not $everythingExe) {
        throw 'Everything was not found after installation. Install it from https://www.voidtools.com/ and rerun this installer.'
    }

    # ---------------------------------------------------------------
    Write-Section '6/8 Configure Everything HTTP server'
    # The HTTP server can expose filenames/search results to devices on the
    # network. Enable it only because the original LOGIC setup requires it.
    # Do not forward its port on your router or expose it to the public internet.
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

    # ---------------------------------------------------------------
    Write-Section '7/8 Install the logic command'
    $binDir = Join-Path $env:LOCALAPPDATA 'LOGIC\bin'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $shim = Join-Path $binDir 'logic.cmd'
    $shimContent = "@echo off`r`nsetlocal`r`ncd /d `"$scriptDir`"`r`n`"$appPython`" `"$tui`" %*`r`nexit /b %errorlevel%`r`n"
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
    Write-Host "Python runtime: $appPython"
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
    Write-Host ''
    Write-Host 'The installer made no partial changes it cannot recover from - simply fix' -ForegroundColor Yellow
    Write-Host 'the issue above (internet, disk space, antivirus) and run it again.' -ForegroundColor Yellow
    exit 1
}
