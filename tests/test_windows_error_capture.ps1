param([string]$Python)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $root '.venv\Scripts\python.exe' }
$testDir = Join-Path $root ('.tmp\capture-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDir | Out-Null
$env:VAR_DIR = Join-Path $testDir 'var'
$env:DATABASE_URL = 'sqlite:///' + (Join-Path $env:VAR_DIR 'finops.db').Replace('\', '/')
$stdout = Join-Path $testDir 'startup.log'
$stderr = Join-Path $testDir 'startup.err'
$process = Start-Process -FilePath $Python -ArgumentList '-c "from app.main import startup; startup()"' `
    -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
if ($process.ExitCode -ne 0) { Get-Content $stderr; throw 'Fresh startup failed' }
if (-not (Select-String -LiteralPath $stdout -Pattern 'First administrator created' -Quiet)) { throw 'Startup output missing' }
$process = Start-Process -FilePath $Python -ArgumentList '-c "raise RuntimeError(''intentional-capture-test'')"' `
    -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
if ($process.ExitCode -eq 0) { throw 'Failure exit code missing' }
if (-not (Select-String -LiteralPath $stderr -Pattern 'RuntimeError: intentional-capture-test' -Quiet)) { throw 'Python traceback not captured' }
Write-Output 'PASS: fresh startup output and failed Python traceback captured under ErrorActionPreference Stop'
