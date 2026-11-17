@echo off
setlocal
cd /d "%~dp0"

set "PORT=8080"
if not "%~1"=="" set "PORT=%~1"

REM First use: prepare the Python environment and then start the portal.
if not exist ".venv\Scripts\uvicorn.exe" (
    echo First launch detected. Preparing the application...
    call "%~dp0deploy.bat" "%PORT%"
    exit /b %ERRORLEVEL%
)

REM Later uses: reuse the prepared environment without reinstalling packages.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_solution.ps1" -Port "%PORT%"
if errorlevel 1 (
    echo.
    echo The portal did not start. Review the message above.
    pause
    exit /b 1
)

exit /b 0
