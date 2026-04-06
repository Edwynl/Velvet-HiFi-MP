@echo off
setlocal EnableDelayedExpansion

title VELVET - Personal Music Server
color 0A

:: ============================================================================
:: VELVET Startup Script v3.1
:: Enhanced with:
::   - FFmpeg availability detection (with version check)
::   - Chromaprint (fpcalc) availability detection
::   - Quick start mode (-n flag for already-scanned library)
::   - Skip auto-venv creation if needed
:: ============================================================================

echo.
echo ===============================
echo    VELVET Personal Music Server
echo ===============================
echo.

:: Parse arguments
set QUICK_START=false
if "%1"=="-n" (
    set QUICK_START=true
    echo [INFO] Quick start mode enabled (skipping library scan)
    echo.
)

:: Check Python
echo [*] Checking Python installation...
set PYTHON_CMD=python
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON_CMD=py
    ) else (
        echo [ERROR] Python not found. Please install Python 3.10+
        echo Download from: https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
echo [OK] Using Python command: !PYTHON_CMD!

:: Check FFmpeg with version info
echo [*] Checking FFmpeg...
ffmpeg -version >nul 2>&1
if not errorlevel 1 (
    set FFmpeg_AVAILABLE=true
) else (
    set FFmpeg_AVAILABLE=false
)

if "!FFmpeg_AVAILABLE!"=="true" (
    set "FFMPEG_VER="
    for /f "tokens=3" %%a in ('ffmpeg -version 2^>nul') do (
        if not defined FFMPEG_VER set "FFMPEG_VER=%%a"
    )
    echo [OK] FFmpeg !FFMPEG_VER! found - upsampling enabled!
) else (
    echo [WARNING] FFmpeg not found. Upsampling feature will be disabled.
    echo To enable: download from https://ffmpeg.org/download.html and add to PATH
    echo or copy ffmpeg.exe to this folder.
)

:: Check Chromaprint (fpcalc) - optional fingerprinting tool
echo [*] Checking Chromaprint...
set "FPCALC_PATH="
for %%I in (fpcalc.exe) do set "FPCALC_PATH=%%~$PATH:I"
if not defined FPCALC_PATH (
    if exist "%CD%\fpcalc.exe" set "FPCALC_PATH=%CD%\fpcalc.exe"
)

if defined FPCALC_PATH (
    echo [OK] fpcalc found at !FPCALC_PATH! - fingerprinting enabled!
) else (
    echo [INFO] Chromaprint ^(fpcalc.exe^) not found - audio fingerprinting disabled.
    echo Download from: https://acoustid.org/chromaprint
)

:: Check environment variables
if "%MUSIC_DIR%"=="" set MUSIC_DIR=E:\roon\StreamripDownloads
if "%DATA_DIR%"=="" set DATA_DIR=velvet_data
:: VELVET_MUSIC_DIR is used by pydantic-settings (with VELVET__ prefix)
set VELVET_MUSIC_DIR=%MUSIC_DIR%

echo [*] Music directory: %MUSIC_DIR%
echo [*] Data directory: %DATA_DIR%
echo.

:: Install dependencies if needed (optional - faster to always run)
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    !PYTHON_CMD! -m venv venv
)

call venv\Scripts\activate.bat

echo [*] Installing/updating dependencies...
python -m pip install --disable-pip-version-check -r requirements.txt -q

:: Quick start mode: just show status without scanning
if "%QUICK_START%"=="true" (
    echo.
    echo ===============================
    echo     Status Report
    echo ===============================
    echo Server ready at http://localhost:8765
    echo.
    echo [INFO] Use Settings to configure your library path
    echo [INFO] Or run without -n flag for full scan
    echo ===============================
    pause
)

echo.
echo [*] Starting VELVET server...
python server.py

:: If Ctrl+C occurred, show exit message
if errorlevel 1 (
    echo.
    echo VELVET stopped by user. Press any key to exit...
    pause >nul
) else (
    echo.
    echo Server stopped normally.
)

endlocal
pause
