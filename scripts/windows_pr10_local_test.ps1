# Run from PowerShell on the Windows server. No GitHub Actions are invoked.
param(
    [switch]$Quick,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Host "Creating isolated Python 3.12 environment..."
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Python 3.12 venv creation failed" }
    }
    $python = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not $SkipInstall) {
        & $python -m pip install -r requirements.txt pytest
        if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }
    }
    Write-Host "Checking Python syntax..."
    & $python -m compileall -q server.py library_paging.py library_safety.py db_pool.py local_playback.py remote_access.py
    if ($LASTEXITCODE -ne 0) { throw "Python syntax check failed" }

    if ($Quick) {
        $cases = @(
            "tests/test_library_paging.py",
            "tests/test_library_discovery.py",
            "tests/test_library_safety.py",
            "tests/test_db_pool_lifecycle.py",
            "tests/test_remote_access_https.py",
            "tests/test_native_volume_position.py",
            "tests/test_server_api.py"
        )
        & $python -m pytest @cases -q
    } else {
        & $python -m pytest tests -q
    }
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed" }

    if (-not $SkipInstall) {
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm dependency installation failed" }
    }
    Write-Host "Running frontend and PWA tests..."
    & npm run test:frontend
    if ($LASTEXITCODE -ne 0) { throw "Frontend regression tests failed" }

    Write-Host ""
    Write-Host "LOCAL TESTS PASSED. Hardware acceptance and music-library backup checks remain separate."
    Write-Host "No GitHub Actions workflows were triggered."
} finally {
    Pop-Location
}
