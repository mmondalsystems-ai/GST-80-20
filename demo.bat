@echo off
REM Finance Operations Portal - demo launcher for Windows.
REM Double-click this file. It runs demo.ps1 without changing any
REM PowerShell execution policy on this machine.
REM
REM   demo.bat            start the portal and open the browser
REM   demo.bat -Seed      start with July 2026 already calculated
REM   demo.bat -Reset     throw away the demo database and start fresh

setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo.ps1" %*
if errorlevel 1 (
  echo.
  echo The demo stopped with an error. The message above says why.
  pause
)
endlocal
