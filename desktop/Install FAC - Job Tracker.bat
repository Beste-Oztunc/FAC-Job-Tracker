@echo off
setlocal EnableExtensions
title FAC - Job Tracker Installer

powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0Install FAC - Job Tracker.ps1"

if errorlevel 1 (
    echo.
    echo FAC - Job Tracker was not installed successfully.
    pause
    exit /b 1
)

endlocal
exit /b 0
