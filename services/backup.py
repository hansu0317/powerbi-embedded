"""DB 자동 백업 — 평일(월~금) 17:00(서버 로컬 시간)에 1회, 서버가 그 시각에 켜져 있을 때만.

서버를 그 시각에 맞춰 억지로 띄우거나 하지 않는다 — 그냥 실행 중인 프로세스 안의
백그라운드 루프라서, 서버가 꺼져 있던 날은 자연스럽게 그날 백업만 건너뛴다
(main.py의 pbi_sync_loop·_login_cleanup_loop와 같은 패턴).

pg_dump로 커스텀 포맷(-Fc) 덤프를 `backups/`에 저장하고, 오래된 백업은 자동 정리한다.
"""
import asyncio
import logging
import os
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

import config

logger = logging.getLogger("powerbi-gateway")

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
BACKUP_DIR    = PROJECT_ROOT / "backups"
# 명시적 환경 설정 → PATH → 기존 Windows 설치 경로 순으로 찾는다.
PG_DUMP_PATH = os.getenv("PG_DUMP_PATH") or shutil.which("pg_dump") or (
    r"C:\PostgreSQL\16\bin\pg_dump.exe" if Path(r"C:\PostgreSQL\16\bin\pg_dump.exe").is_file() else "pg_dump"
)
BACKUP_HOUR   = 17   # 평일 17시(서버 로컬 시간 기준 — 이 PC는 한국 시간대)
CHECK_INTERVAL_SEC = 300  # 5분마다 "지금이 그 시각인가" 확인
RETENTION_DAYS = 30  # 이보다 오래된 백업 파일은 자동 삭제


def _run_pg_dump() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = BACKUP_DIR / f"backup_{stamp}.dump"
    partial_path = out_path.with_suffix(".dump.partial")

    env = os.environ.copy()
    env["PGPASSWORD"] = config.DB_CONFIG["password"] or ""
    cmd = [
        PG_DUMP_PATH,
        "-h", config.DB_CONFIG["host"],
        "-p", str(config.DB_CONFIG["port"]),
        "-U", config.DB_CONFIG["user"],
        "-Fc",  # custom format — pg_restore로 복원, 용량도 더 작음
        "-f", str(partial_path),
        config.DB_CONFIG["dbname"],
    ]
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True, timeout=600)
        if not partial_path.is_file() or partial_path.stat().st_size == 0:
            raise RuntimeError("pg_dump returned an empty backup")
        partial_path.replace(out_path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise
    return out_path


def _cleanup_old_backups() -> int:
    cutoff = datetime.now().timestamp() - RETENTION_DAYS * 86400
    deleted = 0
    for f in BACKUP_DIR.glob("backup_*.dump"):
        # 수동 작업 전 백업(backup_before_*, backup_pre_*)은 자동 보존정책에서 제외.
        if re.fullmatch(r"backup_\d{8}_\d{6}\.dump", f.name) and f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    return deleted


async def backup_loop():
    """서버가 켜져 있는 동안 5분마다 확인해, 평일 17시대에 하루 1번만 백업을 돌린다."""
    last_backup_date: date | None = None
    while True:
        now = datetime.now()
        is_weekday = now.weekday() < 5  # 0=월 ... 4=금, 5=토 6=일
        if is_weekday and now.hour == BACKUP_HOUR and last_backup_date != now.date():
            try:
                path = await asyncio.to_thread(_run_pg_dump)
                deleted = await asyncio.to_thread(_cleanup_old_backups)
                last_backup_date = now.date()
                logger.info("DB BACKUP OK | %s | 오래된 백업 %d개 정리", path.name, deleted)
            except Exception:
                logger.exception("DB BACKUP FAIL")
                # 실패해도 last_backup_date를 갱신하지 않아 같은 날 재시도 창(다음 5분 체크)이 남는다 —
                # 다만 시간(17시)을 벗어나면 그날은 더 이상 재시도 안 하고 다음 평일로 넘어간다.
        await asyncio.sleep(CHECK_INTERVAL_SEC)
