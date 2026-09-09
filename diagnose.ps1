# Writes a full report to diagnose.log. Do not run directly -- use DIAGNOSE.bat.

Set-Location $PSScriptRoot
$ErrorActionPreference = "Continue"

function Line($t) { Write-Output $t }

Line "=== 0. context ==="
Line ("cwd        : " + (Get-Location))
Line ("PSVersion  : " + $PSVersionTable.PSVersion)
Line ("date       : " + (Get-Date))

Line "`n=== 1. venv python ==="
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Line ("path: " + $py)
if (-not (Test-Path $py)) {
    Line "MISSING -- the virtualenv does not exist. Stop; the venv must be rebuilt."
    return
}
& $py --version 2>&1 | ForEach-Object { Line $_ }

Line "`n=== 2. app import ==="
& $py -c "from invoice_ops.api.main import app; print('OK:', app.title)" 2>&1 | ForEach-Object { Line $_ }

Line "`n=== 3. migrations (alembic upgrade head) ==="
& $py -m alembic upgrade head 1>$null 2>$null
Line ("exit code: " + $LASTEXITCODE + "  (0 = ok)")

Line "`n=== 4. port 8000 in use? ==="
$conns = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($conns) {
    $conns | ForEach-Object { Line ("  " + $_.State + "  pid=" + $_.OwningProcess) }
} else {
    Line "  free"
}

Line "`n=== 5. start server, wait 15s, probe ==="
Remove-Item "$PSScriptRoot\_srv.out","$PSScriptRoot\_srv.err" -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $py `
    -ArgumentList "-m","uvicorn","invoice_ops.api.main:app","--host","127.0.0.1","--port","8000" `
    -RedirectStandardOutput "$PSScriptRoot\_srv.out" `
    -RedirectStandardError  "$PSScriptRoot\_srv.err" `
    -PassThru -NoNewWindow
Line ("server PID: " + $p.Id)
Start-Sleep 15

Line "--- curl http://127.0.0.1:8000/health/live ---"
curl.exe -s -m 5 -w "`nHTTP %{http_code}`n" http://127.0.0.1:8000/health/live 2>&1 | ForEach-Object { Line $_ }

Line "--- curl http://localhost:8000/health/live ---"
curl.exe -s -m 5 -w "`nHTTP %{http_code}`n" http://localhost:8000/health/live 2>&1 | ForEach-Object { Line $_ }

Line "`n--- server stdout ---"
Get-Content "$PSScriptRoot\_srv.out" -ErrorAction SilentlyContinue | ForEach-Object { Line $_ }
Line "`n--- server stderr ---"
Get-Content "$PSScriptRoot\_srv.err" -ErrorAction SilentlyContinue | ForEach-Object { Line $_ }

Line "`n=== 6. cleanup ==="
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Line "done"
