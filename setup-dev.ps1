$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Launcher was not found. Install Python 3.11 from python.org.'
}

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.11 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed with exit code $LASTEXITCODE" }

Write-Output 'Ready. Start the application with: .\run-dev.ps1'
