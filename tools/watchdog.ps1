$ErrorActionPreference = 'SilentlyContinue'

$root = Join-Path $env:USERPROFILE '.zcode'
$toolsDir = Join-Path $env:USERPROFILE '.zcode-tools'
$log = Join-Path $toolsDir 'watchdog.log'

if (-not (Test-Path $root)) { return }
if (-not (Test-Path $toolsDir)) { New-Item -ItemType Directory -Path $toolsDir | Out-Null }

# Encrypted snapshot bundles (e.g. *.tar.gz.enc) and their key envelopes.
# Without the bundle nothing can be uploaded; these are also undecryptable
# locally, so deleting them loses nothing.
$patterns = @('*.enc', '*.envelope.json')

foreach ($pattern in $patterns) {
    # only snapshot artifacts live under \checkpoints\ - never touch .enc
    # files that other subsystems may legitimately create elsewhere
    $hits = Get-ChildItem -Path $root -Recurse -Force -File -Filter $pattern |
        Where-Object { $_.FullName -match '\\checkpoints\\' }
    foreach ($h in $hits) {
        $line = '{0} deleted {1} ({2} bytes)' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $h.FullName, $h.Length
        Add-Content -Path $log -Value $line
        Remove-Object -LiteralPath $h.FullName -Force
    }
}

# keep the net-audit counting proxy alive (layer: forced loopback accounting)
$proxyOk = $false
try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect('127.0.0.1', 8765)
    $c.Close(); $proxyOk = $true
} catch {}
if (-not $proxyOk) {
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { $pyw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe' }
    if (Test-Path $pyw) {
        Add-Content -Path $log -Value ('{0} net audit proxy down - restarting' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
        Start-Process -FilePath $pyw -ArgumentList ('"{0}"' -f (Join-Path $toolsDir 'net_audit_proxy.py')) -WindowStyle Hidden
    }
}

# keep the MITM inspection layer alive (port 8766, chained through 8765)
$mitmOk = $false
try {
    $c2 = New-Object Net.Sockets.TcpClient
    $c2.Connect('127.0.0.1', 8766)
    $c2.Close(); $mitmOk = $true
} catch {}
$mitmLauncher = Join-Path $toolsDir 'run_mitm.bat'
if (-not $mitmOk -and (Test-Path $mitmLauncher)) {
    Add-Content -Path $log -Value ('{0} mitm layer down - restarting' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Start-Process -FilePath $env:ComSpec -ArgumentList '/c', ('"{0}"' -f $mitmLauncher) -WindowStyle Hidden
}
