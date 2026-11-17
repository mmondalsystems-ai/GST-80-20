<# Isolated source-BAT deployment profiles. Does not touch the live checkout DB. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Wheelhouse,
    [string]$OutputRoot = ''
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$Python = (Resolve-Path -LiteralPath $Python).Path
$Wheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
if (-not $OutputRoot) { $OutputRoot = Join-Path $env:TEMP ('GSTQA-' + [guid]::NewGuid().ToString('N').Substring(0, 8)) }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $OutputRoot) { throw 'Use a fresh test output directory.' }
New-Item -ItemType Directory -Path $OutputRoot | Out-Null

function Profile([string]$name, [bool]$copyApp, [bool]$copyWheels) {
    $folder = Join-Path $OutputRoot $name
    New-Item -ItemType Directory -Path $folder | Out-Null
    foreach ($file in @('deploy.bat','deploy.ps1','install.ps1','requirements.txt','requirements-postgres.txt')) {
        Copy-Item -LiteralPath (Join-Path $repo $file) -Destination $folder
    }
    if ($copyApp) { Copy-Item -LiteralPath (Join-Path $repo 'app') -Destination $folder -Recurse }
    if ($copyWheels) { Copy-Item -LiteralPath $Wheelhouse -Destination (Join-Path $folder 'wheelhouse') -Recurse }
    return $folder
}

function Invoke-Prepare([string]$folder, [int]$port, [hashtable]$environment) {
    $saved = @{}
    $names = @('PATH','GST8020_PYTHON','GST8020_NO_AUTO_INSTALL','GST8020_DATABASE_DIR','DATABASE_URL','BACKUP_DIR')
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name,'Process') }
    try {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot\System32\WindowsPowerShell\v1.0;$env:SystemRoot"
        foreach ($name in $names | Where-Object { $_ -ne 'PATH' }) { [Environment]::SetEnvironmentVariable($name,$null,'Process') }
        foreach ($name in $environment.Keys) { [Environment]::SetEnvironmentVariable($name,[string]$environment[$name],'Process') }
        $stdout = Join-Path $folder 'test-out.log'
        $stderr = Join-Path $folder 'test-err.log'
        $bat = Join-Path $folder 'deploy.bat'
        $batCommand = '""' + $bat + '" ' + $port + ' --prepare-only"'
        $process = Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" `
            -ArgumentList @('/d','/c',$batCommand) `
            -WorkingDirectory $folder -Wait -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        return @{exit=$process.ExitCode; text=((Get-Content $stdout,$stderr -Raw -ErrorAction SilentlyContinue) -join "`n")}
    } finally {
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process') }
    }
}

$baseEnvironment = @{GST8020_PYTHON=$Python;GST8020_NO_AUTO_INSTALL='1'}
$results = @()

$pg = Profile 'postgres-config' $false $false
'DATABASE_URL=postgresql+psycopg2://finops:secret@localhost/finops' | Set-Content -LiteralPath (Join-Path $pg '.env')
$r = Invoke-Prepare $pg 19310 $baseEnvironment
if ($r.exit -eq 0 -or $r.text -notmatch 'configured for PostgreSQL' -or (Test-Path (Join-Path $pg '.venv'))) { throw "PostgreSQL config profile failed: $($r.text)" }
$results += 'PASS: PostgreSQL .env rejected before Python/pip/database changes'

$ambient = Profile 'postgres-ambient' $false $false
$r = Invoke-Prepare $ambient 19311 ($baseEnvironment + @{DATABASE_URL='postgresql://server/test'})
if ($r.exit -eq 0 -or $r.text -notmatch 'PowerShell\s+environment\s+points to PostgreSQL' -or (Test-Path (Join-Path $ambient '.venv'))) { throw "Ambient PostgreSQL profile failed: $($r.text)" }
$results += 'PASS: inherited PostgreSQL URL rejected before Python/pip/database changes'

$missing = Profile 'no-python-no-winget' $false $false
$r = Invoke-Prepare $missing 19312 @{GST8020_NO_AUTO_INSTALL='1'}
if ($r.exit -eq 0 -or $r.text -notmatch 'Automatic installation is disabled' -or (Test-Path (Join-Path $missing '.venv'))) { throw "Missing Python profile failed: $($r.text)" }
$results += 'PASS: missing Python fails clearly without launching winget or installing PostgreSQL'

$deep = Profile ('deep-' + ('x' * 118)) $false $false
$r = Invoke-Prepare $deep 19316 $baseEnvironment
if ($r.exit -eq 0 -or $r.text -notmatch 'reliable\s+Windows\s+package\s+installation' -or (Test-Path (Join-Path $deep '.venv'))) { throw "Long path profile failed: $($r.text)" }
$results += 'PASS: long Windows path rejected before pip can fail with WinError 206'

$clean = Profile 'offline-python312' $true $true
New-Item -ItemType Directory -Path (Join-Path $clean 'var\backups') -Force | Out-Null
# Simulate the client failure: a broken pre-existing virtualenv. It is archived, never deleted.
New-Item -ItemType Directory -Path (Join-Path $clean '.venv\Scripts') -Force | Out-Null
'broken' | Set-Content -LiteralPath (Join-Path $clean '.venv\Scripts\python.exe')
$environment = $baseEnvironment + @{BACKUP_DIR=(Join-Path $clean 'var\backups');GST8020_DATABASE_DIR=(Join-Path $clean 'var')}
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,19313)
$listener.Start()
try { $r = Invoke-Prepare $clean 19313 $environment }
finally { $listener.Stop() }
if ($r.exit -ne 0 -or $r.text -notmatch 'using port 19314' -or -not (Test-Path (Join-Path $clean 'var\finops.db'))) { throw "Offline install profile failed: $($r.text)" }
if (-not @(Get-ChildItem -LiteralPath $clean -Directory -Filter '.venv-previous-*').Count) { throw 'Broken virtualenv was not archived.' }
if (Test-Path (Join-Path $clean '.venv\Lib\site-packages\psycopg2')) { throw 'PostgreSQL driver was installed in SQLite mode.' }
$identity = (Get-Content -LiteralPath (Join-Path $clean 'var\installation-id') -Raw).Trim()
if ($identity -notmatch '^[0-9a-f]{8}-') { throw 'Installation ID missing.' }
$results += 'PASS: offline wheelhouse install, broken venv archived, occupied port skipped, SQLite prepared, no PostgreSQL driver'

$r = Invoke-Prepare $clean 19315 $environment
if ($r.exit -ne 0) { throw "Repeat install profile failed: $($r.text)" }
if ($identity -ne (Get-Content -LiteralPath (Join-Path $clean 'var\installation-id') -Raw).Trim()) { throw 'Repeat install changed the installation ID.' }
$results += 'PASS: repeat deployment retains installation identity and database'

$results | ForEach-Object { Write-Host $_ }
Write-Host "Profiles and logs: $OutputRoot"
