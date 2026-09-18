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

# (net-audit proxy keepalive removed 2026-09-18 by user request: proxy layer off)
# Snapshot cleanup remains the sole duty of this watchdog.
