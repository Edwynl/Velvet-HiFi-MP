@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
    echo.
    echo [ERROR] VELVET installation failed.
    pause
    exit /b 1
)

echo.
echo [OK] VELVET installation completed.
pause
