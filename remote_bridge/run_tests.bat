@echo off
setlocal
cd /d "%~dp0"
rem Both suites: the bridge itself (Python) and the page's queue sync (Node 18+).
rem Exit code 0 only if both pass. "run_tests.bat --no-pause" for use from a script.
set "PY=python"
where py >nul 2>&1 && set "PY=py -3"
set FAILED=0
%PY% -m unittest discover -s tests -v
if errorlevel 1 set FAILED=1
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js 18 or newer is needed for tests\queue_sync.test.mjs: https://nodejs.org/
  set FAILED=1
) else (
  node tests\queue_sync.test.mjs
  if errorlevel 1 set FAILED=1
)
echo.
if %FAILED%==0 (echo ALL TESTS PASSED) else (echo TESTS FAILED)
if /i not "%~1"=="--no-pause" pause
exit /b %FAILED%
