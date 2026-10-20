@echo off
title VELVET Remote Start

REM ─── VELVET Remote Access Auto-Start Script ─────────────
REM This script starts Tailscale (if installed) then launches VELVET

setlocal EnableDelayedExpansion

:: ─── Check if Tailscale is installed ───
set "TAILSCALE_EXE="
if exist "%LOCALAPPDATA%\Programs\Tailscale\Tailscale.exe" (
    set "TAILSCALE_EXE=%LOCALAPPDATA%\Programs\Tailscale\Tailscale.exe"
) else if exist "%ProgramFiles%\Tailscale\Tailscale.exe" (
    set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\Tailscale.exe"
)

:: ─── Start Tailscale if installed ───
if defined TAILSCALE_EXE (
    echo [*] Starting Tailscale...
    start "" "%TAILSCALE_EXE%"

    echo [*] Waiting for Tailscale to connect...
    :: Wait up to 30 seconds for Tailscale
    for /L %%i in (1,1,30) do (
        timeout /t 1 /nobreak >nul
        tailscale status >nul 2>&1
        if not errorlevel 1 goto :tailscale_ready
    )
    echo [!] Tailscale may not be connected yet, continuing anyway...
    goto :skip_tailscale

    :tailscale_ready
    echo [OK] Tailscale is connected

    :: Show Tailscale IP
    for /f "delims=" %%i in ('tailscale ip -4 2^>nul') do set TAILSCALE_IP=%%i
    if defined TAILSCALE_IP (
        echo.
        echo ================================================
        echo   VELVET Remote Access URL:
        echo   http://%TAILSCALE_IP%:8765
        echo ================================================
        echo.
    )

    :skip_tailscale
) else (
    echo [INFO] Tailscale not found - remote access will not be available
    echo       Install Tailscale from: https://tailscale.com/download
    echo.
)

:: ─── Check Python ───
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

:: ─── Check FFmpeg ───
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] FFmpeg not found - upsampling will be disabled
) else (
    echo [OK] FFmpeg available
)

:: ─── Activate venv if needed ───
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    call venv\Scripts\pip install --upgrade pip -q
    call venv\Scripts\pip install -r requirements.txt -q
)

call venv\Scripts\activate.bat

:: ─── Install dependencies ───
echo [*] Ensuring dependencies are installed...
pip install --disable-pip-version-check -r requirements.txt -q

:: ─── Start MusicIQ server ───
echo [*] Starting VELVET server...
echo    Local:  http://localhost:8765
if defined TAILSCALE_IP (
    echo    Remote: http://%TAILSCALE_IP%:8765
)
echo.

python server.py -n 2>nul

REM Cleanup on exit
exit /b
