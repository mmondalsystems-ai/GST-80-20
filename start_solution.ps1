<#
    Fast local launcher used by START_GST_80_20.bat after initial setup.
    Reuses the existing virtual environment, chooses the next free local port,
    performs database recovery checks, opens the browser, and keeps the server
    attached to this window. Closing the window stops the portal.
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $appDir '.venv\Scripts\python.exe'
$server = Join-Path $appDir '.venv\Scripts\uvicorn.exe'
Set-Location $appDir

if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath $server -PathType Leaf)) {
    Write-Error 'The local environment has not been prepared. Run deploy.bat once, then use START_GST_80_20.bat for later launches.'
    exit 1
}

$requestedPort = $Port
while ($Port -le 65535) {
    $probe = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Port
    )
    $probe.ExclusiveAddressUse = $true
    try {
        $probe.Start()
        break
    } catch [System.Net.Sockets.SocketException] {
        $Port++
    } finally {
        $probe.Stop()
    }
}

if ($Port -gt 65535) {
    Write-Error "No available local TCP port was found from $requestedPort through 65535."
    exit 1
}
if ($Port -ne $requestedPort) {
    Write-Host "Port $requestedPort is unavailable; using port $Port instead." -ForegroundColor Yellow
}

Write-Host 'Checking the local database...' -ForegroundColor Cyan
& $python -m app.recovery
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Database recovery did not complete. The portal was not started.'
    exit 1
}

$database = & $python -m app.deploy_check local-db
if ($LASTEXITCODE -ne 0) {
    Write-Error 'The configured database is not a valid local SQLite database.'
    exit 1
}

$url = "http://127.0.0.1:$Port"
Write-Host "Live database: $database"
Write-Host "Starting GST 80-20 at $url" -ForegroundColor Green
Write-Host 'Keep this window open. Press Ctrl+C to stop the application.'

$browserJob = Start-Job -ScriptBlock {
    param($address)
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest "$address/healthz" -UseBasicParsing -TimeoutSec 1
            if ($response.Content.Trim() -eq 'ok') {
                Start-Process $address
                return
            }
        } catch { }
    }
} -ArgumentList $url

try {
    & $server app.main:app --host 127.0.0.1 --port $Port
    exit $LASTEXITCODE
} finally {
    Stop-Job $browserJob -ErrorAction SilentlyContinue
    Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
}
