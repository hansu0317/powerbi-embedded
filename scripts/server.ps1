# Power BI 게이트웨이 서버 관리 (Windows용 — server.sh의 PowerShell 대응)
#
# 사용법 (PowerShell):
#   .\scripts\server.ps1                # 스키마 확인 후 시작 ("start" 생략 가능)
#   .\scripts\server.ps1 start
#   .\scripts\server.ps1 stop
#   .\scripts\server.ps1 restart
#   .\scripts\server.ps1 status
#
# Git Bash에서는:
#   powershell -ExecutionPolicy Bypass -File scripts/server.ps1 start
#
# 백그라운드로 실행되고 PID를 .server.pid에 기록한다 (server.sh와 동일한 관리 방식).
#
# DB 스키마는 scripts/init_schema.py가 담당한다(버전 이력 없는 단일 스크립트 —
# CREATE TABLE IF NOT EXISTS라서 매번 실행해도 안전하다. schema_migrations로
# 버전을 추적하던 옛 scripts/migrate_report_meta.py는 더 이상 자동 실행되지 않는다).

param(
    [Parameter(Position = 0)][string]$Command = "start"
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile     = Join-Path $ProjectRoot ".server.pid"
$LogDir      = Join-Path $ProjectRoot "logs"
$LogFile     = Join-Path $LogDir "server.log"
$ErrFile     = Join-Path $LogDir "server.err.log"
$Port        = 8247

# 프로젝트 venv가 있으면 우선 사용, 없으면 PATH의 python (환경별 경로 하드코딩 금지)
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

function Get-ServerProcess {
    if (-not (Test-Path $PidFile)) { return $null }
    $procId = Get-Content $PidFile -ErrorAction SilentlyContinue
    if (-not $procId) { return $null }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -match 'python') { return $proc }
    return $null
}

# 기존 로그를 시각이 포함된 파일명으로 보관하고 30일이 지난 로그를 정리한다.
function Invoke-LogRotate {
    foreach ($f in @($LogFile, $ErrFile)) {
        if (Test-Path $f) {
            $stamp      = Get-Date -Format "HHmmss"
            $archiveDir = Join-Path $LogDir (Get-Date -Format "yyyyMMdd")
            New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
            $name = [IO.Path]::GetFileNameWithoutExtension($f)
            Move-Item $f (Join-Path $archiveDir "$name-$stamp.log")
        }
    }
    Get-ChildItem $LogDir -Recurse -File -Filter "server*-*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
        Remove-Item -Force -Confirm:$false
}

function Start-Server {
    if (Get-ServerProcess) {
        Write-Output "이미 실행 중입니다. (PID: $(Get-Content $PidFile))"
        return
    }
    $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        $busyPid = $busy[0].OwningProcess
        Write-Output "포트 $Port 를 다른 프로세스(PID: $busyPid)가 쓰고 있습니다 — .server.pid로 추적되지 않는 프로세스입니다."
        Write-Output "먼저 종료하세요:  Stop-Process -Id $busyPid -Force"
        exit 1
    }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # 서버 시작 전 DB 스키마 확인/생성 (server.sh와 동일한 순서)
    & $Python (Join-Path $ProjectRoot "scripts\init_schema.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Output "DB 스키마 초기화 실패. PostgreSQL과 .env 설정을 확인하세요."
        exit 1
    }

    Invoke-LogRotate
    $proc = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$Port", "--no-access-log") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile
    $proc.Id | Out-File $PidFile -Encoding ascii

    # 시작 시 복구 작업 때문에 포트 바인딩이 늦을 수 있어 최대 15초까지 재시도한다.
    $healthy = $false
    foreach ($i in 1..15) {
        try {
            $res = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 3
            if ($res.StatusCode -eq 200) { $healthy = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) {
        Write-Output "서버 상태 확인 실패. 로그를 확인하세요: $ErrFile"
        Stop-Process -Id $proc.Id -Force -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        exit 1
    }
    Write-Output "서버 시작됨 (PID: $($proc.Id)) → http://127.0.0.1:$Port"
    Write-Output "로그: $ErrFile"
}

function Stop-Server {
    $proc = Get-ServerProcess
    if (-not $proc) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-Output "실행 중인 서버가 없습니다."
        return
    }
    Stop-Process -Id $proc.Id -Force -Confirm:$false
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Output "서버 종료됨 (PID: $($proc.Id))"
}

function Show-Status {
    $proc = Get-ServerProcess
    if ($proc) { Write-Output "실행 중 (PID: $($proc.Id))" }
    else       { Write-Output "중지됨" }
}

switch ($Command) {
    "start"   { Start-Server }
    "stop"    { Stop-Server }
    "restart" { Stop-Server; Start-Sleep -Seconds 1; Start-Server }
    "status"  { Show-Status }
}
