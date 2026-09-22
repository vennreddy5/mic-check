# Mic Check - run this each time you want to use the app.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$envFile = Join-Path $backendDir ".env"
$appFile = Join-Path $backendDir "app.py"

if (-not (Test-Path $envFile) -or -not (Test-Path $venvPython)) {
    Write-Error "Setup not found. Run .\install.ps1 first."
    exit 1
}

Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Invoke-WebRequest "http://localhost:5000" -UseBasicParsing -TimeoutSec 1 | Out-Null
            Start-Process "http://localhost:5000"
            return
        } catch { Start-Sleep -Seconds 1 }
    }
} | Out-Null

Write-Host "Starting Mic Check (loading dependencies, this can take ~10-15 seconds) ..."
Write-Host "Browser will open automatically once the server is ready."
& $venvPython $appFile
