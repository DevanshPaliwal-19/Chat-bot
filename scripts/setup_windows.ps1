# One-time setup for Windows PowerShell 5.1. Run from the project root.
$ErrorActionPreference = "Stop"
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example" }
Write-Host "Done. Activate with:  .\.venv\Scripts\Activate.ps1"
Write-Host "Optional voice:       pip install -r requirements-voice.txt"
