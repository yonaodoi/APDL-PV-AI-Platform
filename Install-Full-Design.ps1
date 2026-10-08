param([string]$ProjectPath = "C:\Users\user\APDL_PV_AI_Platform.worktrees\user-authentication-login")
$ErrorActionPreference = "Stop"
$pythonPath = Join-Path $ProjectPath ".venv\Scripts\python.exe"
$scriptPath = Join-Path $PSScriptRoot "update_design.py"
if (!(Test-Path -LiteralPath $pythonPath)) { throw "Project Python not found: $pythonPath" }
& $pythonPath $scriptPath --project $ProjectPath
if ($LASTEXITCODE -ne 0) { throw "Design update did not finish. Check the error above." }
