param(
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Url = "http://127.0.0.1:$Port/"

Write-Host "Starting Voxyrn operator console..."
Write-Host "Project: $Root"
Write-Host "Backend: python -m src.web_app --port $Port"
Write-Host "Open:    $Url"

# Free the port if a previous console is still bound.
try {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn -and $conn.OwningProcess) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
} catch {
    # Ignore port-cleanup failures; uvicorn will report bind errors clearly.
}

Start-Process -FilePath $Python -ArgumentList @("-m", "src.web_app", "--port", "$Port") -WorkingDirectory $Root

if (-not $NoBrowser) {
    Start-Sleep -Seconds 3
    Start-Process $Url
}
