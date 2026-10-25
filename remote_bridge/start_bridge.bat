@echo off
setlocal
cd /d "%~dp0"
if not exist config.json (
  copy /Y config.example.json config.json >nul
  echo.
  echo [CONFIG] Edit config.json and set upstream_url to your parents' Velvet Tailscale URL.
  notepad config.json
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 bridge.py --config config.json
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required. Install Python from python.org.
    pause
    exit /b 1
  )
  python bridge.py --config config.json
)
if errorlevel 1 echo [ERROR] The bridge did not start. Check config.json and Tailscale.
pause
