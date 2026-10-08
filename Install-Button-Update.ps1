param([string]$ProjectPath = "C:\Users\user\APDL_PV_AI_Platform.worktrees\user-authentication-login")
$ErrorActionPreference = "Stop"
$pythonPath = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (!(Test-Path $pythonPath)) { throw "Python not found: $pythonPath" }
& $pythonPath (Join-Path $PSScriptRoot "install_button_update.py") --project $ProjectPath
if ($LASTEXITCODE -ne 0) { throw "Update failed. Read the error above." }
