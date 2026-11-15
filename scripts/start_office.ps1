# Run HQ on this PC (for testing, or if the Mac is down). The Mac is the normal 24/7 home: see scripts/install-mac.sh.
#   .\scripts\start_office.ps1            -> http://127.0.0.1:8810 (this PC only)
#   .\scripts\start_office.ps1 -Tailnet   -> also reachable from the phone and the Mac over Tailscale
param([switch]$Tailnet)
$env:PYTHONUTF8 = "1"
Set-Location (Split-Path $PSScriptRoot -Parent)
$bind = if ($Tailnet) { "0.0.0.0" } else { "127.0.0.1" }
python -m office serve --host $bind --port 8810
