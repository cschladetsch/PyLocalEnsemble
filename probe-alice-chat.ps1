<#
probe-alice-chat.ps1

Finds the actual endpoint app.js calls when you hit "send", then calls it
directly with a timeout so we can see whether the hang is in the frontend,
the network, or the backend's request handling.

Usage:
    .\probe-alice-chat.ps1
    .\probe-alice-chat.ps1 -AliceUrl "http://127.0.0.1:8000" -Message "hello" -TimeoutSec 30
#>

param(
    [string]$AliceUrl   = "http://127.0.0.1:8000",
    [string]$Message    = "hello",
    [int]$TimeoutSec    = 30
)

function Write-Section($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

# 1. Pull app.js and look for fetch()/XHR calls that look like the send path
Write-Section "Fetching app.js to find the chat endpoint"
try {
    $appJsResp = Invoke-WebRequest -Uri "$AliceUrl/static/app.js" -UseBasicParsing -TimeoutSec 10
    $appJs = $appJsResp.Content
} catch {
    Write-Host "[FAIL] Could not fetch app.js -> $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Look for fetch("/something") or fetch('/something') calls near a send-like function
$matches = [regex]::Matches($appJs, 'fetch\(\s*["'']([^"'']+)["'']')
$candidates = $matches | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique

if ($candidates.Count -eq 0) {
    Write-Host "[WARN] No fetch() calls found in app.js. It may use XHR or websockets instead." -ForegroundColor Yellow
    Write-Host "Searching for 'send' function definition instead:" -ForegroundColor Yellow
    $sendFn = [regex]::Match($appJs, 'function\s+send\s*\([^)]*\)\s*\{[\s\S]{0,400}')
    if ($sendFn.Success) {
        Write-Host $sendFn.Value -ForegroundColor Gray
    }
} else {
    Write-Host "Found candidate endpoints called via fetch():" -ForegroundColor Green
    $candidates | ForEach-Object { Write-Host "  $_" }
}

# Common guesses if regex above didn't nail it down
$likely = $candidates | Where-Object { $_ -match 'send|chat|message|reply' }
if (-not $likely) { $likely = $candidates }

Write-Section "Testing candidate endpoint(s) with a real message"
foreach ($ep in $likely) {
    $url = if ($ep.StartsWith("http")) { $ep } else { "$AliceUrl$ep" }
    Write-Host "POST $url" -ForegroundColor Gray

    $body = @{ message = $Message; text = $Message; content = $Message } | ConvertTo-Json

    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $resp = Invoke-RestMethod -Uri $url -Method Post -Body $body `
            -ContentType "application/json" -TimeoutSec $TimeoutSec
        $sw.Stop()
        Write-Host "[OK] Responded in $($sw.ElapsedMilliseconds) ms" -ForegroundColor Green
        Write-Host ($resp | ConvertTo-Json -Depth 3 -Compress) -ForegroundColor Gray
    } catch {
        Write-Host "[FAIL/HANG] $url -> $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Section "Summary"
Write-Host "If a POST above hangs past $TimeoutSec s: the backend's request handler for that" -ForegroundColor Gray
Write-Host "route is stuck (deadlock, waiting on a lock, or a downstream call that never returns)." -ForegroundColor Gray
Write-Host "If it responds fine here but the UI still hangs: the bug is in app.js itself," -ForegroundColor Gray
Write-Host "check the browser Network tab for the request status and console for JS errors." -ForegroundColor Gray
