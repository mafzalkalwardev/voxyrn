param(
    [string]$Voice = "en_US-lessac-medium"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dest = Join-Path $Root "models\piper"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$Base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
foreach ($name in @("$Voice.onnx.json", "$Voice.onnx")) {
    $out = Join-Path $Dest $name
    Write-Host "Downloading $name"
    & curl.exe -L --fail --retry 3 -o $out "$Base/$name"
    if (-not (Test-Path $out) -or (Get-Item $out).Length -lt 32) {
        throw "Download failed: $out"
    }
}

Get-ChildItem $Dest -Filter "$Voice*" | Select-Object Name, Length
