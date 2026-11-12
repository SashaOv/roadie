<# : batch part; PowerShell reads this line as the start of a comment
@echo off
rem Set up Windows to run Roadie's tests fast and in full: turn on Developer
rem Mode, so the symlink tests run, and exclude the folders the tests use from
rem Defender's real-time scan. Run it as yourself, not elevated: it resolves
rem the folders as your account, then asks Windows once to elevate the changes.
rem It needs PowerShell 7 (pwsh).
setlocal
set "ROADIE_SETUP=%~f0"
where pwsh >nul 2>nul || (
    echo setup stopped: it needs PowerShell 7, and pwsh is not on PATH
    exit /b 1
)
pwsh -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create([IO.File]::ReadAllText($env:ROADIE_SETUP)))"
exit /b %errorlevel%
: end of batch part #>

$ErrorActionPreference = 'Stop'

function Quote([string] $text) { "'" + ($text -replace "'", "''") + "'" }

$checkout = (Resolve-Path (Join-Path (Split-Path $env:ROADIE_SETUP) '..')).Path
$paths = [System.Collections.Generic.List[string]]::new()
$paths.Add($checkout)
$paths.Add((Join-Path ([IO.Path]::GetTempPath()) "pytest-of-$env:USERNAME"))
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $paths.Add((uv cache dir))
    $paths.Add((uv python dir))
} else {
    Write-Warning 'uv is not on PATH, so its cache and Python installs stay scanned'
}
$python = Join-Path $checkout '.venv\Scripts\python.exe'
if (Test-Path $python) {
    $paths.Add((& $python -c 'import sys; print(sys.base_prefix)'))
} else {
    Write-Warning "no .venv in $checkout, so the Python it would use stays scanned; run 'uv sync' and this script again"
}
$paths = @($paths | Sort-Object -Unique)

$log = Join-Path ([IO.Path]::GetTempPath()) "roadie-setup-$PID.log"
$elevated = @"
`$ErrorActionPreference = 'Stop'
`$ProgressPreference = 'SilentlyContinue'
`$failed = `$false
& {
    try {
        `$key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock'
        if (-not (Test-Path `$key)) { New-Item `$key -Force | Out-Null }
        New-ItemProperty `$key -Name AllowDevelopmentWithoutDevLicense -Value 1 -PropertyType DWord -Force | Out-Null
        'Developer Mode is on'
    } catch { "could not turn on Developer Mode: `$_"; `$script:failed = `$true }
    try {
        Add-MpPreference -ExclusionPath $(($paths | ForEach-Object { Quote $_ }) -join ',')
        'Defender now excludes:'
        (Get-MpPreference).ExclusionPath | ForEach-Object { "  `$_" }
    } catch { "could not add Defender exclusions: `$_"; `$script:failed = `$true }
} *> $(Quote $log)
if (`$failed) { exit 1 }
"@

Write-Host 'Asking Windows to elevate: allow it to turn on Developer Mode and exclude:'
$paths | ForEach-Object { Write-Host "  $_" }
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($elevated))
try {
    $process = Start-Process pwsh -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
} catch {
    Write-Host "setup stopped: Windows did not elevate: $($_.Exception.Message)"
    exit 1
}
if (Test-Path $log) {
    Get-Content $log | Write-Host
    Remove-Item $log
} else {
    Write-Host 'the elevated shell left no report; it may run as another account without access to your TEMP'
}

if (Test-Path $python) {
    $output = & $python -c @'
import tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as scratch:
    (Path(scratch) / "link").symlink_to(Path(scratch) / "target")
'@ 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'Python can make symlinks, so the symlink tests run'
    } else {
        Write-Host 'Python cannot make symlinks yet; sign out and back in, then run this script again to check. Python said:'
        $output | Select-Object -Last 1 | ForEach-Object { Write-Host "  $_" }
    }
}
exit $process.ExitCode
