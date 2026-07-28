<#
diagnose-alice.ps1

Diagnoses a hung PyAlice chat pipeline: Ollama, the Alice backend on :8000,
and the actual chat request path. Run from PowerShell, not WSL.

Usage:
    .\diagnose-alice.ps1
    .\diagnose-alice.ps1 -AliceUrl "http://127.0.0.1:8000" -OllamaUrl "http://127.0.0.1:11434"
#>

param(
    [string]$AliceUrl  = "http://127.0.0.1:8000",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [string]$Model     = "qwen2.5-coder:7b",
    [int]$TimeoutSec   = 15
)

function Write-Section($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

function Test-Endpoint {
    param([string]$Url, [string]$Label, [int]$Timeout = 5)
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $Timeout
        $sw.Stop()
        Write-Host "[OK] $Label responded in $($sw.ElapsedMilliseconds) ms (status $($resp.StatusCode))" -ForegroundColor Green
        return $resp
    } catch {
        Write-Host "[FAIL] $Label -> $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

# 1. Port and process check
Write-Section "Listening ports"
$ports = @(8000, 11434)
foreach ($p in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $procId = $conn.OwningProcess | Select-Object -First 1
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        Write-Host "[OK] Port $p listening, PID $procId ($($proc.ProcessName))" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] Nothing listening on port $p" -ForegroundColor Red
    }
}

# 2. Basic reachability
Write-Section "Basic HTTP reachability"
Test-Endpoint -Url "$OllamaUrl/api/tags" -Label "Ollama /api/tags" | Out-Null
Test-Endpoint -Url "$AliceUrl/" -Label "Alice root page" | Out-Null

# 3. Direct Ollama chat completion test (isolates model/runtime hang vs backend hang)
Write-Section "Direct Ollama chat test (model: $Model)"
$body = @{
    model    = $Model
    messages = @(@{ role = "user"; content = "Say OK and nothing else." })
    stream   = $false
} | ConvertTo-Json -Depth 5

try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-RestMethod -Uri "$OllamaUrl/api/chat" -Method Post -Body $body `
        -ContentType "application/json" -TimeoutSec $TimeoutSec
    $sw.Stop()
    Write-Host "[OK] Ollama chat responded in $($sw.ElapsedMilliseconds) ms" -ForegroundColor Green
    Write-Host "     Reply: $($resp.message.content)" -ForegroundColor Gray
} catch {
    Write-Host "[FAIL] Ollama chat call timed out or errored -> $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "     This means the model/runtime itself is stuck, not just the Alice backend." -ForegroundColor Yellow
}

# 4. GPU / VRAM check (contention can silently stall model loads)
Write-Section "GPU status"
try {
    $gpu = & nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>$null
    if ($gpu) {
        Write-Host $gpu -ForegroundColor Gray
    } else {
        Write-Host "[WARN] nvidia-smi returned nothing" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARN] nvidia-smi not found or failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

# 5. Ollama process resource usage (stuck at 100% CPU/mem with no progress is a sign of a wedged model)
Write-Section "Ollama process resource usage"
$ollamaProc = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue
if ($ollamaProc) {
    $ollamaProc | Select-Object Id, ProcessName, CPU, WorkingSet64 | Format-Table -AutoSize
} else {
    Write-Host "[FAIL] No ollama process found running" -ForegroundColor Red
}

# 6. Recent Alice backend log tail, if a log file exists
Write-Section "Alice backend log (last 40 lines, if found)"
$logCandidates = @(
    ".\logs\alice.log",
    ".\alice.log",
    "$env:USERPROFILE\local\repos\PyAlice-private\logs\alice.log"
)
$found = $false
foreach ($log in $logCandidates) {
    if (Test-Path $log) {
        Write-Host "Reading: $log" -ForegroundColor Gray
        Get-Content $log -Tail 40
        $found = $true
        break
    }
}
if (-not $found) {
    Write-Host "[WARN] No log file found at expected paths. Check the terminal running the Alice server directly." -ForegroundColor Yellow
}

Write-Section "Summary"
Write-Host "If Ollama chat test (step 3) hangs or times out: restart the ollama process." -ForegroundColor Gray
Write-Host "If step 3 is fast but the UI still hangs: the bug is in the Alice backend's request handling, check its console output live while sending a message." -ForegroundColor Gray
Write-Host "If GPU shows near-100% utilization with no completion: model may be wedged, restart ollama and retry." -ForegroundColor Gray