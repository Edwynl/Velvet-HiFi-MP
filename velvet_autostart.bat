@echo off
title VELVET Auto-Start

REM ─── VELVET Windows Auto-Start Script ─────────────
REM This script launches VELVET at user login (quick start, no rescan)

setlocal EnableDelayedExpansion

:: Check Python first
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    echo To setup auto-start manually: Run 'start.bat' once to create venv.
    pause
    exit /b 1
)

:: Check FFmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] FFmpeg not found - upsampling will be disabled
) else (
    echo [OK] FFmpeg available
)

:: Activate venv if needed
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    call venv\Scripts\pip install --upgrade pip -q
    call venv\Scripts\pip install -r requirements.txt -q
)

call venv\Scripts\activate.bat

:: Install dependencies
echo [*] Ensuring dependencies are installed...
pip install --disable-pip-version-check -r requirements.txt -q

:: Start server (quick start mode - skip rescan if library already exists)
python server.py -n 2>nul

REM Cleanup on exit - kill remaining Python process
exit /b
