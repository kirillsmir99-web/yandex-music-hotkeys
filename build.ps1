$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }

& $Python -m tools.build_icon
if ($LASTEXITCODE -ne 0) { throw "Сборка иконки завершилась с кодом $LASTEXITCODE" }
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name YandexMusicGameOverlay `
    --icon assets\app-icon.ico `
    --version-file assets\version_info.txt `
    --add-data "assets\fonts\WixMadeforDisplay-Variable.ttf;assets\fonts" `
    --add-data "assets\fonts\WixMadeforText-Variable.ttf;assets\fonts" `
    --add-data "assets\fonts\SourGummy-Variable.ttf;assets\fonts" `
    --collect-all winsdk `
    --exclude-module numpy `
    --hidden-import pycaw `
    --hidden-import comtypes `
    yandex_music_overlay.pyw
if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с кодом $LASTEXITCODE" }
