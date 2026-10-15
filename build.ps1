$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    py -3.14 -m PyInstaller --noconfirm --clean --windowed --onefile --name "PC Booster" --icon "assets\pc_booster.ico" --hidden-import psutil._pswindows --hidden-import pystray._win32 --hidden-import PIL.IcoImagePlugin --add-data "assets\dashboard_hero_small.png;assets" --add-data "assets\pc_booster.ico;assets" app.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    Write-Host "Created: $projectRoot\dist\PC Booster.exe"
}
finally {
    Pop-Location
}
