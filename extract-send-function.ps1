<#
extract-send-function.ps1

Extracts the FULL body of send() from app.js using brace counting, so we
get the complete function including the fetch('/chat', ...) call and the
SSE stream-reading loop, not just a length-truncated snippet.

Usage:
    .\extract-send-function.ps1
    .\extract-send-function.ps1 -AliceUrl "http://127.0.0.1:8000"
#>

param(
    [string]$AliceUrl = "http://127.0.0.1:8000",
    [string]$FunctionName = "send"
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

# Save full snapshot too, in case we need more context later
$snapshotPath = ".\app.js.snapshot"
$js | Out-File -FilePath $snapshotPath -Encoding utf8
Write-Host "Full file saved to $snapshotPath" -ForegroundColor Gray

Write-Section "Extracting function $FunctionName() with brace counting"

$startPattern = "function\s+$FunctionName\s*\([^)]*\)\s*\{"
$m = [regex]::Match($js, $startPattern)

if (-not $m.Success) {
    Write-Host "[FAIL] Could not find 'function $FunctionName(' in app.js" -ForegroundColor Red
    exit 1
}

$startIdx = $m.Index
$braceIdx = $m.Index + $m.Length - 1   # index of the opening {
$depth = 0
$endIdx = -1

for ($i = $braceIdx; $i -lt $js.Length; $i++) {
    $ch = $js[$i]
    if ($ch -eq '{') { $depth++ }
    elseif ($ch -eq '}') {
        $depth--
        if ($depth -eq 0) { $endIdx = $i; break }
    }
}

if ($endIdx -eq -1) {
    Write-Host "[WARN] Braces never balanced, function may not have been fully captured." -ForegroundColor Yellow
    $endIdx = [Math]::Min($startIdx + 4000, $js.Length - 1)
}

$fullFunction = $js.Substring($startIdx, $endIdx - $startIdx + 1)

Write-Host $fullFunction -ForegroundColor Gray

$outPath = ".\$FunctionName.function.js"
$fullFunction | Out-File -FilePath $outPath -Encoding utf8

Write-Section "Summary"
Write-Host "Full function saved to $outPath ($($fullFunction.Length) chars, $(($fullFunction -split "`n").Count) lines)" -ForegroundColor Gray
Write-Host "Look for the fetch('/chat', ...) call and how the SSE response body is read." -ForegroundColor Gray
Write-Host "Check specifically whether 'retry' is checked anywhere after the stream finishes." -ForegroundColor Gray
