param(
    [switch]$CheckOnly,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

# Resolve the worktree by branch, even when this launcher is copied to another
# checkout. Never start the current feature branch on the production port.
$worktreeLines = @(& git -C $PSScriptRoot -c core.quotepath=false worktree list --porcelain)
if ($LASTEXITCODE -ne 0) { throw 'Cannot read Git worktrees.' }
$mainRoot = $null
$entryRoot = $null
foreach ($line in $worktreeLines) {
    if ($line.StartsWith('worktree ')) {
        $entryRoot = $line.Substring(9)
    } elseif ($line -eq 'branch refs/heads/main') {
        $mainRoot = [IO.Path]::GetFullPath($entryRoot)
    }
}
if (!$mainRoot) { throw 'No main worktree found. Restore the main worktree before starting VELVET.' }
$branch = & git -C $mainRoot branch --show-current
if ($LASTEXITCODE -ne 0 -or $branch -ne 'main') { throw 'The selected runtime is not on main.' }
$serverPath = Join-Path $mainRoot 'server.py'
if (!(Test-Path -LiteralPath $serverPath)) { throw "Missing server: $serverPath" }
if (!(Test-Path -LiteralPath (Join-Path $mainRoot '.env'))) {
    throw 'Main has no .env. Configure its existing library before starting.'
}

$commonGitDir = & git -C $mainRoot rev-parse --path-format=absolute --git-common-dir
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the shared Python environment.' }
$originalRoot = Split-Path -Parent $commonGitDir
$pythonPath = @(
    (Join-Path $mainRoot 'venv\Scripts\python.exe'),
    (Join-Path $originalRoot 'venv\Scripts\python.exe'),
    (Join-Path $mainRoot '.venv\Scripts\python.exe'),
    (Join-Path $originalRoot '.venv\Scripts\python.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (!$pythonPath) { throw 'No existing VELVET Python environment found. Run install.bat in main first.' }

Push-Location -LiteralPath $mainRoot
try {
    @'
from settings import VelvetSettings
import sys
if not VelvetSettings().db_path.is_file():
    sys.exit('Configured library.db is missing; restore the existing library before starting.')
'@ | & $pythonPath -
    if ($LASTEXITCODE -ne 0) { throw 'The existing main library or Python environment is unavailable.' }
} finally {
    Pop-Location
}

Write-Host "VELVET main: $mainRoot"
Write-Host "Revision: $(& git -C $mainRoot rev-parse --short HEAD)"
Write-Host 'Web: http://localhost:8765'

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    foreach ($ownerId in @($listeners.OwningProcess | Select-Object -Unique)) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId"
        if (!$owner -or !$owner.CommandLine -or
            $owner.CommandLine.Replace('/', '\').IndexOf($serverPath, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "Port 8765 belongs to another process (PID $ownerId). It was left running; inspect it before retrying."
        }
    }
    # A live PID alone does not establish that the correct frontend is served.
    foreach ($asset in @('static/index.html', 'static/js/library-views.js', 'static/js/player-ui.js', 'static/manifest.json', 'sw.js')) {
        $urlPath = if ($asset -eq 'static/index.html') { '' } else { $asset }
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8765/$urlPath" -TimeoutSec 10
        $served = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
        $expected = [IO.File]::ReadAllText((Join-Path $mainRoot $asset))
        if (($served -replace "`r`n", "`n") -cne ($expected -replace "`r`n", "`n")) {
            throw "Port 8765 is serving a different $asset. Inspect the running server before retrying."
        }
    }
    Write-Host 'The main server is already running; its page and assets match.'
    if (!$CheckOnly -and !$NoBrowser) { Start-Process 'http://localhost:8765' }
    return
}

if ($CheckOnly) {
    Write-Host "Ready to start with: $pythonPath"
    return
}

Set-Location -LiteralPath $mainRoot
$env:VELVET_PORT = '8765'
$env:PYTHONUNBUFFERED = '1'
foreach ($toolRoot in @($mainRoot, $originalRoot)) {
    $toolBin = Join-Path $toolRoot 'tools\bin'
    if (Test-Path -LiteralPath $toolBin) { $env:PATH = "$toolBin;$env:PATH" }
}
if (!$NoBrowser) {
    $browserHelper = Join-Path $mainRoot 'open_ui_when_ready.py'
    Start-Process -FilePath $pythonPath -ArgumentList ('"' + $browserHelper + '"') -WorkingDirectory $mainRoot -WindowStyle Hidden | Out-Null
}
Write-Host 'Keep this window open while using VELVET. Press Ctrl+C to stop.'
& $pythonPath $serverPath
if ($LASTEXITCODE -ne 0) { throw "VELVET exited with code $LASTEXITCODE." }
