param(
    [string]$ProjectPath = "C:\Users\user\APDL_PV_AI_Platform.worktrees\user-authentication-login"
)
$ErrorActionPreference = "Stop"
$target = Join-Path $ProjectPath "app\static\css\platform.css"
$patch = Join-Path $PSScriptRoot "platform-refresh.css"
if (!(Test-Path -LiteralPath $target)) { throw "Platform stylesheet not found: $target" }
if (!(Test-Path -LiteralPath $patch)) { throw "Extract both files together before running the installer." }
$original = [System.IO.File]::ReadAllText($target)
$addition = [System.IO.File]::ReadAllText($patch)
$pattern = '(?s)/\* APDL remaining pages refresh: start \*/.*?/\* APDL remaining pages refresh: end \*/'
$clean = [regex]::Replace($original, $pattern, '')
$backup = "$target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
Copy-Item -LiteralPath $target -Destination $backup
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($target, $clean.TrimEnd() + "`r`n`r`n" + $addition, $utf8)
Write-Host "Platform design updated. Backup: $backup"
Write-Host "Refresh the platform with Ctrl + F5."
