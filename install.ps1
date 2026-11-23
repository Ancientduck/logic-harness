# =====================================================================
#  LOGIC crash-proof installer  (pure PowerShell)
#  - Installs LOGIC on a normal drive: the first fixed, non-USB drive that
#    is not the Windows drive (D:, E:, ...) if one exists, otherwise under
#    your user profile. You can type any other folder. Never System32.
#  - First clones https://github.com/Ancientduck/logic-harness if needed
#  - Uses its OWN private, pinned Python 3.13 (python-build-standalone,
#    SHA-256 verified), stored inside the LOGIC folder. Any Python already
#    on the PC (old OR brand new) is never touched, so it can never break
#    LOGIC's dependencies.
#  - Wheel-only installs (--only-binary=:all:), so pip can NEVER start
#    a source build that needs a C compiler.
#  - Prefers requirements.lock (exact pinned versions) over requirements.txt
#  - Installs Everything + its es.exe command-line tool (same folder, on PATH)
#  - Python packages can be installed fully offline: put .whl files in a
#    "wheels" folder inside the LOGIC folder (Chromium/npm/winget still
#    need internet).
#
#  Usage:
#    Right-click -> Run with PowerShell
#    OR open PowerShell and run:  .\install.ps1
#    OR choose the folder yourself:  .\install.ps1 -InstallDir D:\logic-harness
# =====================================================================
param(
    [string]$InstallerPath = '',
    [string]$InstallDir    = '',
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'
$ProgressPreference   = 'SilentlyContinue'

# --- Pinned embedded Python (python-build-standalone, astral-sh) -----
$RT_TAG   = '20261026'
$RT_VER   = '3.13.18'
$RT_FILE  = "cpython-$RT_VER+$RT_TAG-x86_64-pc-windows-msvc-install_only.tar.gz"
$RT_URL   = "https://github.com/astral-sh/python-build-standalone/releases/download/$RT_TAG/$($RT_FILE -replace '\+','%2B')"
$RT_SHA   = 'EBE90CC1DCE2DD2D7AFA1F0AA9B6C269C7C0174AEABDC6A801381602B7283424'
$RUNTIME_DIR = $null   # set once the LOGIC folder is known (<LOGIC>\.runtime)

# --- Pinned es.exe (Everything command-line interface, voidtools) ----
$ES_VER = '1.1.0.27'
$ES_URL = "https://www.voidtools.com/ES-$ES_VER.x64.zip"
$ES_SHA = '16058CCAA225D2B40BF2FC67ACF64FD2D928A4BA485DEF7277885D66B6DC8A6A'

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

function Invoke-Download([string]$Url, [string]$Dest, [string]$Label = 'embedded Python') {
    # Tries curl.exe (ships with Win10+) then Invoke-WebRequest, 3 rounds.
    for ($i = 1; $i -le 3; $i++) {
        try {
            Write-Host "Downloading $Label (attempt $i/3)..." -ForegroundColor Gray
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
        throw "Could not download the private Python runtime. Check the internet connection (github.com must be reachable) and rerun."
    }
    if (-not (Test-FileSha256 -Path $tmp -ExpectedUpper $RT_SHA)) {
        $actual = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "Embedded Python download failed the SHA-256 check (got $actual). The file was corrupted or tampered with - rerun the installer."
    }
    Write-Host 'Checksum OK.' -ForegroundColor Green

    # 3) Extract with tar.exe (built into Windows 10 1803+)
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if (-not $tar) { throw 'tar.exe not found. Update Windows 10 (1803+) and rerun.' }
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

function Exit-Installer([int]$Code) {
    # In an elevated window that would vanish on exit, wait so errors can be read.
    if ($Elevated) {
        Write-Host ''
        Read-Host 'Press ENTER to close this window' | Out-Null
    }
    exit $Code
}

function Test-UnsafeDir([string]$Dir) {
    # True for empty/invalid paths and anything inside the Windows folder (System32 etc.).
    if (-not $Dir) { return $true }
    try { $full = [System.IO.Path]::GetFullPath($Dir).TrimEnd('\') } catch { return $true }
    $win = $env:windir.TrimEnd('\')
    return ($full -ieq $win) -or $full.StartsWith($win + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-DefaultInstallRoot {
    # First fixed, non-USB drive that is NOT the Windows drive and has >= 3 GB free
    # (alphabetical: D: before E:). Falls back to a folder in the user profile.
    $sys = $env:SystemDrive
    try {
        $disks = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop |
            Where-Object { $_.DeviceID -ne $sys -and $_.FreeSpace -ge 3GB } |
            Sort-Object DeviceID)
        foreach ($d in $disks) {
            $isUsb = $false
            try {
                $bus = (Get-Partition -DriveLetter $d.DeviceID.Substring(0, 1) -ErrorAction Stop |
                        Get-Disk -ErrorAction Stop).BusType
                if ("$bus" -eq 'USB') { $isUsb = $true }
            } catch { }
            if (-not $isUsb) { return (Join-Path ($d.DeviceID + '\') 'logic-harness') }
        }
    } catch { }
    return (Join-Path $env:LOCALAPPDATA 'logic-harness')
}

function Resolve-InstallRoot([string]$Requested) {
    # Returns an existing, writable folder that is not under C:\Windows.
    # With -InstallDir the choice is final; otherwise the user may accept the default or type another.
    $default     = Get-DefaultInstallRoot
    $interactive = -not $Requested
    $pick        = if ($Requested) { $Requested } else { $default }
    while ($true) {
        if ($interactive) {
            Write-Host ''
            Write-Host "LOGIC will be installed in this folder: $pick" -ForegroundColor Cyan
            $ans = (Read-Host 'Press ENTER to accept, or type a different folder (for example D:\logic-harness)').Trim().Trim('"')
            if ($ans) { $pick = $ans }
        }
        $problem = $null
        $full    = $null
        try { $full = [System.IO.Path]::GetFullPath($pick) } catch { $problem = "'$pick' is not a valid folder path." }
        if (-not $problem) {
            if ($full -notmatch '^[A-Za-z]:\\') { $problem = 'Please use a full path that starts with a drive letter, for example D:\logic-harness.' }
            elseif (-not (Test-Path -LiteralPath $full.Substring(0, 3))) { $problem = "Drive $($full.Substring(0, 2)) does not exist on this PC." }
            elseif (Test-UnsafeDir $full) { $problem = 'Please choose a folder outside the Windows directory (not System32).' }
            else {
                try { New-Item -ItemType Directory -Path $full -Force -ErrorAction Stop | Out-Null }
                catch { $problem = "Cannot create '$full': $($_.Exception.Message)" }
            }
        }
        if (-not $problem) { return $full }
        if (-not $interactive) { throw $problem }
        Write-Host $problem -ForegroundColor Yellow
        $pick = $default
    }
}

function Invoke-PipInstall([string]$PythonExe, [string[]]$PyPrefix, [string]$ReqFile) {
    $wheelsDir = Join-Path $scriptDir 'wheels'
    $hasWheels = (Test-Path $wheelsDir) -and (@(Get-ChildItem -LiteralPath $wheelsDir -Filter *.whl -ErrorAction SilentlyContinue).Count -gt 0)

    # Upgrading pip needs PyPI, so skip it in offline mode; a failure is not fatal.
    if (-not $hasWheels) {
        & $PythonExe @PyPrefix -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { Write-Host 'Could not upgrade pip - continuing with the version bundled in the runtime.' -ForegroundColor Yellow }
    }

    # Warn when the dependency list is not pinned (pinned = identical installs on every PC).
    $unpinned = @(Get-Content -LiteralPath $ReqFile |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith('#') -and -not $_.StartsWith('-') -and $_ -notmatch '==' })
    if ($unpinned.Count -gt 0) {
        Write-Host "Note: $($unpinned.Count) package(s) in $(Split-Path -Leaf $ReqFile) are not pinned to an exact version, so pip may install newer releases than the ones LOGIC was tested with." -ForegroundColor Yellow
        Write-Host 'To make every install identical, run this on your working PC and commit the file:  python -m pip freeze > requirements.lock' -ForegroundColor Yellow
    }

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

    # Elevate if needed. The relaunch uses the SAME PowerShell host (Windows PowerShell or
    # PowerShell 7), and -Elevated makes the new window wait for ENTER before it closes.
    if (-not (Test-IsAdmin)) {
        Write-Host 'Requesting administrator privileges through Windows UAC...' -ForegroundColor Yellow
        $hostExe = (Get-Process -Id $PID).Path
        if (-not $hostExe -or -not (Test-Path -LiteralPath $hostExe)) { $hostExe = "$PSHOME\powershell.exe" }

        if ($isRemote) {
            $url   = 'https://raw.githubusercontent.com/Ancientduck/logic-harness/main/install.ps1'
            $extra = ''
            if ($InstallDir) { $extra = " -InstallDir '$InstallDir'" }
            $elevCmd  = "& ([scriptblock]::Create((irm '$url'))) -Elevated$extra"
            $elevArgs = "-NoProfile -ExecutionPolicy Bypass -Command `"$elevCmd`""
        } else {
            $elevArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -InstallerPath `"$scriptPath`" -Elevated"
            if ($InstallDir) { $elevArgs += " -InstallDir `"$InstallDir`"" }
        }

        $child = Start-Process -FilePath $hostExe -ArgumentList $elevArgs -Verb RunAs -Wait -PassThru
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
    Write-Section '0/8 Choose install location and get the logic-harness repository'

    $REPO_URL  = 'https://github.com/Ancientduck/logic-harness.git'
    $REPO_NAME = 'logic-harness'

    function Test-LogicRoot([string]$Dir) {
        if (-not $Dir) { return $false }
        return (Test-Path (Join-Path $Dir 'requirements.txt')) -and
               (Test-Path (Join-Path $Dir 'logic_tui.py'))
    }

    $scriptDir = $null

    # Reuse a LOGIC checkout the installer sits in (or next to) - never one under C:\Windows.
    if (-not $isRemote) {
        $here = Split-Path -Parent $scriptPath
        foreach ($c in @($here, (Join-Path $here $REPO_NAME))) {
            if ((Test-LogicRoot $c) -and -not (Test-UnsafeDir $c)) {
                $scriptDir = $c
                Write-Host "Found existing LOGIC harness at: $scriptDir" -ForegroundColor Green
                break
            }
        }
    }

    if (-not $scriptDir) {
        # Pick a normal drive (D:, E:, ...) or whatever the user types - never System32, whatever the working directory is.
        $installRoot = Resolve-InstallRoot -Requested $InstallDir
        Write-Host "LOGIC folder: $installRoot" -ForegroundColor Cyan
        $cloneTarget = $installRoot   # the chosen folder IS the repo folder

        if (Test-LogicRoot $cloneTarget) {
            $scriptDir = $cloneTarget
            Write-Host "Found existing LOGIC harness at: $scriptDir" -ForegroundColor Green
        } else {
            # Need to clone. First make sure git is available.
            $git = Get-Command git.exe -ErrorAction SilentlyContinue
            if (-not $git) {
                Write-Host 'Git not found - installing Git for Windows via winget...' -ForegroundColor Yellow
                Invoke-WingetInstall -Id 'Git.Git' -Name 'Git'
                Refresh-Path
                $git = Get-Command git.exe -ErrorAction SilentlyContinue
                if (-not $git) {
                    throw 'Git is still unavailable after installation. Install it manually from https://git-scm.com and rerun.'
                }
            }

            if (Test-Path -LiteralPath $cloneTarget) {
                $items = @(Get-ChildItem -LiteralPath $cloneTarget -Force -ErrorAction SilentlyContinue)
                if ($items.Count -gt 0) {
                    if (Test-Path -LiteralPath (Join-Path $cloneTarget '.git')) {
                        Write-Host "Removing incomplete earlier clone: $cloneTarget" -ForegroundColor Yellow
                        Remove-Item -LiteralPath $cloneTarget -Recurse -Force -ErrorAction SilentlyContinue
                    } else {
                        throw "The folder '$cloneTarget' already exists and does not look like LOGIC. Delete it or choose another install folder, then rerun."
                    }
                }
            }

            Write-Host "Cloning $REPO_URL -> $cloneTarget ..." -ForegroundColor Cyan
            & $git.Source clone --depth 1 $REPO_URL $cloneTarget
            if ($LASTEXITCODE -ne 0) {
                throw "git clone failed. Check your internet connection and try again."
            }
            $scriptDir = $cloneTarget
            Write-Host "Repository cloned successfully." -ForegroundColor Green
        }
    }

    # The private Python runtime lives inside the LOGIC folder (same drive as LOGIC).
    $RUNTIME_DIR = Join-Path $scriptDir '.runtime'

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

    # Exact pinned versions (requirements.lock) win over the loose requirements.txt.
    $lockFile = Join-Path $scriptDir 'requirements.lock'
    if (Test-Path -LiteralPath $lockFile -PathType Leaf) {
        $requirements = $lockFile
        Write-Host 'Using pinned dependency list: requirements.lock' -ForegroundColor Green
    }

    # ---------------------------------------------------------------
    Write-Section '1/8 Private Python 3.13 runtime (pinned, independent of the system)'
    Write-Host 'LOGIC uses its own pinned Python so its libraries always match. Python versions already on this PC are left alone.' -ForegroundColor Gray
    Refresh-Path
    $appPython   = Get-EmbeddedPython
    $appPyPrefix = @()

    $v = Get-PyVer -Exe $appPython -Prefix $appPyPrefix
    if (-not $v) { throw "Python at '$appPython' did not respond to a version check." }
    if (-not $v.StartsWith('3.13')) {
        throw "Refusing to continue: '$appPython' is Python $v. LOGIC requires 3.13.x."
    }
    Write-Host "Using private Python $v" -ForegroundColor Green

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
    Write-Section '6/8 Install es.exe (Everything command-line tool)'
    $everythingDir = Split-Path -Parent $everythingExe
    $esExe = Join-Path $everythingDir 'es.exe'

    if (Test-Path -LiteralPath $esExe -PathType Leaf) {
        Write-Host "es.exe already present: $esExe" -ForegroundColor Green
    } else {
        $esZip = Join-Path $env:TEMP "ES-$ES_VER.x64.zip"
        Remove-Item -LiteralPath $esZip -Force -ErrorAction SilentlyContinue
        if (-not (Invoke-Download -Url $ES_URL -Dest $esZip -Label "es.exe $ES_VER")) {
            throw "Could not download es.exe from $ES_URL. Check the internet connection, or download ES from https://www.voidtools.com/downloads and put es.exe in $everythingDir, then rerun."
        }
        if (-not (Test-FileSha256 -Path $esZip -ExpectedUpper $ES_SHA)) {
            $actual = (Get-FileHash -LiteralPath $esZip -Algorithm SHA256).Hash
            Remove-Item -LiteralPath $esZip -Force -ErrorAction SilentlyContinue
            throw "es.exe download failed the SHA-256 check (got $actual). The file was corrupted or tampered with - rerun the installer."
        }
        Write-Host 'Checksum OK.' -ForegroundColor Green

        $esExtract = Join-Path $env:TEMP ('logic-es-' + [System.Guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $esZip -DestinationPath $esExtract -Force
        $esFound = Get-ChildItem -LiteralPath $esExtract -Filter 'es.exe' -Recurse -File | Select-Object -First 1
        if (-not $esFound) { throw 'es.exe was not found inside the downloaded archive.' }
        Copy-Item -LiteralPath $esFound.FullName -Destination $esExe -Force
        Remove-Item -LiteralPath $esExtract -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $esZip -Force -ErrorAction SilentlyContinue
        Write-Host "es.exe installed: $esExe" -ForegroundColor Green
    }
    if (-not (Test-Path -LiteralPath $esExe -PathType Leaf)) { throw "es.exe is missing after installation: $esExe" }

    # Put the Everything folder (now containing es.exe) on PATH - persistent and this session.
    Add-UserPath $everythingDir
    Refresh-Path
    if (-not (Get-Command es.exe -ErrorAction SilentlyContinue)) {
        Write-Host 'es.exe is installed but not yet visible in this session. Open a new terminal after setup.' -ForegroundColor Yellow
    }

    # es.exe talks to the running Everything app, so make sure it is running (background/tray).
    if (-not (Get-Process -Name 'Everything' -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $everythingExe -ArgumentList '-startup'
        Start-Sleep -Seconds 3
        Write-Host 'Everything started in the background.' -ForegroundColor Green
    }

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
    Write-Host "LOGIC folder: $scriptDir"
    Write-Host 'Run: 9router'
    Write-Host 'Then open http://localhost:20128 and finish the provider/model/API-token setup.'
    Write-Host 'Finally run: logic'
    Exit-Installer 0
}
catch {
    Write-Host ''
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Fix the issue above (internet, disk space, antivirus) and run the installer again - it is safe to re-run.' -ForegroundColor Yellow
    Exit-Installer 1
}
