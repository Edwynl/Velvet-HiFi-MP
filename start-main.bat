@echo off
setlocal
title VELVET - main on 8765
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-main.ps1" %*
if errorlevel 1 (
    echo.
    echo VELVET main could not start. Read the error above.
    pause
    exit /b 1
)
