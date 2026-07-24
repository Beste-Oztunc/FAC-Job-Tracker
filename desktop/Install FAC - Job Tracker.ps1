$ErrorActionPreference = "Stop"

$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppFolderName = "FAC - Job Tracker"

Write-Host ""
Write-Host "FAC - Job Tracker Installer"
Write-Host "=========================================="
Write-Host ""
Write-Host "A folder chooser will open so you can select the installation location."
Write-Host ""

$InstallParent = ""

try {
    Add-Type -AssemblyName System.Windows.Forms

    $Dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $Dialog.Description = "Choose where FAC - Job Tracker should be installed"
    $Dialog.ShowNewFolderButton = $true

    $Result = $Dialog.ShowDialog()

    if ($Result -eq [System.Windows.Forms.DialogResult]::OK) {
        $InstallParent = $Dialog.SelectedPath
    }
}
catch {
    Write-Host "The folder chooser could not be opened."
}

if ([string]::IsNullOrWhiteSpace($InstallParent)) {
    $InstallParent = Read-Host "Enter the full folder path, or leave blank to cancel"
}

if ([string]::IsNullOrWhiteSpace($InstallParent)) {
    Write-Host "Installation cancelled."
    exit 0
}

$TargetDir = Join-Path $InstallParent $AppFolderName

$RequiredFiles = @(
    "job_app.py",
    "job_app_runtime.py",
    "collect_all_ats_app.py",
    "job_agent_app.py",
    "companies.py",
    "requirements_app.txt",
    "README.md",
    "README_START_HERE.txt",
    "FAC_SEARCH_TIPS.txt",
    "LICENSE.txt",
    "THIRD_PARTY_NOTICES.txt",
    "CONTRIBUTING.md",
    "Start FAC - Job Tracker.command",
    "Start FAC - Job Tracker.bat"
)

$RequiredFolders = @(
    "frontend",
    "third_party_licenses"
)

$Missing = @()

foreach ($File in $RequiredFiles) {
    $Path = Join-Path $SourceDir $File

    if (-not (Test-Path $Path -PathType Leaf)) {
        $Missing += $File
    }
}

foreach ($Folder in $RequiredFolders) {
    $Path = Join-Path $SourceDir $Folder

    if (-not (Test-Path $Path -PathType Container)) {
        $Missing += $Folder
    }
}

if ($Missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Installation stopped before making changes."
    Write-Host "Keep the complete Desktop folder together and run the installer from it."
    Write-Host ""
    Write-Host "Missing:"
    $Missing | ForEach-Object { Write-Host "  $_" }
    exit 1
}

Write-Host ""
Write-Host "FAC - Job Tracker will be installed in:"
Write-Host "  $TargetDir"
Write-Host ""

if (Test-Path $TargetDir) {
    Write-Host "An existing installation was found."
    Write-Host "Application files will be updated."
    Write-Host "Your .env, .venv, output folder, caches, and saved settings will be preserved."
}
else {
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
}

$FrontendTarget = Join-Path $TargetDir "frontend"

if (Test-Path $FrontendTarget) {
    Remove-Item -Recurse -Force $FrontendTarget
}

Copy-Item -Recurse -Force `
    (Join-Path $SourceDir "frontend") `
    $FrontendTarget

$ThirdPartyTarget = Join-Path $TargetDir "third_party_licenses"

if (Test-Path $ThirdPartyTarget) {
    Remove-Item -Recurse -Force $ThirdPartyTarget
}

Copy-Item -Recurse -Force `
    (Join-Path $SourceDir "third_party_licenses") `
    $ThirdPartyTarget

foreach ($File in $RequiredFiles) {
    Copy-Item -Force `
        (Join-Path $SourceDir $File) `
        (Join-Path $TargetDir $File)
}

$EnvFile = Join-Path $TargetDir ".env"

if (-not (Test-Path $EnvFile)) {
    $EnvLines = @(
        "# FAC - Job Tracker configuration",
        "# FAC: Fully Automated Candidate",
        "# The unofficial expansion is left as an exercise for the reader.",
        "# FAC_ORIGIN=RnVjayBBbGwgQ29ycG9yYXRpb25z",
        "#",
        "# AI is optional. Leave OPENAI_API_KEY empty to run without AI analysis.",
        "# Never share a .env file containing a real API key.",
        "",
        "OPENAI_API_KEY=",
        "OPENAI_MODEL=gpt-5-mini"
    )

    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($EnvFile, $EnvLines, $Utf8NoBom)
    Write-Host "Created a new local .env configuration file."
}
else {
    Write-Host "Existing .env configuration preserved."
}

if ($env:FAC_INSTALLER_TEST_MODE -eq "1") {
    Write-Host "Installer copy test completed."
    exit 0
}

$VenvPython = Join-Path $TargetDir ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython -PathType Leaf)) {
    Write-Host "Creating a private Python environment..."

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv (Join-Path $TargetDir ".venv")
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv (Join-Path $TargetDir ".venv")
    }
    else {
        Write-Host ""
        Write-Host "Python 3 was not found."
        Write-Host 'Install Python 3 and enable "Add Python to PATH", then run this installer again.'
        exit 1
    }

    if ($LASTEXITCODE -ne 0) {
        throw "The Python environment could not be created."
    }
}

if (-not (Test-Path $VenvPython -PathType Leaf)) {
    throw "The Python environment could not be found after creation."
}

Write-Host "Installing or updating application dependencies..."
& $VenvPython -m pip install --upgrade pip | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw "pip could not be updated."
}

& $VenvPython -m pip install -r (Join-Path $TargetDir "requirements_app.txt")

if ($LASTEXITCODE -ne 0) {
    throw "Application dependencies could not be installed."
}

Write-Host ""
Write-Host "Installation complete."
Write-Host ""
Write-Host "Installed at:"
Write-Host "  $TargetDir"
Write-Host ""
Write-Host "Starting FAC - Job Tracker..."

Push-Location $TargetDir

try {
    & $VenvPython "job_app.py"
}
finally {
    Pop-Location
}
