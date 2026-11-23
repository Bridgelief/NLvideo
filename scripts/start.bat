@echo off
rem Windows cmd launcher for the AI shooting assistant.
rem Usage: scripts\start.bat   (set PORT=8011 to use another port)
setlocal
cd /d "%~dp0.."
set "PYTHONPATH=%CD%"
if not defined PORT set "PORT=8000"

echo === AI Shooting Assistant - startup checks ===
echo Repo root : %CD%

where python >nul 2>nul
if errorlevel 1 (
  echo [x] python not found. Install Python 3.11 and add it to PATH.
  exit /b 1
)
for /f "delims=" %%V in ('python --version 2^>^&1') do echo [v] %%V

set "MISSING=0"
for %%F in (data\intent_weights.json data\rules.json data\phrases.json data\edit_rules.json data\materials.csv) do (
  if not exist "%%F" set "MISSING=1"
)
if "%MISSING%"=="1" (
  echo [!] data/ files incomplete: service will start but suggestions/edit may degrade.
) else (
  echo [v] data/ key files present
)

if not exist "engine\suggest_engine.py" echo [!] engine not ready yet: service will use built-in fallback
if exist "engine\suggest_engine.py" if exist "engine\edit_engine.py" if exist "engine\edl_export.py" echo [v] engine/ present

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [i] ffmpeg not found: rough cut exports EDL/commands/.bat only.
) else (
  echo [v] ffmpeg found, real rendering is possible.
)

netstat -ano | findstr /R /C:"127.0.0.1:%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo [x] Port %PORT% is already in use. The service may already be running.
  echo     Try another port: set PORT=8011 ^& scripts\start.bat
  exit /b 1
)
echo [v] Port %PORT% is available

echo.
echo Serving at http://127.0.0.1:%PORT%
echo Press Ctrl+C to stop
echo.

python -m uvicorn server.app:app --host 127.0.0.1 --port %PORT%
exit /b %errorlevel%
