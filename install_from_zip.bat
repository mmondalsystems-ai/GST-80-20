@echo off
REM Standalone client bootstrap: select a repository ZIP, prepare the local app,
REM and launch the portal.
setlocal EnableExtensions DisableDelayedExpansion

set "GST8020_APP=%LOCALAPPDATA%\GST-80-20"
set "GST8020_START_PORT=8080"

if /I "%~1"=="/?" goto usage
if /I "%~1"=="--help" goto usage
if not "%~1"=="" set "GST8020_START_PORT=%~1"
if not "%~2"=="" goto usage

if exist "%GST8020_APP%\deploy.ps1" goto prepare
if exist "%GST8020_APP%" goto incomplete_target

:ask_zip
echo.
echo Enter the full path to the GST 80-20 GitHub repository ZIP.
echo You can drag the ZIP file into this window and press Enter.
set "GST8020_ZIP="
set /p "GST8020_ZIP=Repository ZIP: "
if not defined GST8020_ZIP goto cancelled
set "GST8020_ZIP=%GST8020_ZIP:"=%"
if not exist "%GST8020_ZIP%" (
  echo File not found: "%GST8020_ZIP%"
  goto ask_zip
)
for %%I in ("%GST8020_ZIP%") do set "GST8020_ZIP=%%~fI"
for %%I in ("%GST8020_ZIP%") do if /I not "%%~xI"==".zip" (
  echo The selected file is not a .zip archive.
  goto ask_zip
)

echo.
echo Validating and extracting into:
echo   "%GST8020_APP%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $target=$env:GST8020_APP; $stage=Join-Path $env:LOCALAPPDATA ('GST-80-20-stage-'+[guid]::NewGuid().ToString('N')); try { New-Item -ItemType Directory -Path $stage | Out-Null; Expand-Archive -LiteralPath $env:GST8020_ZIP -DestinationPath $stage; $found=@(Get-ChildItem -LiteralPath $stage -Filter 'deploy.bat' -File -Recurse); if ($found.Count -ne 1) { throw 'The ZIP must contain exactly one application deploy.bat.' }; $root=$found[0].Directory.FullName; foreach ($required in @('deploy.ps1','install.ps1','requirements.txt','app\main.py')) { if (-not (Test-Path -LiteralPath (Join-Path $root $required) -PathType Leaf)) { throw ('The ZIP is incomplete; missing '+$required) } }; foreach ($forbidden in @('issue_license.py','.env','var')) { if (Test-Path -LiteralPath (Join-Path $root $forbidden)) { throw ('Use a clean current repository ZIP; it must not contain '+$forbidden) } }; foreach ($pem in @(Get-ChildItem -LiteralPath $root -Filter '*.pem' -File -Recurse)) { if (Select-String -LiteralPath $pem.FullName -Pattern 'BEGIN.*PRIVATE KEY' -Quiet) { throw 'The ZIP contains a private key and must not be distributed.' } }; if (Test-Path -LiteralPath $target) { throw 'The destination already exists.' }; Move-Item -LiteralPath $root -Destination $target } finally { if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue } }"
if errorlevel 1 (
  echo.
  echo The ZIP was not installed. Verify that it is the complete repository archive.
  pause
  exit /b 1
)

:prepare
if not exist "%GST8020_APP%\deploy.ps1" goto incomplete_target
if exist "%GST8020_APP%\issue_license.py" (
  echo This is an older package containing issuer-only code. It was not started.
  echo Archive the old folder, then use a current clean repository ZIP.
  pause
  exit /b 1
)
echo.
echo Preparing the local installation. This may install Python and dependencies.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%GST8020_APP%\deploy.ps1" -Port "%GST8020_START_PORT%" -PrepareOnly
if errorlevel 1 (
  echo.
  echo Preparation did not complete. Fix the message above and run this BAT again.
  pause
  exit /b 1
)

echo Starting GST 80-20...
call "%GST8020_APP%\deploy.bat" "%GST8020_START_PORT%"
exit /b %errorlevel%

:incomplete_target
echo.
echo This destination exists but is not a complete installation:
echo   "%GST8020_APP%"
echo It was not overwritten. Inspect or rename that folder before retrying.
pause
exit /b 1

:cancelled
echo No ZIP was selected. Nothing was installed.
exit /b 0

:usage
echo Usage: install_from_zip.bat [starting-port]
echo Prompts for a repository ZIP, prepares a local installation, and starts the portal.
exit /b 0
