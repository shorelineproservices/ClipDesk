# Clock this PC's employees in. They connect to HQ on the Mac (OFFICE_URL in .env), run their jobs with this PC's
# Ollama, and report back. Close the window to clock them out; tasks they were holding go back to the queue after an hour.
#   .\scripts\start_worker.ps1                 -> HQ from .env (default the Mac)
#   .\scripts\start_worker.ps1 -Local          -> HQ on this PC (http://127.0.0.1:8810)
param([switch]$Local)
$env:PYTHONUTF8 = "1"
$env:OFFICE_MACHINE = "pc"
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($Local) { $env:OFFICE_URL = "http://127.0.0.1:8810" }
python -m office work --machine pc
