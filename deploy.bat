@echo off
REM Source-based Windows setup and local launch. Does not use the separate EXE.
REM Usage: deploy.bat [port] [--prepare-only]  (default: 8080)
setlocal
cd /d "%~dp0"

if /I "%~1"=="/?" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--help" goto usage

set "PORT=8080"
if not "%~1"=="" set "PORT=%~1"
if not "%~2"=="" if /I not "%~2"=="--prepare-only" goto usage
if not "%~3"=="" goto usage

if /I "%~2"=="--prepare-only" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -Port "%PORT%" -PrepareOnly
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -Port "%PORT%"
)
if errorlevel 1 (
  echo.
  echo Setup or launch did not complete. Review the message above.
  if /I not "%~2"=="--prepare-only" pause
  exit /b 1
)
exit /b 0

:usage
echo Usage: deploy.bat [port] [--prepare-only]
echo Source-based installer. Requires supported Python and internet or wheelhouse.
echo PostgreSQL is not used by this local SQLite launcher.
exit /b 0
