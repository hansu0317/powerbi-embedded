# Power BI Gateway 서버 시작 스크립트 (Windows) — .env + PostgreSQL만 준비되면 이걸로 기동
# 사용법: .\scripts\server.ps1          (최신 버전까지 마이그레이션 후 기동)
#        .\scripts\server.ps1 v2       (DB를 v2까지만 적용 후 기동)
# server.sh(리눅스)와 동일한 순서: 마이그레이션 → uvicorn 기동. PID 파일·로그 로테이션 등
# 백그라운드 관리 기능은 뺐다 — foreground 실행(Ctrl+C로 종료)이 우선 목표.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# 프로젝트 venv 우선 사용, 없으면 PATH의 python (환경별 경로 하드코딩 금지 — server.sh와 동일 원칙)
$Python = if (Test-Path "$ProjectRoot\venv\Scripts\python.exe") {
    "$ProjectRoot\venv\Scripts\python.exe"
} else {
    "python"
}

$DbTarget = if ($args.Count -gt 0) { $args[0] -replace '^v', '' } else { $null }
$MigrateArgs = if ($DbTarget) { @("--target", $DbTarget) } else { @() }

Write-Host "DB 마이그레이션 적용 중..."
& $Python "$ProjectRoot\scripts\migrate_report_meta.py" @MigrateArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "DB 마이그레이션 실패. PostgreSQL 서비스 실행 여부와 .env 설정을 확인하세요." -ForegroundColor Red
    exit 1
}

Write-Host "서버 시작 (http://127.0.0.1:8247, Ctrl+C로 종료)"
& $Python -m uvicorn main:app --host 0.0.0.0 --port 8247
