param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

& $Python -m pip install -r "$PSScriptRoot\requirements.txt"
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name MengSanGuoWindowBot --distpath $ProjectRoot --workpath "$PSScriptRoot\build" --specpath "$PSScriptRoot\build" "$PSScriptRoot\main.py"
Write-Host "Built: $ProjectRoot\MengSanGuoWindowBot.exe"
