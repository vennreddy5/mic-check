# Mic Check - one-time setup. Safe to re-run (e.g. after a git pull).

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $repoRoot "backend"
$venvDir = Join-Path $backendDir ".venv"
$envFile = Join-Path $backendDir ".env"
$requirementsFile = Join-Path $backendDir "requirements.txt"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python was not found on PATH. Install Python 3.11+ from python.org and re-run this script."
    exit 1
}

if (-not (Test-Path $venvDir)) {
    Write-Host "Creating virtual environment at backend\.venv ..."
    python -m venv $venvDir
} else {
    Write-Host "Virtual environment already exists, skipping creation."
}

$venvPython = Join-Path $venvDir "Scripts\python.exe"

Write-Host "Upgrading pip..."
& $venvPython -m pip install --upgrade pip | Out-Null

Write-Host "Installing dependencies from backend\requirements.txt ..."
& $venvPython -m pip install -r $requirementsFile

if (-not (Test-Path $envFile)) {
    Write-Host ""
    $apiKey = Read-Host "Paste your Anthropic API key"
    # ascii (no BOM) - Set-Content's utf8 encoding writes a BOM that python-dotenv can't parse
    "ANTHROPIC_API_KEY=$apiKey" | Set-Content -Path $envFile -Encoding ascii
    Write-Host "Saved key to backend\.env"
} else {
    Write-Host "backend\.env already exists, keeping your existing API key."
}

Write-Host ""
Write-Host "Setup complete. Run .\start.ps1 to launch Mic Check."
