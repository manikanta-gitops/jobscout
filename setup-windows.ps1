# Creates a venv, installs dependencies and schedules jobscout every 30 minutes in Task Scheduler.
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) { py -3 -m venv (Join-Path $root ".venv") }  # needs Python 3.11+
& $py -m pip install -r (Join-Path $root "requirements.txt")

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Write-Host "Fill in $envFile, then run this script again."
    exit 0
}

& $py -m jobscout --test
if ($LASTEXITCODE -ne 0) { throw "Telegram test failed; check .env" }

$action = New-ScheduledTaskAction -Execute $py -Argument "-m jobscout" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "jobscout" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled task 'jobscout' registered (every 30 minutes while you are logged in)."
