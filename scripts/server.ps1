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

# 콘솔 인코딩은 스크립트 전체에서 출력이 시작되기 전에 딱 한 번만 맞춘다. restart처럼
# Stop-Server → Start-Server가 한 프로세스 안에서 이어질 때, 이미 이전 인코딩으로 그려진
# 줄이 있는 상태에서 중간에 인코딩을 바꾸면 콘솔이 기존 줄의 폭(한글 2칸)을 다시 계산하며
# 글자가 겹쳐 찍히는 현상(예: "실실행행 중중")이 생긴다. 그래서 Start-Server 안이 아니라
# 여기서 가장 먼저 설정한다.
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

    # (콘솔/PYTHONUTF8 인코딩은 스크립트 최상단에서 한 번만 설정 — 위 주석 참고)

    # 서버 시작 전 DB 스키마 확인/생성 (server.sh와 동일한 순서)
    & $Python (Join-Path $ProjectRoot "scripts\init_schema.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Output "DB 스키마 초기화 실패. PostgreSQL과 .env 설정을 확인하세요."
        exit 1
    }

    # GET 필터(PoC) 컬럼 5개 — init_schema.py에 없어 새 DB에서는 로그인 즉시 깨진다
    # (2026-08-12 발견, server.sh와 동일 이유로 여기서도 매 시작마다 같이 실행)
    & $Python (Join-Path $ProjectRoot "scripts\add_get_filter_columns.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Output "GET 필터 컬럼 추가 실패. PostgreSQL과 .env 설정을 확인하세요."
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
        Write-Output "서버 상태 확인 실패. 로그를 확인하세요: $LogFile"
        Stop-Process -Id $proc.Id -Force -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-LogLine "ERROR" "SERVER START HEALTH CHECK FAILED (PID: $($proc.Id))"
        exit 1
    }
    Write-Output "서버 시작됨 (PID: $($proc.Id)) → http://127.0.0.1:$Port"
    Write-Output "로그: $LogFile"
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
    Write-LogLine "INFO" "SERVER STOP (PID: $($proc.Id))"
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
