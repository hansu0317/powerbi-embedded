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

# 포트는 .env의 PORT로 오버라이드 가능(없으면 8249) — server.sh와 동일한 이유
# (배포 환경마다 로컬 포트 점유 상황이 달라 스크립트에 고정값을 두면 계속
# merge 충돌이 남, 2026-08-20 발견). .pgpass류 파서 없이 정규식으로 그 줄만 읽는다.
$Port = 8249
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    $envPortLine = Get-Content $EnvFile | Where-Object { $_ -match '^PORT=' } | Select-Object -First 1
    if ($envPortLine) { $Port = ($envPortLine -replace '^PORT=', '').Trim() }
}

# 이 스크립트가 콘솔에 직접 찍는 문구는 전부 영어(ASCII)로 통일한다. chcp 65001 +
# [Console]::OutputEncoding = UTF8을 다 해봐도, 구형 콘솔 창(conhost)의 한글(2칸 폭)
# 다시 그리기 버그가 PowerShell 자신의 Write-Output에서도 재현됐다(2026-08-20,
# "실실행행 중중인인..." 형태 — restart 시 특히 잘 남). 코드페이지 설정으로 못 잡는
# conhost 자체 렌더링 버그라 판단해, 원인을 없애는 대신 아예 한글을 안 쓰기로 했다
# (scripts/add_get_filter_columns.py의 print() 두 줄에 썼던 것과 동일한 해법).
# 로그 파일(logs/server.log)의 한글 내용엔 영향 없음 — 이건 화면에 바로 찍는
# Write-Output 문구에만 해당.
try { & chcp.com 65001 | Out-Null } catch {}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = "1"

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

# 서버가 살아있는 동안은 python.exe가 -RedirectStandardOutput으로 server.log를 계속
# 붙들고 있어(공유 모드 제한) 다른 프로세스가 같은 파일에 쓰려고 하면 IOException이
# 난다 — 그래서 이 함수는 프로세스가 이미 내려간 뒤(Stop-Server)에만 쓴다. BOM 없는
# UTF-8로 append — 이미 파일 맨 앞에 Python이 남긴 BOM이 있으므로 여기서 또 붙이면
# 파일 중간에 BOM 문자가 섞여 보인다.
function Write-LogLine {
    param([string]$Level, [string]$Message)
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    try {
        [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    } catch {}
}

# 기존 로그를 시각이 포함된 파일명으로 보관하고 30일이 지난 로그를 정리한다.
function Invoke-LogRotate {
    if (Test-Path $LogFile) {
        $stamp      = Get-Date -Format "HHmmss"
        $archiveDir = Join-Path $LogDir (Get-Date -Format "yyyyMMdd")
        New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
        Move-Item $LogFile (Join-Path $archiveDir "server-$stamp.log")
    }
    Get-ChildItem $LogDir -Recurse -File -Filter "server*-*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
        Remove-Item -Force -Confirm:$false
}

function Start-Server {
    if (Get-ServerProcess) {
        Write-Output "Already running. (PID: $(Get-Content $PidFile))"
        return
    }
    $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        $busyPid = $busy[0].OwningProcess
        Write-Output "Port $Port is in use by another process (PID: $busyPid) - not tracked by .server.pid."
        Write-Output "Stop it first:  Stop-Process -Id $busyPid -Force"
        exit 1
    }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # (콘솔/PYTHONUTF8 인코딩은 스크립트 최상단에서 한 번만 설정 — 위 주석 참고)

    # 서버 시작 전 DB 스키마 확인/생성 (server.sh와 동일한 순서)
    & $Python (Join-Path $ProjectRoot "scripts\init_schema.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Output "DB schema init failed. Check PostgreSQL and .env settings."
        exit 1
    }

    # MS 계정 로그인(SSO)용 users.email 컬럼 — init_schema.py에 없어 새 DB에서는 로그인
    # 화면에 버튼이 안 뜨는 정도로 그치지만, 매 시작마다 같이 실행해 항상 최신 스키마를
    # 보장한다(2026-08-20 도입, IF NOT EXISTS라 안전).
    & $Python (Join-Path $ProjectRoot "scripts\add_sso_email_column.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Output "SSO email column migration failed. Check PostgreSQL and .env settings."
        exit 1
    }

    Invoke-LogRotate
    # 로그는 server.log 한 파일로만 모은다 — main.py가 모든 레벨을 표준출력 하나로만
    # 내보내므로 그게 곧 이 리다이렉트 대상이다. 표준에러는 NUL로 버린다: 우리 앱
    # 로거는 표준에러를 전혀 쓰지 않고(main.py 참고), 여기 남는 건 uvicorn 자체의
    # 시작/종료 배너 같은 부가 문구뿐이다.
    $proc = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$Port", "--no-access-log") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $LogFile -RedirectStandardError "NUL"
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
        Write-Output "Server health check failed. Check the log: $LogFile"
        Stop-Process -Id $proc.Id -Force -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-LogLine "ERROR" "SERVER START HEALTH CHECK FAILED (PID: $($proc.Id))"
        exit 1
    }
    Write-Output "Server started (PID: $($proc.Id)) -> http://127.0.0.1:$Port"
    Write-Output "Log: $LogFile"
}

function Stop-Server {
    $proc = Get-ServerProcess
    if (-not $proc) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-Output "No server is running."
        return
    }
    Stop-Process -Id $proc.Id -Force -Confirm:$false
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-LogLine "INFO" "SERVER STOP (PID: $($proc.Id))"
    Write-Output "Server stopped (PID: $($proc.Id))"
}

function Show-Status {
    $proc = Get-ServerProcess
    if ($proc) { Write-Output "Running (PID: $($proc.Id))" }
    else       { Write-Output "Stopped" }
}

switch ($Command) {
    "start"   { Start-Server }
    "stop"    { Stop-Server }
    "restart" { Stop-Server; Start-Sleep -Seconds 1; Start-Server }
    "status"  { Show-Status }
}
