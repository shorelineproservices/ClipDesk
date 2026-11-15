# Copy the project to the Mac (~/ClipDesk) over SSH and (re)install the services there.
# Skips data/ (the Mac keeps its own ledger), caches and git. Copies .env so both machines share the token.
#   .\scripts\push-to-mac.ps1            push + install
#   .\scripts\push-to-mac.ps1 -NoInstall push only
# Goes through a temp archive + scp: PowerShell mangles binary data in a pipe, so `tar | ssh` cannot be used here.
param([switch]$NoInstall, [string]$MacHost = "homebase")
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$tgz = Join-Path $env:TEMP "clipdesk-push.tgz"
if (Test-Path $tgz) { Remove-Item $tgz -Force }
& tar -czf $tgz --exclude=./data --exclude=__pycache__ --exclude=*.pyc --exclude=.git -C $root .
if ($LASTEXITCODE -ne 0) { Write-Error "tar failed"; exit 1 }
& scp -q $tgz "${MacHost}:~/clipdesk-push.tgz"
if ($LASTEXITCODE -ne 0) { Write-Error "scp failed"; exit 1 }
# Stage into .incoming, then copy over the live tree (never unpack straight over a running service's files).
& ssh $MacHost "set -e; mkdir -p ~/ClipDesk/.incoming; tar -xzf ~/clipdesk-push.tgz -C ~/ClipDesk/.incoming; cd ~/ClipDesk; cp -a .incoming/. .; rm -rf .incoming ~/clipdesk-push.tgz; chmod +x scripts/*.sh; echo 'copied to ~/ClipDesk'"
if ($LASTEXITCODE -ne 0) { Write-Error "push failed"; exit 1 }
Remove-Item $tgz -Force
if (-not $NoInstall) { & ssh $MacHost "bash ~/ClipDesk/scripts/install-mac.sh" }
