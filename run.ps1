# Alpha Harness - Unified Start Script (Windows)
# Starts both backend and frontend services in parallel

Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Alpha Harness - Unified Start" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

$backendJob = $null
$frontendJob = $null

# Cleanup function
$cleanup = {
    Write-Host ""
    Write-Host "Shutting down services..." -ForegroundColor Yellow
    if ($backendJob) { Stop-Job -Job $backendJob -ErrorAction SilentlyContinue }
    if ($frontendJob) { Stop-Job -Job $frontendJob -ErrorAction SilentlyContinue }
}

# Register cleanup on exit
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action $cleanup

# Start backend
Write-Host "Starting backend..." -ForegroundColor Green
$backendJob = Start-Job -ScriptBlock {
    Set-Location (Join-Path $args[0] "backend")
    & uv run uvicorn alpha_harness.main:app --port 8000
} -ArgumentList (Get-Location).Path
Write-Host "Backend started (Job ID: $($backendJob.Id))" -ForegroundColor Green

# Wait a moment for backend to start
Start-Sleep -Seconds 2

# Start frontend
Write-Host "Starting frontend..." -ForegroundColor Green
$frontendJob = Start-Job -ScriptBlock {
    Set-Location (Join-Path $args[0] "frontend")
    & pnpm dev
} -ArgumentList (Get-Location).Path
Write-Host "Frontend started (Job ID: $($frontendJob.Id))" -ForegroundColor Green

Write-Host ""
Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Services Running" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Backend:  http://localhost:8000" -ForegroundColor Yellow
Write-Host "Frontend: http://localhost:5173" -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to stop all services" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan

# Wait for jobs
Wait-Job -Job $backendJob, $frontendJob
