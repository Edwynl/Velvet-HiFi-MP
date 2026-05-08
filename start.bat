@echo off
setlocal

title VELVET - Personal Music Server
color 0A
cd /d "%~dp0"

echo.
echo ===============================
echo    VELVET Personal Music Server
echo ===============================
echo.

if exist "%~dp0tools\bin" set "PATH=%~dp0tools\bin;%PATH%"
set "PATH=%~dp0;%PATH%"

echo [*] Repairing/checking local environment...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -SkipNode
if errorlevel 1 (
    echo.
    echo [ERROR] Environment setup failed.
    echo Run install.bat or install.ps1 -InstallSystemTools for automatic system dependency installation.
    pause
    exit /b 1
)

call "%~dp0venv\Scripts\activate.bat"

echo.
echo [*] Starting VELVET server...
python "%~dp0server.py" %*

echo.
echo Server stopped.
pause
