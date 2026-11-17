@echo off
REM Run this from an extracted/downloaded GST 80-20 repository folder.
setlocal
if /I "%~1"=="/?" goto usage
if /I "%~1"=="--help" goto usage
if not "%~2"=="" goto usage
if not exist "%~dp0deploy.bat" (
  echo deploy.bat is missing. Extract the complete GitHub repository first.
  pause
  exit /b 1
)
echo Starting GST 80-20 from:
echo   %~dp0
echo Keep this folder outside OneDrive, Google Drive, and Dropbox so the live SQLite file stays local.
if "%~1"=="" (
  call "%~dp0deploy.bat"
) else (
  call "%~dp0deploy.bat" "%~1"
)
exit /b %errorlevel%

:usage
echo Usage: install_from_download.bat [starting-port]
echo Installs dependencies and starts the app from this extracted repository folder.
exit /b 0
