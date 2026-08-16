$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw 'The environment is not configured. Run .\setup-dev.ps1 first.'
}

Start-Process -FilePath $Pythonw -ArgumentList (Join-Path $ProjectRoot 'yandex_music_overlay.pyw') -WorkingDirectory $ProjectRoot
