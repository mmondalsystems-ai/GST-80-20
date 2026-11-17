<#
    Finance Operations Portal - start it on this machine for a demo.

    Double-click demo.bat, or from PowerShell:

        .\demo.ps1              set up if needed, then start and open the browser
        .\demo.ps1 -Seed        the same, but with July 2026 already calculated
        .\demo.ps1 -Reset       throw away the demo database and start fresh
        .\demo.ps1 -Port 9000   use a different port

    No administrator rights, no database server, nothing installed system-wide.
    Everything lives in this folder and is removed when you delete it.
#>

[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$Seed,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppDir

function Say  ($m) { Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $m }
function Fail ($m) {
    Write-Host ""
    Write-Host "Cannot start: " -ForegroundColor Red -NoNewline
    Write-Host $m
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host ""
Write-Host "Finance Operations Portal - demo" -ForegroundColor White
Write-Host ""

# ------------------------------------------------------------------ python
$py = $null
foreach ($cand in @('py -3.13', 'py -3.12', 'py -3.11', 'python', 'python3')) {
    $exe, $arg = $cand -split ' ', 2
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $ok = if ($arg) { & $exe $arg -c 'import sys;print(sys.version_info>=(3,11))' 2>$null }
              else      { & $exe      -c 'import sys;print(sys.version_info>=(3,11))' 2>$null }
        if ($ok -eq 'True') { $py = @($exe, $arg); break }
    } catch { }
}
if (-not $py) {
    Fail @"
Python 3.11 or newer is needed and was not found.

    Install it from https://www.python.org/downloads/windows/
    Tick "Add python.exe to PATH" during setup, then run this again.
"@
}
$pyExe, $pyArg = $py

# ------------------------------------------------------------------- reset
if ($Reset) {
    Say "Clearing the demo database"
    Remove-Item -Recurse -Force (Join-Path $AppDir 'var') -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- one-time
$venvPy  = Join-Path $AppDir '.venv\Scripts\python.exe'
$uvicorn = Join-Path $AppDir '.venv\Scripts\uvicorn.exe'
if (-not (Test-Path $venvPy)) {
    Say "First run - setting up (about a minute)"
    if ($pyArg) { & $pyExe $pyArg -m venv (Join-Path $AppDir '.venv') }
    else        { & $pyExe        -m venv (Join-Path $AppDir '.venv') }
    if (-not (Test-Path $venvPy)) { Fail "the Python environment could not be created." }

    $wheelhouse = Join-Path $AppDir 'wheelhouse'
    $reqs       = Join-Path $AppDir 'requirements.txt'
    if ((Test-Path $wheelhouse) -and (Get-ChildItem $wheelhouse -ErrorAction SilentlyContinue)) {
        & $venvPy -m pip install -q --no-index --find-links $wheelhouse -r $reqs
        if ($LASTEXITCODE -ne 0) { Fail "the offline bundle does not match this machine." }
    } else {
        & $venvPy -m pip install -q --upgrade pip 2>$null | Out-Null
        & $venvPy -m pip install -q -r $reqs
        if ($LASTEXITCODE -ne 0) {
            Fail "could not download the dependencies. Check this machine's internet connection."
        }
    }
} else {
    Say "Environment ready"
}

# A demo uses a local file for its database, so there is nothing to install.
$envFile = Join-Path $AppDir '.env'
if (-not (Test-Path $envFile)) {
    Set-Content -Path $envFile -Encoding UTF8 -Value @(
        '# Demo configuration. Uses a local file for the database.',
        'ORG_NAME=Oswal Group',
        'BOOTSTRAP_ADMIN_EMAIL=admin@oswalgroup.net',
        'BOOTSTRAP_ADMIN_PASSWORD=demo1234'
    )
}

# ------------------------------------------------------------------- seed
if ($Seed) {
    Say "Loading July 2026 so the portal opens with figures already in it"
    & $venvPy (Join-Path $AppDir 'seed_demo.py')
    if ($LASTEXITCODE -ne 0) { Fail "the sample month could not be loaded. See the message above." }
} else {
    & $venvPy -c "from app.main import startup; startup()" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "the application could not start. Try:  .\demo.ps1 -Reset" }
}

# ------------------------------------------------------------------ start
$url = "http://127.0.0.1:$Port"

Write-Host ""
Write-Host ("-" * 60) -ForegroundColor Green
Write-Host "  Open     " -NoNewline -ForegroundColor White; Write-Host $url
Write-Host "  Sign in  " -NoNewline -ForegroundColor White
Write-Host "admin@oswalgroup.net  /  demo1234"
if ($Seed) {
    Write-Host "  Ready    July 2026 is already loaded" -ForegroundColor DarkGray
} else {
    Write-Host "  To demo  GST 80:20 -> New calculation -> upload the three files" -ForegroundColor DarkGray
    Write-Host "           in demo-inputs\" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "  Press Ctrl+C in this window to stop." -ForegroundColor DarkGray
Write-Host ("-" * 60) -ForegroundColor Green
Write-Host ""

# Open the browser once the server is actually answering.
Start-Job -ScriptBlock {
    param($u)
    foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-WebRequest "$u/healthz" -UseBasicParsing -TimeoutSec 1 | Out-Null
            Start-Process $u
            break
        } catch { }
    }
} -ArgumentList $url | Out-Null

& $uvicorn app.main:app --host 127.0.0.1 --port $Port --log-level warning
