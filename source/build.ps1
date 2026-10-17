param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

& $Python -m pip install -r "$PSScriptRoot\requirements.txt"
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name MengSanGuoWindowBot --add-data "$PSScriptRoot\updater_install.ps1;." --hidden-import update_ui --hidden-import updater --distpath $ProjectRoot --workpath "$PSScriptRoot\build" --specpath "$PSScriptRoot\build" "$PSScriptRoot\main.py"
if ($LASTEXITCODE -ne 0) { throw "打包失败" }
Write-Host "Built: $ProjectRoot\MengSanGuoWindowBot.exe"
