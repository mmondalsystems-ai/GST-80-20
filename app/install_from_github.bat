@echo off
REM First-time setup from GitHub. Git Credential Manager supports private repos.
REM Installs outside synced folders so the live SQLite database stays local.
setlocal
if /I "%~1"=="/?" goto usage
if /I "%~1"=="--help" goto usage
if not "%~2"=="" goto usage

set "GST8020_APP=%LOCALAPPDATA%\GST-80-20"
if exist "%GST8020_APP%\deploy.bat" goto launch
if exist "%GST8020_APP%" (
  echo The target folder already exists but is not a complete GST 80-20 installation:
  echo   %GST8020_APP%
  echo It has not been overwritten. Inspect that folder before retrying.
  pause
  exit /b 1
)

set "GIT_EXE="
for /f "delims=" %%G in ('where git.exe 2^>nul') do if not defined GIT_EXE set "GIT_EXE=%%G"
if not defined GIT_EXE (
  where winget.exe >nul 2>&1
  if errorlevel 1 (
    echo Git is not installed and winget is unavailable.
    echo Install Git for Windows, or use install_from_download.bat with a full extracted copy.
    pause
    exit /b 1
  )
  echo Installing Git for Windows...
  winget install --id Git.Git --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo Git installation failed. Install Git for Windows manually and retry.
    pause
    exit /b 1
  )
  if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT_EXE=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
  if not defined GIT_EXE if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT_EXE=%ProgramFiles%\Git\cmd\git.exe"
  if not defined GIT_EXE for /f "delims=" %%G in ('where git.exe 2^>nul') do if not defined GIT_EXE set "GIT_EXE=%%G"
  if not defined GIT_EXE (
    echo Git installed, but its executable was not found. Open a new Command Prompt and retry.
    pause
    exit /b 1
  )
)

echo Cloning GST 80-20 into:
echo   %GST8020_APP%
echo If prompted, sign in with a GitHub account that can access the repository.
"%GIT_EXE%" clone --depth 1 "https://github.com/ChiranjibSarma/GST-80-20.git" "%GST8020_APP%"
if errorlevel 1 (
  echo GitHub clone failed. Check network access and repository permissions.
  echo No existing app or database was overwritten.
  pause
  exit /b 1
)
if not exist "%GST8020_APP%\deploy.bat" (
  echo The cloned repository does not contain deploy.bat at its root.
  pause
  exit /b 1
)

:launch
if "%~1"=="" (
  call "%GST8020_APP%\deploy.bat"
) else (
  call "%GST8020_APP%\deploy.bat" "%~1"
)
exit /b %errorlevel%

:usage
echo Usage: install_from_github.bat [starting-port]
echo Clones the GitHub repo on first use, then runs its deployment BAT.
echo GitHub sign-in may be required. Existing installations are reused, not updated.
exit /b 0
