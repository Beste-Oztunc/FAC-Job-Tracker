@echo off
setlocal EnableExtensions
title FAC - Job Tracker

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    goto check_dependencies
)

where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_COMMAND=py -3"
    goto create_environment_with_command
)

where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_COMMAND=python"
    goto create_environment_with_command
)

echo Python 3 was not found.
echo Install Python 3 from python.org and enable "Add Python to PATH".
pause
exit /b 1

:create_environment_with_command
echo Creating the FAC - Job Tracker Python environment...
%PYTHON_COMMAND% -m venv ".venv"

if not exist ".venv\Scripts\python.exe" (
    echo The Python environment could not be created.
    pause
    exit /b 1
)

set "PYTHON=.venv\Scripts\python.exe"

:check_dependencies
"%PYTHON%" -c "import fastapi, uvicorn, requests" >nul 2>&1

if errorlevel 1 (
    echo Installing FAC - Job Tracker dependencies...
    "%PYTHON%" -m pip install -r requirements_app.txt

    if errorlevel 1 (
        echo Dependencies could not be installed.
        pause
        exit /b 1
    )
)

"%PYTHON%" job_app.py

endlocal
