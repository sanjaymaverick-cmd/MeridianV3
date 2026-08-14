# Build a local folder app. Does not touch v1 or v2 scripts.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pip install -e ".[desktop]"
python -m PyInstaller --noconfirm --clean --name MERIDIAN-V3 `
  --add-data "meridian_v3/ui/templates;meridian_v3/ui/templates" `
  --add-data "meridian_v3/ui/static;meridian_v3/ui/static" `
  --add-data "config;config" `
  -m meridian_v3
Write-Host "Output: dist\MERIDIAN-V3\"
