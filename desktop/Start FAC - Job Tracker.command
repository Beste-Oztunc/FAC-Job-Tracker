#!/bin/bash

set -e

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "Python 3 was not found."
    read -r -p "Press Enter to close."
    exit 1
fi

if ! "$PYTHON" -c "import fastapi, uvicorn, requests" >/dev/null 2>&1; then
    echo "Installing FAC - Job Tracker dependencies..."
    "$PYTHON" -m pip install -r requirements_app.txt
fi

"$PYTHON" job_app.py
