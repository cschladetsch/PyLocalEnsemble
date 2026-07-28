<#
inspect-alice-retry.ps1

Fetches app.js from the running Alice server and extracts the code that
handles the "retry": true stub response from /chat, since that's the
likely source of the hang (the backend returns a warm-up placeholder
and expects the frontend to retry, but the retry logic may be broken).

Usage:
    .\inspect-alice-retry.ps1
    .\inspect-alice-retry.ps1 -AliceUrl "http://127.0.0.1:8000"
#>

param(
    [string]$AliceUrl = "http://127.0.0.1:8000"
)

function Write-Section($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

Write-Section "Fetching app.js"
try {
    $resp = Invoke-WebRequest -Uri "$AliceUrl/static/app.js" -UseBasicParsing -TimeoutSec 10
    $js = $resp.Content
} catch {
    Write-Host "[FAIL] Could not fetch app.js -> $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Save a local copy for reference/diffing later
$outFile = ".\app.js.snapshot"
$js | Out-File -FilePath $outFile -Encoding utf8
Write-Host "Saved snapshot to $outFile" -ForegroundColor Gray

# 1. Find every mention of "retry" and print surrounding context
Write-Section "Occurrences of 'retry'"
$retryMatches = [regex]::Matches($js, '.{150}retry.{150}')
if ($retryMatches.Count -eq 0) {
    Write-Host "[WARN] No mention of 'retry' found in app.js at all." -ForegroundColor Yellow
    Write-Host "That means the frontend never checks for it, the stub reply just gets shown as final." -ForegroundColor Yellow
} else {
    $i = 1
    foreach ($m in $retryMatches) {
        Write-Host "--- match $i ---" -ForegroundColor Green
        Write-Host $m.Value -ForegroundColor Gray
        $i++
    }
}

# 2. Find the function that parses the SSE stream from /chat (look for "data:" or EventSource or delta)
Write-Section "SSE / stream parsing logic (delta handling)"
$streamMatches = [regex]::Matches($js, 'function\s+\w*[Ss]tream\w*\s*\([^)]*\)\s*\{[\s\S]{0,600}')
if ($streamMatches.Count -gt 0) {
    foreach ($m in $streamMatches) {
        Write-Host $m.Value -ForegroundColor Gray
        Write-Host "---" -ForegroundColor DarkGray
    }
} else {
    # fallback: find anything referencing "delta" or "done"
    $deltaMatches = [regex]::Matches($js, '.{100}(delta|done":\s*true).{200}')
    foreach ($m in $deltaMatches) {
        Write-Host $m.Value -ForegroundColor Gray
        Write-Host "---" -ForegroundColor DarkGray
    }
}

# 3. Find the send() function itself
Write-Section "send() function"
$sendFn = [regex]::Match($js, 'function\s+send\s*\([^)]*\)\s*\{[\s\S]{0,800}')
if ($sendFn.Success) {
    Write-Host $sendFn.Value -ForegroundColor Gray
} else {
    Write-Host "[WARN] Could not isolate send() with this regex, check app.js.snapshot manually." -ForegroundColor Yellow
}

Write-Section "Summary"
Write-Host "Look for: does the code check response.retry and re-POST to /chat automatically?" -ForegroundColor Gray
Write-Host "If retry handling is missing or the re-POST loop never terminates on a real reply, that is the bug." -ForegroundColor Gray
Write-Host "Full file saved locally at $outFile for manual review in your editor." -ForegroundColor Gray