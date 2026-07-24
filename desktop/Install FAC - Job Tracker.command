#!/bin/bash

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_FOLDER_NAME="FAC - Job Tracker"

choose_install_parent() {
    if ! command -v osascript >/dev/null 2>&1; then
        return 0
    fi

    osascript <<'APPLESCRIPT'
try
    set chosenFolder to choose folder with prompt "Choose where FAC - Job Tracker should be installed:"
    return POSIX path of chosenFolder
on error number -128
    return ""
end try
APPLESCRIPT
}

echo
echo "FAC - Job Tracker Installer"
echo "=========================================="
echo
echo "A folder chooser will open so you can select the installation location."
echo

INSTALL_PARENT="$(choose_install_parent || true)"

if [ -z "$INSTALL_PARENT" ]; then
    echo "No folder was returned by the chooser."
    read -r -p "Enter the full folder path, or leave blank to cancel: " INSTALL_PARENT
fi

if [ -z "$INSTALL_PARENT" ]; then
    echo "Installation cancelled."
    exit 0
fi

TARGET_DIR="${INSTALL_PARENT%/}/$APP_FOLDER_NAME"

REQUIRED_FILES=(
    "job_app.py"
    "job_app_runtime.py"
    "collect_all_ats_app.py"
    "job_agent_app.py"
    "companies.py"
    "requirements_app.txt"
    "README.md"
    "README_START_HERE.txt"
    "FAC_SEARCH_TIPS.txt"
    "LICENSE.txt"
    "THIRD_PARTY_NOTICES.txt"
    "CONTRIBUTING.md"
    "Start FAC - Job Tracker.command"
    "Start FAC - Job Tracker.bat"
)

REQUIRED_FOLDERS=(
    "frontend"
    "third_party_licenses"
)

MISSING=0

for FILE in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$SOURCE_DIR/$FILE" ]; then
        echo "Missing required file: $FILE"
        MISSING=1
    fi
done

for FOLDER in "${REQUIRED_FOLDERS[@]}"; do
    if [ ! -d "$SOURCE_DIR/$FOLDER" ]; then
        echo "Missing required folder: $FOLDER"
        MISSING=1
    fi
done

if [ "$MISSING" -ne 0 ]; then
    echo
    echo "Installation stopped before making changes."
    echo "Keep the complete Desktop folder together and run the installer from it."
    read -r -p "Press Enter to close."
    exit 1
fi

echo
echo "FAC - Job Tracker will be installed in:"
echo "  $TARGET_DIR"
echo

if [ -d "$TARGET_DIR" ]; then
    echo "An existing installation was found."
    echo "Application files will be updated."
    echo "Your .env, .venv, output folder, caches, and saved settings will be preserved."
else
    mkdir -p "$TARGET_DIR"
fi

rm -rf "$TARGET_DIR/frontend"
cp -R "$SOURCE_DIR/frontend" "$TARGET_DIR/frontend"

rm -rf "$TARGET_DIR/third_party_licenses"
cp -R \
    "$SOURCE_DIR/third_party_licenses" \
    "$TARGET_DIR/third_party_licenses"

for FILE in "${REQUIRED_FILES[@]}"; do
    cp "$SOURCE_DIR/$FILE" "$TARGET_DIR/$FILE"
done

chmod +x "$TARGET_DIR/Start FAC - Job Tracker.command"

if [ ! -f "$TARGET_DIR/.env" ]; then
    cat > "$TARGET_DIR/.env" <<'ENVFILE'
# FAC - Job Tracker configuration
# FAC: Fully Automated Candidate
# The unofficial expansion is left as an exercise for the reader.
# FAC_ORIGIN=RnVjayBBbGwgQ29ycG9yYXRpb25z
#
# AI is optional. Leave OPENAI_API_KEY empty to run without AI analysis.
# Never share a .env file containing a real API key.

OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
ENVFILE
    chmod 600 "$TARGET_DIR/.env"
    echo "Created a new local .env configuration file."
else
    echo "Existing .env configuration preserved."
fi

if [ "${FAC_INSTALLER_TEST_MODE:-0}" = "1" ]; then
    echo "Installer copy test completed."
    exit 0
fi

if [ ! -x "$TARGET_DIR/.venv/bin/python" ]; then
    if command -v python3 >/dev/null 2>&1; then
        SYSTEM_PYTHON="python3"
    elif command -v python >/dev/null 2>&1; then
        SYSTEM_PYTHON="python"
    else
        echo
        echo "Python 3 was not found."
        echo "Install Python 3, then run this installer again."
        read -r -p "Press Enter to close."
        exit 1
    fi

    echo "Creating a private Python environment..."
    "$SYSTEM_PYTHON" -m venv "$TARGET_DIR/.venv"
fi

PYTHON="$TARGET_DIR/.venv/bin/python"

echo "Installing or updating application dependencies..."
"$PYTHON" -m pip install --upgrade pip >/dev/null
"$PYTHON" -m pip install -r "$TARGET_DIR/requirements_app.txt"

echo
echo "Installation complete."
echo
echo "Installed at:"
echo "  $TARGET_DIR"
echo
echo "Starting FAC - Job Tracker..."

cd "$TARGET_DIR"
"$PYTHON" job_app.py
