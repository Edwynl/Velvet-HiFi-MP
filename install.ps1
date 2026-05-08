param(
    [switch]$InstallSystemTools,
    [switch]$SkipNode
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-WingetInstall {
    param(
        [string]$PackageId,
        [string]$Name
    )

    if (-not (Test-Command "winget")) {
        throw "winget is not available. Install $Name manually, then run this script again."
    }

    Write-Step "Installing $Name with winget"
    winget install --id $PackageId --exact --accept-package-agreements --accept-source-agreements
}

function Get-PythonCommand {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "py"; Args = @("-3.10") },
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Test-Command $candidate.Exe)) {
            continue
        }

        $versionArgs = @($candidate.Args + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"))
        & $candidate.Exe @versionArgs *> $null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]$candidate
        }
    }

    return $null
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]$Python,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )

    $allArgs = @($Python.Args + $Arguments)
    & $Python.Exe @allArgs
}

function Resolve-DefaultMusicDir {
    $candidates = @()
    if ($env:USERPROFILE) {
        $candidates += Join-Path $env:USERPROFILE "Music"
    }
    $candidates += @("C:\Music", "D:\Music", "E:\Music")

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    if ($env:USERPROFILE) {
        return Join-Path $env:USERPROFILE "Music"
    }

    return "C:\Music"
}

function Ensure-EnvFile {
    $envPath = Join-Path $Root ".env"
    if (Test-Path -LiteralPath $envPath) {
        Write-Ok ".env already exists"
        return
    }

    $musicDir = Resolve-DefaultMusicDir
    $content = @(
        "# VELVET local configuration",
        "# This file is machine-specific and should not be committed.",
        "VELVET_MUSIC_DIR=$musicDir",
        "VELVET_DATA_DIR=velvet_data",
        "VELVET_PORT=8765",
        "VELVET_HOST=0.0.0.0",
        "VELVET_UPNP_FRIENDLY_NAME=VELVET",
        "",
        "# Optional API keys",
        "# ACOUSTID_KEY=",
        "# LASTFM_API_KEY=",
        "",
        "# Optional CORS override, JSON array",
        "# VELVET_CORS_ORIGINS=[`"http://localhost:8765`",`"http://127.0.0.1:8765`"]"
    )

    Set-Content -LiteralPath $envPath -Value $content -Encoding UTF8
    Write-Ok "Created .env with music directory: $musicDir"
}

function Test-ToolOnPath {
    param([string]$Tool)

    $localBin = Join-Path $Root "tools\bin"
    if (Test-Path -LiteralPath $localBin) {
        $env:PATH = "$localBin;$env:PATH"
    }
    $env:PATH = "$Root;$env:PATH"

    if (-not (Test-Command $Tool)) {
        return $false
    }

    if ($Tool -eq "ffmpeg") {
        & $Tool -version *> $null
        return ($LASTEXITCODE -eq 0)
    }

    return $true
}

Write-Host "VELVET installer / environment repair" -ForegroundColor Magenta
Write-Host "Project: $Root"

Write-Step "Checking Python"
$python = Get-PythonCommand
if (-not $python -and $InstallSystemTools) {
    Invoke-WingetInstall -PackageId "Python.Python.3.12" -Name "Python 3.12"
    $python = Get-PythonCommand
}
if (-not $python) {
    throw "Python 3.10+ was not found. Install Python, or run: .\install.ps1 -InstallSystemTools"
}
Invoke-Python $python --version

Write-Step "Creating Python virtual environment"
if (-not (Test-Path -LiteralPath (Join-Path $Root "venv\Scripts\python.exe"))) {
    Invoke-Python $python -m venv venv
}
Write-Ok "venv is ready"

$venvPython = Join-Path $Root "venv\Scripts\python.exe"

Write-Step "Installing Python dependencies"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install --disable-pip-version-check -r requirements.txt
Write-Ok "Python dependencies installed"

Write-Step "Checking FFmpeg"
if (-not (Test-ToolOnPath "ffmpeg")) {
    if ($InstallSystemTools) {
        Invoke-WingetInstall -PackageId "Gyan.FFmpeg" -Name "FFmpeg"
    } else {
        Write-Warn "FFmpeg was not found. Upsampling and local decode features will be limited."
        Write-Warn "Run .\install.ps1 -InstallSystemTools, install FFmpeg manually, or place ffmpeg.exe in tools\bin."
    }
} else {
    Write-Ok "FFmpeg is available"
}

Write-Step "Checking fpcalc"
if (Test-ToolOnPath "fpcalc") {
    Write-Ok "fpcalc is available"
} else {
    Write-Warn "fpcalc was not found. AcoustID fingerprinting is optional and will be disabled."
    Write-Warn "Place fpcalc.exe in the project root or tools\bin to enable it."
}

if (-not $SkipNode -and (Test-Path -LiteralPath (Join-Path $Root "package.json"))) {
    Write-Step "Checking Node.js dependencies"
    if (-not (Test-Command "node") -or -not (Test-Command "npm")) {
        if ($InstallSystemTools) {
            Invoke-WingetInstall -PackageId "OpenJS.NodeJS.LTS" -Name "Node.js LTS"
        } else {
            Write-Warn "Node.js/npm was not found. Frontend tests will be unavailable."
            Write-Warn "Run .\install.ps1 -InstallSystemTools or install Node.js LTS manually."
        }
    }

    if ((Test-Command "node") -and (Test-Command "npm")) {
        npm install
        Write-Ok "Node dependencies installed"
    }
}

Write-Step "Preparing local configuration"
Ensure-EnvFile

Write-Step "Sanity check"
& $venvPython -m py_compile settings.py server.py upnp_server.py enrichment.py local_playback.py env_loader.py
Write-Ok "Python files compile"

Write-Host ""
Write-Host "VELVET is ready." -ForegroundColor Green
Write-Host "Start it with: .\start.bat"
Write-Host "Open: http://localhost:8765"
