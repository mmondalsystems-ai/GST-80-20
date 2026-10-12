<#
    Finance Operations Portal - installer for Windows Server.

    Right-click and choose "Run with PowerShell", or from an elevated prompt:

        .\install.ps1                  install and register a startup task
        .\install.ps1 -NoService       install only, start it by hand
        .\install.ps1 -Sqlite          skip PostgreSQL, use a local file
        .\install.ps1 -Port 9000       serve on a different port
        .\install.ps1 -Uninstall       remove the startup task (keeps data)

    Safe to run more than once: it upgrades an existing install in place and
    never touches the database contents.
#>

[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$Sqlite,
    [switch]$NoService,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "This installer needs Windows PowerShell 5.1 or newer." -ForegroundColor Red
    Write-Host "Windows Server 2016 and later already have it." -ForegroundColor Red
    exit 1
}

$AppDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = 'FinanceOperationsPortal'
$DbName   = 'finops'
$DbUser   = 'finops'

function Step($m) { Write-Host ""; Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $m -ForegroundColor White }
function Info($m) { Write-Host "    $m" }
function Warn($m) { Write-Host "    ! $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host ""; Write-Host "Installation stopped: " -ForegroundColor Red -NoNewline; Write-Host $m; Write-Host ""; exit 1 }

# ------------------------------------------------------------- uninstall ---
if ($Uninstall) {
    Step "Removing the startup task"
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask   -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Info "Task removed."
    } else {
        Info "No task was installed."
    }
    Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$AppDir*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Info "Your data has been left alone: the database, $AppDir\var and .env are untouched."
    exit 0
}

Write-Host ""
Write-Host "Finance Operations Portal - installer" -ForegroundColor White
Write-Host "Installing into $AppDir" -ForegroundColor DarkGray

# ---------------------------------------------------------------- python ---
Step "Checking Python"
$py = $null
if ($env:GST8020_PYTHON) {
    $requestedPython = $env:GST8020_PYTHON.Trim('"')
    if (-not (Test-Path -LiteralPath $requestedPython -PathType Leaf)) {
        Fail "GST8020_PYTHON points to a missing file: $requestedPython"
    }
    try {
        $check = & $requestedPython -c 'import sys;print((3,11)<=sys.version_info<(3,14) and sys.maxsize>2**32)' 2>$null
        if ($check -eq 'True') { $py = @($requestedPython, '') }
    } catch { }
    if (-not $py) { Fail 'GST8020_PYTHON must point to working 64-bit Python 3.11-3.13.' }
}
foreach ($cand in $(if ($py) { @() } else { @('py -3.12', 'py -3.13', 'py -3.11', 'py -3', 'python', 'python3') })) {
    $exe, $arg = $cand -split ' ', 2
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $check = if ($arg) { & $exe $arg -c 'import sys;print((3,11)<=sys.version_info<(3,14) and sys.maxsize>2**32)' 2>$null }
                 else      { & $exe    -c 'import sys;print((3,11)<=sys.version_info<(3,14) and sys.maxsize>2**32)' 2>$null }
        if ($check -eq 'True') { $py = @($exe, $arg); break }
    } catch { }
}
if (-not $py) {
    Fail @"
Supported Python 3.11-3.13 is required and was not found. Python 3.12 is recommended.

    Install it from https://www.python.org/downloads/windows/
    Tick "Add python.exe to PATH" during setup, then run this script again.
"@
}
$pyExe, $pyArg = $py
$pyVer = if ($pyArg) { & $pyExe $pyArg --version } else { & $pyExe --version }
Info "Using $pyVer"

# ------------------------------------------------------------ virtualenv ---
Step "Setting up the Python environment"
$venvPy = Join-Path $AppDir '.venv\Scripts\python.exe'
$venvDir = Join-Path $AppDir '.venv'
if (Test-Path -LiteralPath $venvDir) {
    $venvSupported = $false
    try { $venvSupported = ((& $venvPy -c 'import sys;print((3,11)<=sys.version_info<(3,14) and sys.maxsize>2**32)' 2>$null) -eq 'True') }
    catch { }
    if (-not $venvSupported) {
        $archivedVenv = Join-Path $AppDir ('.venv-previous-' + [guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $venvDir -Destination $archivedVenv
        Info "Archived unsupported/broken Python environment at $archivedVenv; database and .env are unchanged."
    }
}
if (-not (Test-Path $venvPy)) {
    if ($pyArg) { & $pyExe $pyArg -m venv (Join-Path $AppDir '.venv') }
    else        { & $pyExe        -m venv (Join-Path $AppDir '.venv') }
    Info "Created .venv"
} else {
    Info "Reusing the existing .venv"
}
if (-not (Test-Path $venvPy)) { Fail "the Python environment could not be created in $AppDir\.venv" }

Step "Installing dependencies"
$wheelhouse = Join-Path $AppDir 'wheelhouse'
$reqs       = Join-Path $AppDir 'requirements.txt'
# Windows PowerShell 5.1 turns native stderr into a terminating error when
# ErrorActionPreference is Stop. Let pip report its own exit code instead.
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
if ((Test-Path $wheelhouse) -and (Get-ChildItem $wheelhouse -ErrorAction SilentlyContinue)) {
    Info "Found wheelhouse\ - installing without touching the internet"
    & $venvPy -m pip install --disable-pip-version-check --quiet --no-index --find-links $wheelhouse -r $reqs
    $pipExit = $LASTEXITCODE
    if ($pipExit -ne 0) {
        Fail @"
the offline bundle in wheelhouse\ does not cover this machine.
    It must be built on the same operating system and Python version as this server.
    See 'Air-gapped servers' in DEPLOYMENT.md.
"@
    }
} else {
    & $venvPy -m pip install --disable-pip-version-check --quiet -r $reqs
    $pipExit = $LASTEXITCODE
    if ($pipExit -ne 0) {
        Fail @"
could not download the dependencies.
    If this server has no internet access, build an offline bundle on a machine that does,
    copy the whole folder across, and run this installer again.
    See 'Air-gapped servers' in DEPLOYMENT.md.
"@
    }
}
} finally {
    $ErrorActionPreference = $previousErrorAction
}
Info "Dependencies installed"

# -------------------------------------------------------------- database ---
# An existing .env is the authority. Re-running the installer must not reset a
# database password that the current .env still refers to.
$envFile = Join-Path $AppDir '.env'
$dbUrl   = ''
$existingDbUrl = ''
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern '^DATABASE_URL=(.*)$' -ErrorAction SilentlyContinue |
            Select-Object -Last 1
    if ($line) { $existingDbUrl = $line.Matches[0].Groups[1].Value.Trim() }
}

if ($existingDbUrl) {
    Step "Using the database already configured in .env"
    Info ($existingDbUrl -replace '.*@', '')
    Info "Delete DATABASE_URL from .env if you want the installer to set one up again."
}
elseif (-not $Sqlite) {
    Step "Setting up PostgreSQL"
    $psqlCmd = Get-Command psql -ErrorAction SilentlyContinue
    if (-not $psqlCmd) {
        $found = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue |
                 Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { $psqlCmd = $found }
    }
    if ($psqlCmd) {
        $psql   = if ($psqlCmd.Source) { $psqlCmd.Source } else { $psqlCmd.FullName }
        $dbPass = & $venvPy -c 'import secrets;print(secrets.token_urlsafe(24))'
        Info "Found $psql"
        Write-Host ""
        Write-Host "    Enter the PostgreSQL superuser (postgres) password when prompted." -ForegroundColor DarkGray
        Write-Host "    Press Enter with no password to skip and use SQLite instead." -ForegroundColor DarkGray
        $sec = Read-Host "    postgres password" -AsSecureString
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                 [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        if ($plain) {
            $env:PGPASSWORD = $plain
            $sql = @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$DbUser') THEN
    CREATE ROLE $DbUser LOGIN PASSWORD '$dbPass';
  ELSE
    ALTER ROLE $DbUser WITH LOGIN PASSWORD '$dbPass';
  END IF;
END `$`$;
"@
            $sql | & $psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $exists = (& $psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DbName'" 2>$null).Trim()
                if ($exists -ne '1') {
                    & $psql -U postgres -d postgres -q -c "CREATE DATABASE $DbName OWNER $DbUser" 2>&1 | Out-Null
                    Info "Created the '$DbName' database"
                } else {
                    Info "Using the existing '$DbName' database"
                }
                # Make sure the role can create and read its own tables, including
                # when the database already existed under another owner.
                & $psql -U postgres -d postgres -q -c "ALTER DATABASE $DbName OWNER TO $DbUser" 2>&1 | Out-Null
                $adopt = @"
ALTER SCHEMA public OWNER TO $DbUser;
GRANT ALL ON SCHEMA public TO $DbUser;
DO `$`$ DECLARE r record; BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO $DbUser', r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname='public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO $DbUser', r.sequencename);
  END LOOP;
END `$`$;
"@
                $adopt | & $psql -U postgres -d $DbName -q 2>&1 | Out-Null
                $canCreate = (& $psql -U postgres -d $DbName -tAc "SELECT has_schema_privilege('$DbUser','public','CREATE')" 2>$null).Trim()
                if ($canCreate -eq 't') {
                    $dbUrl = "postgresql+psycopg2://${DbUser}:${dbPass}@localhost:5432/${DbName}"
                } else {
                    Warn "The '$DbUser' role cannot create tables in '$DbName'."
                    Info "A database administrator needs to run:  GRANT ALL ON SCHEMA public TO $DbUser;"
                    Info "Falling back to SQLite so the portal works in the meantime."
                }
            } else {
                Warn "That password was not accepted by PostgreSQL."
                Info "Falling back to a local SQLite file so the portal works now."
            }
            Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
        } else {
            Info "Skipped. Using a local SQLite file."
        }
    } else {
        Warn "PostgreSQL was not found on this machine."
        Info "Falling back to a local SQLite file, which suits a pilot or a single preparer."
        Info "For several people at once, install PostgreSQL and set DATABASE_URL in .env."
    }
} else {
    Step "Using SQLite as requested"
}

# ------------------------------------------------------------------ .env ---
Step "Writing configuration"
if (Test-Path $envFile) {
    Copy-Item $envFile "$envFile.backup" -Force
    Info "Kept your existing .env (a copy is at .env.backup)"
} else {
    $secret = & $venvPy -c 'import secrets;print(secrets.token_urlsafe(48))'
    $lines = @("# Written by install.ps1 on $(Get-Date -Format 'yyyy-MM-dd HH:mm'). Safe to edit.")
    if ($dbUrl) { $lines += "DATABASE_URL=$dbUrl" }
    else        { $lines += "# No DATABASE_URL set, so the portal uses var\finops.db (SQLite)." }
    $lines += @(
        "SECRET_KEY=$secret",
        "SESSION_HTTPS_ONLY=0        # set to 1 once the site is served over HTTPS",
        "ORG_NAME=Oswal Group",
        "BOOTSTRAP_ADMIN_EMAIL=admin@oswalgroup.net",
        "BOOTSTRAP_ADMIN_PASSWORD=admin123456789 # temporary; change after first login"
    )
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
    Info "Created .env"
}

# ------------------------------------------------------------ first run ----
Push-Location $AppDir
try {
    $databaseKind = & $venvPy -m app.deploy_check database-kind
    if ($LASTEXITCODE -ne 0) { Fail 'could not determine the configured database type' }
    if ($databaseKind -eq 'postgresql') {
        Step 'Installing optional PostgreSQL driver'
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $driverArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '--only-binary=psycopg2-binary', '-r', (Join-Path $AppDir 'requirements-postgres.txt'))
            if ((Test-Path $wheelhouse) -and (Get-ChildItem $wheelhouse -ErrorAction SilentlyContinue)) {
                $driverArgs += @('--no-index', '--find-links', $wheelhouse)
            }
            & $venvPy @driverArgs
            $driverExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousErrorAction }
        if ($driverExit -ne 0) { Fail 'No compatible PostgreSQL binary driver could be installed. Check Python/platform and package access, or use local SQLite. Offline PostgreSQL bundles must include requirements-postgres.txt dependencies.' }
    } else {
        Info 'SQLite: PostgreSQL driver installation skipped.'
    }
} finally { Pop-Location }

Step "Preparing the database and the first administrator"
Push-Location $AppDir
$varDir = Join-Path $AppDir 'var'
New-Item -ItemType Directory -Force -Path $varDir | Out-Null
$log = Join-Path $varDir 'install-first-run.log'
try {
    $firstRun = Start-Process -FilePath $venvPy -ArgumentList '-c "from app.main import startup; startup()"' `
        -WorkingDirectory $AppDir -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $rc = $firstRun.ExitCode
} finally { Pop-Location }
if ($rc -ne 0) {
    Write-Host ""
    Get-Content $log, "$log.err" -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Fail "the database could not be prepared. Review $log and $log.err for the full Python error."
}
Select-String -Path $log -Pattern 'Email:|Password:|Database:' |
    ForEach-Object { Info ($_.Line.Trim()) }
$installationId = & $venvPy -c 'from app.license import installation_id; print(installation_id())'
if ($LASTEXITCODE -ne 0) { Fail "could not create the offline licence installation ID" }
Info "Offline licence installation ID: $installationId"

# ------------------------------------------------------------ smoke test ---
Step "Checking that it serves"
$uvicorn = Join-Path $AppDir '.venv\Scripts\uvicorn.exe'
$smokeLog = Join-Path $varDir 'install-smoke.log'
$proc = Start-Process -FilePath $uvicorn `
        -ArgumentList "app.main:app --host 127.0.0.1 --port $Port" `
        -WorkingDirectory $AppDir -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $smokeLog -RedirectStandardError "$smokeLog.err"
$ok = $false
foreach ($i in 1..40) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 2
        if ($r.Content.Trim() -eq 'ok') { $ok = $true; break }
    } catch { }
}
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
if (-not $ok) {
    Write-Host ""
    Get-Content $smokeLog, "$smokeLog.err" -Tail 10 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Fail "the portal did not answer on port $Port. The output above says why."
}
Info "Answered on port $Port"

# -------------------------------------------------------------- service ----
$started = $false
if (-not $NoService) {
    Step "Registering the startup task"
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
               ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Warn "Not running as Administrator, so nothing was registered."
        Info "Re-run from an elevated PowerShell to have it start with the machine."
    } else {
        try {
            $action  = New-ScheduledTaskAction -Execute $uvicorn `
                       -Argument "app.main:app --host 0.0.0.0 --port $Port --workers 4 --proxy-headers" `
                       -WorkingDirectory $AppDir
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                         -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                         -ExecutionTimeLimit ([TimeSpan]::Zero)
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings -Force | Out-Null
            Start-ScheduledTask -TaskName $TaskName
            Start-Sleep -Seconds 3
            try {
                $r = Invoke-WebRequest "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 3
                if ($r.Content.Trim() -eq 'ok') { $started = $true }
            } catch { }
            if ($started) {
                Info "Task '$TaskName' is running and will start with the machine"
            } else {
                Warn "The task was registered but the portal is not answering yet."
                Info "Check it in Task Scheduler, or start it by hand with the command below."
            }
        } catch {
            Warn "Could not register the startup task: $($_.Exception.Message)"
            Info "You can still start the portal by hand with the command below."
        }
    }

    # Open the port so other machines on the network can reach it.
    if ($isAdmin) {
        try {
            if (-not (Get-NetFirewallRule -DisplayName "Finance Operations Portal" -ErrorAction SilentlyContinue)) {
                New-NetFirewallRule -DisplayName "Finance Operations Portal" -Direction Inbound `
                    -LocalPort $Port -Protocol TCP -Action Allow -Profile Domain,Private | Out-Null
                Info "Opened TCP $Port on the domain and private firewall profiles"
            }
        } catch { Warn "Could not add a firewall rule; open TCP $Port by hand if needed." }
    }
}

# --------------------------------------------------------------- finish ----
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
       Select-Object -First 1).IPAddress
if (-not $ip) { $ip = '127.0.0.1' }

Write-Host ""
Write-Host ("-" * 64) -ForegroundColor Green
Write-Host "  Installed." -ForegroundColor White
Write-Host ""
if ($started) {
    Write-Host "  Open   http://${ip}:$Port" -ForegroundColor White
    Write-Host "  Manage it in Task Scheduler under '$TaskName'" -ForegroundColor DarkGray
} else {
    Write-Host "  Start it with:"
    Write-Host ""
    Write-Host "    cd $AppDir"
    Write-Host "    .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port $Port"
    Write-Host ""
    Write-Host "  Then open http://${ip}:$Port" -ForegroundColor White
}
Write-Host ""
Write-Host "  Sign in with the first-run credentials above, or your existing account."
Write-Host "  Send the installation ID above to the licence issuer."
Write-Host "  New calculations remain disabled until var\license.json is installed."
if (Test-Path (Join-Path $varDir 'first-admin-password.txt')) {
    Write-Host "  The initial password is also in var\first-admin-password.txt." -ForegroundColor DarkGray
}
Write-Host "  Change that password under Administration - Users straight away." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Next: serve it over HTTPS behind IIS - see section 4 of DEPLOYMENT.md." -ForegroundColor White
Write-Host ("-" * 64) -ForegroundColor Green
Write-Host ""
