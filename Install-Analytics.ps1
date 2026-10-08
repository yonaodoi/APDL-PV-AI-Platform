param(
    [string]$ProjectPath = "C:\Users\user\Desktop\APDL_PV_AI_Platform"
)
$ErrorActionPreference = "Stop"
$relative = "app\templates\analytics\analytics_home.html"
$target = Join-Path $ProjectPath $relative
$source = Join-Path $PSScriptRoot $relative
if (!(Test-Path $target)) { throw "Analytics template was not found at $target" }
if (!(Test-Path $source)) { throw "Extract the whole ZIP before running this script." }
$backup = "$target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
Copy-Item -LiteralPath $target -Destination $backup
Copy-Item -LiteralPath $source -Destination $target -Force
Write-Host "Analytics cards updated. Original saved to $backup"
Write-Host "Restart Flask, then refresh the Analytics page."
