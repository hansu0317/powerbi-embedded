import asyncio
import time
import logging
import secrets
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import psycopg2
import psycopg2.extras
import psycopg2.pool
import httpx
import msal
from fastapi import FastAPI, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("powerbi-gateway")

load_dotenv()

TENANT_ID  = os.getenv("TENANT_ID")
CLIENT_ID  = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
WORKSPACE_ID  = os.getenv("WORKSPACE_ID")
SECRET_KEY    = os.getenv("SECRET_KEY")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY는 32자 이상의 랜덤 문자열로 설정해야 합니다.")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "127.0.0.1"),
    "port":     int(os.getenv("DB_PORT", "5432")),
    "dbname":   os.getenv("DB_NAME", "powerbi_gateway"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
}

# Report ID는 DB(report_meta.pbi_report_id)에서 관리 — 웹 업로드로 동적 등록 가능
PBI_API     = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"
PBI_GROUPS  = "https://api.powerbi.com/v1.0/myorg/groups"
FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_API  = f"{FABRIC_BASE}/workspaces/{WORKSPACE_ID}"

MAX_PBIX_SIZE = 1024 * 1024 * 1024  # Imports API 단일 POST 한도 1GB
MAX_UPLOADS_PER_DAY = int(os.getenv("MAX_UPLOADS_PER_DAY", "10"))
MAX_PERSONAL_REPORTS = int(os.getenv("MAX_PERSONAL_REPORTS", "20"))
FABRIC_SYNC_INTERVAL = int(os.getenv("FABRIC_SYNC_INTERVAL", "600"))  # 초, 0이면 주기 동기화 끔

def db_recover_upload_jobs():
    """Fabric 성공 후 DB 등록만 실패한 작업을 재시작 시 복구한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, user_id, report_name, pbi_report_id, pbi_workspace_id
                   FROM upload_jobs
                   WHERE status IN ('fabric_succeeded', 'db_failed')
                     AND pbi_report_id IS NOT NULL
                   ORDER BY id"""
            )
            jobs = cur.fetchall()
    for job in jobs:
        try:
            db_register_report(
                job["report_name"], job["pbi_report_id"], job["user_id"],
                pbi_workspace_id=job["pbi_workspace_id"],
            )
            db_update_upload_job(job["id"], "completed", error_message=None)
            logger.info("UPLOAD RECOVERED | job_id=%s | report=%s", job["id"], job["report_name"])
        except psycopg2.Error:
            logger.exception("UPLOAD RECOVERY FAIL | job_id=%s", job["id"])


def db_get_pending_imports():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.user_id, j.report_name, j.import_id, u.username
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   WHERE j.status = 'accepted' AND j.import_id IS NOT NULL
                   ORDER BY j.id"""
            )
            return cur.fetchall()


async def recover_pending_imports():
    jobs = await asyncio.to_thread(db_get_pending_imports)
    if not jobs:
        return
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        for job in jobs:
            try:
                resp = await client.get(f"{PBI_API}/imports/{job['import_id']}", headers=headers)
                if resp.status_code != 200:
                    logger.warning("UPLOAD RECOVERY WAIT | job_id=%s | http=%s", job["id"], resp.status_code)
                    continue
                result = resp.json()
                state = result.get("importState")
                if state == "Failed":
                    await asyncio.to_thread(
                        db_update_upload_job, job["id"], "failed", error_message=str(result.get("error"))
                    )
                    continue
                if state != "Succeeded" or not result.get("reports"):
                    continue
                pbi_report_id = result["reports"][0]["id"]
                dataset_ids = [dataset["id"] for dataset in result.get("datasets", [])]
                await fabric_rename_new_items(pbi_report_id, dataset_ids, job["report_name"], job["username"])
                await asyncio.to_thread(
                    db_update_upload_job, job["id"], "fabric_succeeded", pbi_report_id=pbi_report_id
                )
                await asyncio.to_thread(
                    db_register_report, job["report_name"], pbi_report_id, job["user_id"],
                    dataset_ids[0] if dataset_ids else None, None,
                )
                await asyncio.to_thread(db_update_upload_job, job["id"], "completed")
                logger.info("UPLOAD IMPORT RECOVERED | job_id=%s", job["id"])
            except Exception:
                logger.exception("UPLOAD IMPORT RECOVERY FAIL | job_id=%s", job["id"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """서버 시작/종료 시 실행되는 초기화 블록.

    시작 순서:
      1. db_recover_upload_jobs    — fabric_succeeded·db_failed 상태 업로드를 DB에 등록
      2. recover_pending_imports   — accepted 상태 업로드의 Fabric Import를 재조회해 이어서 처리
      3. fabric_sync_loop 시작     — 백그라운드에서 Fabric 삭제 동기화 실행
    종료 시 sync_task를 취소해 루프를 정상 종료한다.
    """
    try:
        await asyncio.to_thread(db_recover_upload_jobs)
        await recover_pending_imports()
    except Exception:
        logger.exception("STARTUP DB CHECK FAIL")
    sync_task = asyncio.create_task(fabric_sync_loop())
    yield
    sync_task.cancel()


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=28800,
    same_site="lax",
    https_only=COOKIE_SECURE,
)
templates = Jinja2Templates(directory="templates")

token_cache = {"access_token": None, "expires_at": 0}
fabric_token_cache = {"access_token": None, "expires_at": 0}


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/api/upload":
        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > MAX_PBIX_SIZE + 1024 * 1024:
                return HTMLResponse("업로드 파일이 너무 큽니다.", status_code=413)
        except ValueError:
            return HTMLResponse("잘못된 Content-Length입니다.", status_code=400)
    return await call_next(request)


# ── DB 헬퍼 ──────────────────────────────────────────────────────────────────

# 요청마다 연결을 새로 맺지 않도록 프로세스당 커넥션 풀을 사용한다.
# 모든 쓰기 함수는 명시적으로 commit하므로, 반환 전 rollback으로 트랜잭션 잔재만 정리한다.
db_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=int(os.getenv("DB_POOL_MIN", "2")),
    maxconn=int(os.getenv("DB_POOL_MAX", "20")),
    cursor_factory=psycopg2.extras.RealDictCursor,
    **DB_CONFIG,
)


@contextmanager
def db_conn():
    conn = db_pool.getconn()
    broken = False
    try:
        yield conn
        conn.rollback()
    except psycopg2.Error:
        # 연결이 깨졌을 수 있으므로 풀에 돌려주지 않고 폐기한다.
        broken = True
        raise
    except Exception:
        # DB 외 예외(HTTPException 등)도 트랜잭션만 정리하고 연결은 재사용한다.
        try:
            conn.rollback()
        except psycopg2.Error:
            broken = True
        raise
    finally:
        db_pool.putconn(conn, close=broken)


def db_health_check():
    """핵심 테이블 조인이 살아 있는지 확인한다 (/health)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*)
                   FROM reports r
                   JOIN report_meta m ON m.report_id = r.id
                   LEFT JOIN users u ON u.id = r.owner_id"""
            )


def db_authenticate(username: str, password: str):
    """아이디+비밀번호 확인 후 사용자 정보 반환. 불일치하거나 비활성 계정이면 None."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, password, is_admin "
                "FROM users WHERE username = %s AND is_active = TRUE",
                (username,),
            )
            row = cur.fetchone()
    if not row:
        return None
    if not bcrypt.checkpw(password.encode(), row["password"].encode()):
        return None
    return row


def db_login_allowed(username: str, ip: str) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS count FROM login_attempts
                   WHERE username = %s AND ip_address = %s AND succeeded = FALSE
                     AND attempted_at >= NOW() - INTERVAL '15 minutes'""",
                (username, ip),
            )
            return cur.fetchone()["count"] < 5


def db_record_login(username: str, ip: str, succeeded: bool):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM login_attempts WHERE attempted_at < NOW() - INTERVAL '30 days'")
            if succeeded:
                cur.execute("DELETE FROM login_attempts WHERE username = %s AND ip_address = %s", (username, ip))
                cur.execute("UPDATE users SET last_login_at = NOW() WHERE username = %s", (username,))
            cur.execute(
                "INSERT INTO login_attempts (username, ip_address, succeeded) VALUES (%s, %s, %s)",
                (username, ip, succeeded),
            )
        conn.commit()


def db_get_user(username: str):
    """username으로 사용자 정보 조회."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, is_admin "
                "FROM users WHERE username = %s",
                (username,),
            )
            return cur.fetchone()


def db_get_reports(username: str) -> list:
    """해당 사용자가 접근 가능한 보고서 목록을 반환한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.owner_id,
                          owner.username AS owner_username,
                          s.preview_image_url, s.tab_type
                   FROM user_reports ur
                   JOIN reports r     ON r.id = ur.report_id
                   JOIN report_meta m ON m.report_id = r.id
                   LEFT JOIN report_settings s ON s.report_id = r.id
                   LEFT JOIN users owner ON owner.id = r.owner_id
                   JOIN users   u ON u.id = ur.user_id
                   WHERE u.username = %s AND ur.can_view = TRUE AND r.status = 'active'
                   ORDER BY r.id""",
                (username,),
            )
            return cur.fetchall()


def db_get_report(report_id: int):
    """DB 보고서 ID로 Fabric ID와 소유 정보를 조회한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.owner_id,
                          m.pbi_report_id, m.pbi_dataset_id, m.pbi_workspace_id,
                          s.default_page, s.enable_filter, s.enable_page_nav,
                          s.use_data_bot, s.tab_type,
                          COALESCE(rr.enabled, FALSE) AS rls_enabled,
                          COALESCE(rr.role_names, ARRAY[]::TEXT[]) AS rls_role_names
                   FROM reports r
                   LEFT JOIN report_meta m ON m.report_id = r.id
                   LEFT JOIN report_settings s ON s.report_id = r.id
                   LEFT JOIN report_rls rr ON rr.report_id = r.id
                   WHERE r.id = %s""",
                (report_id,),
            )
            return cur.fetchone()


def db_find_report(owner_id: int, name: str):
    """같은 이름의 '살아있는' 보고서 조회. Fabric에서 삭제된(deleted) 이름은 재사용 가능."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.name, r.owner_id, m.pbi_report_id
                FROM reports r
                LEFT JOIN report_meta m ON m.report_id = r.id
                WHERE r.owner_id = %s AND LOWER(r.name) = LOWER(%s)
                  AND r.status <> 'deleted'
                """,
                (owner_id, name),
            )
            return cur.fetchone()


def db_reserve_upload(user_id: int, report_name: str) -> int:
    """Fabric 호출 전에 업로드를 예약하며 DB 제약으로 다중 프로세스 경합을 막는다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (user_id,))
            cur.execute(
                "SELECT COUNT(*) AS count FROM reports WHERE owner_id = %s AND report_type = 'personal'",
                (user_id,),
            )
            if cur.fetchone()["count"] >= MAX_PERSONAL_REPORTS:
                raise HTTPException(status_code=429, detail=f"개인 보고서는 최대 {MAX_PERSONAL_REPORTS}개까지 등록할 수 있습니다.")
            cur.execute(
                """SELECT COUNT(*) AS count FROM upload_jobs
                   WHERE user_id = %s AND created_at >= CURRENT_DATE""",
                (user_id,),
            )
            if cur.fetchone()["count"] >= MAX_UPLOADS_PER_DAY:
                raise HTTPException(status_code=429, detail=f"하루 업로드는 최대 {MAX_UPLOADS_PER_DAY}회입니다.")
            try:
                cur.execute(
                    """INSERT INTO upload_jobs (user_id, report_name, status)
                       VALUES (%s, %s, 'publishing') RETURNING id""",
                    (user_id, report_name),
                )
                row = cur.fetchone()
            except psycopg2.errors.UniqueViolation as exc:
                conn.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"'{report_name}' 보고서를 이미 처리 중이거나 등록했습니다.",
                ) from exc
        conn.commit()
    return row["id"]


def db_update_upload_job(job_id: int, status: str, **values):
    allowed = {"import_id", "pbi_report_id", "error_message", "pbi_workspace_id", "report_id"}
    updates = {key: value for key, value in values.items() if key in allowed}
    assignments = ["status = %s", "updated_at = NOW()"]
    params = [status]
    for key, value in updates.items():
        assignments.append(f"{key} = %s")
        params.append(value)
    params.append(job_id)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE upload_jobs SET {', '.join(assignments)} WHERE id = %s",
                params,
            )
        conn.commit()


def db_register_report(
    name: str,
    pbi_report_id: str,
    owner_id: int,
    pbi_dataset_id: str | None = None,
    fabric_folder_id: str | None = None,
    pbi_workspace_id: str | None = None,
):
    """업로드된 보고서를 등록하고 소유자와 관리자에게 열람 권한을 부여한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            # 초기 보고서를 명시적 ID로 넣은 DB는 SERIAL 시퀀스가 뒤처질 수 있다.
            # 등록 트랜잭션끼리 잠근 뒤 현재 최대 ID로 맞춰 기본키 충돌을 방지한다.
            cur.execute("LOCK TABLE reports IN SHARE ROW EXCLUSIVE MODE")
            cur.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('reports', 'id'),
                    GREATEST((SELECT COALESCE(MAX(id), 0) FROM reports), 1),
                    (SELECT COUNT(*) > 0 FROM reports)
                )
                """
            )
            cur.execute(
                "SELECT id FROM reports WHERE owner_id = %s AND LOWER(name) = LOWER(%s) FOR UPDATE",
                (owner_id, name),
            )
            existing = cur.fetchone()
            if existing:
                # 같은 이름이 deleted 상태로 남아 있으면 그 행을 재사용해 되살린다.
                report_id = existing["id"]
                cur.execute(
                    """UPDATE reports SET status = 'active', deleted_at = NULL,
                              updated_at = NOW(), updated_by = %s
                       WHERE id = %s""",
                    (owner_id, report_id),
                )
            else:
                cur.execute(
                    """INSERT INTO reports (
                           name, report_type, owner_id, status, created_by, updated_by
                       ) VALUES (%s, 'personal', %s, 'active', %s, %s)
                       RETURNING id""",
                    (name, owner_id, owner_id, owner_id),
                )
                report_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO report_meta (
                       report_id, pbi_report_id, pbi_workspace_id,
                       pbi_dataset_id, fabric_folder_id
                   ) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (report_id) DO UPDATE SET pbi_report_id = EXCLUDED.pbi_report_id,
                                                        pbi_workspace_id = EXCLUDED.pbi_workspace_id,
                                                        pbi_dataset_id = EXCLUDED.pbi_dataset_id,
                                                        fabric_folder_id = EXCLUDED.fabric_folder_id,
                                                        updated_at = NOW()""",
                (report_id, pbi_report_id, pbi_workspace_id or WORKSPACE_ID, pbi_dataset_id, fabric_folder_id),
            )
            cur.execute("INSERT INTO report_settings (report_id) VALUES (%s) ON CONFLICT DO NOTHING", (report_id,))
            cur.execute("INSERT INTO report_rls (report_id) VALUES (%s) ON CONFLICT DO NOTHING", (report_id,))
            cur.execute(
                """INSERT INTO user_reports (user_id, report_id, can_view, can_edit, can_manage, granted_by)
                   VALUES (%s, %s, TRUE, TRUE, TRUE, %s)
                   ON CONFLICT (user_id, report_id) DO UPDATE SET
                       can_view = TRUE, can_edit = TRUE, can_manage = TRUE""",
                (owner_id, report_id, owner_id),
            )
            cur.execute(
                """
                INSERT INTO user_reports (user_id, report_id, can_view, can_edit, can_manage, granted_by)
                SELECT id, %s, TRUE, TRUE, TRUE, %s
                FROM users WHERE is_admin = TRUE AND id <> %s
                ON CONFLICT (user_id, report_id) DO UPDATE SET
                    can_view = TRUE, can_edit = TRUE, can_manage = TRUE
                """,
                (report_id, owner_id, owner_id),
            )
            cur.execute(
                """INSERT INTO report_audit_log (
                       report_id, actor_user_id, action, details
                   ) VALUES (%s, %s, 'personal_report_registered',
                             jsonb_build_object('pbi_report_id', %s, 'name', %s))""",
                (report_id, owner_id, pbi_report_id, name),
            )
        conn.commit()
    return report_id


def db_record_view(report_id: int, user_id: int):
    """보고서 열람 이력 기록 — 인기 보고서 파악·감사 용도."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_views (report_id, user_id) VALUES (%s, %s)",
                (report_id, user_id),
            )
        conn.commit()


# ── Fabric 삭제 동기화 ────────────────────────────────────────────────────────

def db_get_synced_reports():
    """Fabric과 대조할 보고서 목록 (active=삭제 감지 대상, deleted=복구 감지 대상)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.status, m.pbi_report_id,
                          COALESCE(m.pbi_workspace_id, %s) AS pbi_workspace_id
                   FROM reports r
                   JOIN report_meta m ON m.report_id = r.id
                   WHERE r.status IN ('active', 'deleted')""",
                (WORKSPACE_ID,),
            )
            return cur.fetchall()


def db_mark_report_deleted(report_id: int, pbi_report_id: str, reason: str):
    """Fabric에서 보고서가 사라진 것을 DB에 반영한다.

    물리 삭제 대신 status='deleted' 소프트 삭제 — 권한(user_reports)·감사 이력은 보존된다.
    reason은 어느 경로로 감지됐는지 기록 ("workspace sync" | "embed 404").
    이미 deleted면 UPDATE가 0건이므로 audit_log 중복 삽입도 막힌다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE reports SET status = 'deleted', deleted_at = NOW(), updated_at = NOW()
                   WHERE id = %s AND status <> 'deleted'""",
                (report_id,),
            )
            if cur.rowcount:
                cur.execute(
                    """INSERT INTO report_audit_log (report_id, action, details)
                       VALUES (%s, 'fabric_deleted',
                               jsonb_build_object('pbi_report_id', %s, 'reason', %s))""",
                    (report_id, pbi_report_id, reason),
                )
        conn.commit()
        return bool(cur.rowcount)


def db_restore_report(report_id: int, pbi_report_id: str):
    """삭제 처리됐던 보고서가 Fabric에 다시 나타나면 active로 되돌린다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE reports SET status = 'active', deleted_at = NULL, updated_at = NOW()
                   WHERE id = %s AND status = 'deleted'""",
                (report_id,),
            )
            if cur.rowcount:
                cur.execute(
                    """INSERT INTO report_audit_log (report_id, action, details)
                       VALUES (%s, 'fabric_restored', jsonb_build_object('pbi_report_id', %s))""",
                    (report_id, pbi_report_id),
                )
        conn.commit()
        return bool(cur.rowcount)


async def sync_fabric_reports() -> dict:
    """Fabric 워크스페이스 목록과 DB를 대조해 삭제/복구를 반영한다.

    흐름:
      1. DB에서 active·deleted 보고서 전체 조회 (pbi_report_id, pbi_workspace_id)
      2. 워크스페이스별로 Fabric REST API 호출 → 현재 존재하는 report ID 집합 확보
      3. 대조:
         - DB active   + Fabric 없음 → db_mark_report_deleted  (삭제 처리)
         - DB deleted  + Fabric 있음 → db_restore_report        (복구)
      4. 결과 요약 반환 {"checked": n, "deleted": n, "restored": n}

    안전장치: 워크스페이스 목록 조회 실패 시 None을 저장하고 해당 워크스페이스는 건너뜀.
    Azure API 장애로 목록이 비어 보여도 전체 보고서가 삭제 처리되는 사고를 막는다.
    """
    rows = await asyncio.to_thread(db_get_synced_reports)
    if not rows:
        return {"checked": 0, "deleted": 0, "restored": 0}
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}"}

    # 워크스페이스별로 Fabric에 실제 존재하는 report ID 집합을 미리 수집한다.
    # 실패한 워크스페이스는 None → 나중에 건너뜀
    workspace_reports: dict[str, set[str] | None] = {}
    async with httpx.AsyncClient(timeout=60) as client:
        for ws_id in {row["pbi_workspace_id"] for row in rows}:
            try:
                resp = await client.get(f"{PBI_GROUPS}/{ws_id}/reports", headers=headers)
                resp.raise_for_status()
                workspace_reports[ws_id] = {item["id"] for item in resp.json().get("value", [])}
            except Exception as exc:
                workspace_reports[ws_id] = None
                logger.warning("FABRIC SYNC SKIP | workspace=%s | error=%s", ws_id, exc)

    deleted = restored = checked = 0
    for row in rows:
        existing = workspace_reports.get(row["pbi_workspace_id"])
        if existing is None:  # 이 워크스페이스는 조회 실패 → 건너뜀
            continue
        checked += 1
        in_fabric = row["pbi_report_id"] in existing
        # active 보고서가 Fabric에 없으면 → 삭제 처리
        if row["status"] == "active" and not in_fabric:
            if await asyncio.to_thread(
                db_mark_report_deleted, row["id"], row["pbi_report_id"], "workspace sync"
            ):
                deleted += 1
                logger.info("FABRIC SYNC DELETE | report_id=%s | name=%s", row["id"], row["name"])
        # deleted 보고서가 Fabric에 다시 나타나면 → 복구 (재업로드 등으로 살아난 경우)
        elif row["status"] == "deleted" and in_fabric:
            if await asyncio.to_thread(db_restore_report, row["id"], row["pbi_report_id"]):
                restored += 1
                logger.info("FABRIC SYNC RESTORE | report_id=%s | name=%s", row["id"], row["name"])
    return {"checked": checked, "deleted": deleted, "restored": restored}


async def fabric_sync_loop():
    """서버 시작 시 1회 + FABRIC_SYNC_INTERVAL 주기로 Fabric 동기화를 반복한다.

    lifespan에서 asyncio.create_task()로 백그라운드 실행 — 서버 기동을 막지 않는다.
    FABRIC_SYNC_INTERVAL=0 이면 시작 시 1회만 실행하고 루프를 종료한다.
    변경이 없으면 로그를 남기지 않는다 (주기마다 정상 로그가 쌓이지 않도록).
    """
    try:
        logger.info("FABRIC SYNC (startup) | %s", await sync_fabric_reports())
    except Exception:
        logger.exception("STARTUP FABRIC SYNC FAIL")
    if FABRIC_SYNC_INTERVAL <= 0:
        return
    while True:
        await asyncio.sleep(FABRIC_SYNC_INTERVAL)
        try:
            summary = await sync_fabric_reports()
            if summary["deleted"] or summary["restored"]:  # 변경 있을 때만 로그
                logger.info("FABRIC SYNC | %s", summary)
        except Exception:
            logger.exception("FABRIC SYNC FAIL")


# ── Azure AD / Power BI ───────────────────────────────────────────────────────

def get_access_token() -> str:
    """서비스 주체(Client Credentials)로 Azure AD 토큰 발급. 만료 5분 전 자동 갱신."""
    now = time.time()
    if token_cache["access_token"] and now < token_cache["expires_at"] - 300:
        return token_cache["access_token"]
    msal_app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = msal_app.acquire_token_for_client(
        scopes=["https://analysis.windows.net/powerbi/api/.default"]
    )
    if "access_token" not in result:
        raise HTTPException(status_code=500, detail=f"토큰 발급 실패: {result.get('error_description')}")
    token_cache["access_token"] = result["access_token"]
    token_cache["expires_at"] = now + result.get("expires_in", 3600)
    return token_cache["access_token"]


def get_fabric_token() -> str:
    """Fabric REST API용 토큰 발급 (폴더 생성/이동). 만료 5분 전 자동 갱신."""
    now = time.time()
    if fabric_token_cache["access_token"] and now < fabric_token_cache["expires_at"] - 300:
        return fabric_token_cache["access_token"]
    msal_app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = msal_app.acquire_token_for_client(
        scopes=["https://api.fabric.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(f"Fabric 토큰 발급 실패: {result.get('error_description')}")
    fabric_token_cache["access_token"] = result["access_token"]
    fabric_token_cache["expires_at"] = now + result.get("expires_in", 3600)
    return fabric_token_cache["access_token"]


async def get_embed_token(report_id: int, pbi_username: str, roles: str) -> dict:
    """Power BI Embed Token 발급. roles는 DB에 저장된 콤마 구분 문자열."""
    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["pbi_report_id"]:
        raise HTTPException(status_code=404, detail="등록되지 않은 보고서입니다.")
    pbi_report_id = report_row["pbi_report_id"]
    workspace_id = report_row["pbi_workspace_id"] or WORKSPACE_ID
    report_api = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"

    access_token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    embed_url = f"{report_api}/reports/{pbi_report_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(embed_url, headers=headers)
        if resp.status_code == 404:
            # Fabric에서 이미 삭제된 보고서 — 주기 동기화를 기다리지 않고 바로 반영한다.
            await asyncio.to_thread(
                db_mark_report_deleted, report_row["id"], pbi_report_id, "embed 404"
            )
            raise HTTPException(status_code=404, detail="Fabric에서 삭제된 보고서입니다.")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"보고서 조회 실패: {resp.text}")
        report_info = resp.json()

    dataset_id = report_info.get("datasetId", "")
    dataset_info = None
    if dataset_id:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{report_api}/datasets/{dataset_id}", headers=headers)
            if resp.status_code == 200:
                dataset_info = resp.json()

    token_url = f"{report_api}/reports/{pbi_report_id}/GenerateToken"
    body = {"accessLevel": "view"}
    identity_required = report_row["rls_enabled"] or dataset_info is None or dataset_info.get("isEffectiveIdentityRequired")
    if identity_required:
        identity = {"username": pbi_username, "datasets": [dataset_id]}
        roles_required = report_row["rls_enabled"] or dataset_info is None or dataset_info.get("isEffectiveIdentityRolesRequired")
        if roles_required:
            configured_roles = report_row["rls_role_names"]
            identity["roles"] = configured_roles or [role.strip() for role in roles.split(",") if role.strip()]
        body["identities"] = [identity]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(token_url, headers=headers, json=body)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"임베드 토큰 발급 실패: {resp.text}")
        token_data = resp.json()

    return {
        "embed_token": token_data["token"],
        "embed_url":   report_info["embedUrl"],
        "report_id":   pbi_report_id,
        "report_name": report_row["name"],
        "settings": {
            "default_page": report_row["default_page"],
            "enable_filter": report_row["enable_filter"],
            "enable_page_nav": report_row["enable_page_nav"],
            "use_data_bot": report_row["use_data_bot"],
            "tab_type": report_row["tab_type"],
        },
    }


# ── 세션 헬퍼 ─────────────────────────────────────────────────────────────────

async def current_user(request: Request):
    """세션 쿠키에서 username을 읽어 DB에서 사용자 정보 반환. 없으면 None."""
    username = request.session.get("username")
    if not username:
        return None
    return await asyncio.to_thread(db_get_user, username)


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str):
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="잘못된 요청입니다. 페이지를 새로고침해 주세요.")


# ── 라우트 ────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if await current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None, "csrf_token": csrf_token(request)})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(), password: str = Form(), csrf: str = Form()):
    verify_csrf(request, csrf)
    ip = request.client.host
    if not await asyncio.to_thread(db_login_allowed, username, ip):
        logger.warning("LOGIN BLOCK | user=%-12s | ip=%s", username, ip)
        return templates.TemplateResponse(request, "login.html", {
            "error": "로그인 실패 횟수가 많습니다. 15분 후 다시 시도해 주세요.",
            "csrf_token": csrf_token(request),
        }, status_code=429)
    user = await asyncio.to_thread(db_authenticate, username, password)
    await asyncio.to_thread(db_record_login, username, ip, bool(user))
    if not user:
        logger.warning("LOGIN FAIL | user=%-12s | ip=%s", username, ip)
        return templates.TemplateResponse(request, "login.html", {
            "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
            "csrf_token": csrf_token(request),
        })
    request.session["username"] = user["username"]
    logger.info("LOGIN OK   | user=%-12s | ip=%s | name=%s", user["username"], ip, user["display_name"])
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    user = await current_user(request)
    if user:
        logger.info("LOGOUT     | user=%-12s | ip=%s", user["username"], request.client.host)
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "report.html", {
        "user":    user,
        "reports": await asyncio.to_thread(db_get_reports, user["username"]),
        "csrf_token": csrf_token(request),
    })


@app.get("/api/embed/{report_id}")
async def api_embed(request: Request, report_id: int):
    ip = request.client.host
    user = await current_user(request)
    if not user:
        logger.warning("EMBED DENY | user=미로그인       | ip=%s | report_id=%s", ip, report_id)
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    allowed_ids = {row["id"] for row in await asyncio.to_thread(db_get_reports, user["username"])}
    if report_id not in allowed_ids:
        logger.warning("EMBED DENY | user=%-12s | ip=%s | report_id=%s (권한없음)", user["username"], ip, report_id)
        raise HTTPException(status_code=403, detail="해당 보고서에 접근 권한이 없습니다.")
    logger.info("EMBED OK   | user=%-12s | ip=%s | report_id=%s", user["username"], ip, report_id)
    result = await get_embed_token(report_id, user["pbi_username"], user["roles"])
    asyncio.create_task(asyncio.to_thread(db_record_view, report_id, user["id"]))
    return result


@app.post("/api/admin/sync-fabric")
async def api_sync_fabric(request: Request):
    """관리자 수동 동기화: Fabric에서 지운 보고서를 즉시 DB에 반영한다."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="관리자만 실행할 수 있습니다.")
    summary = await sync_fabric_reports()
    logger.info("FABRIC SYNC (manual) | user=%s | %s", user["username"], summary)
    return summary


# ── 보고서 업로드 (웹 게시) ───────────────────────────────────────────────────

async def fabric_get_or_create_folder(folder_name: str) -> str:
    """Fabric 워크스페이스 루트의 사용자 폴더 ID를 반환하고, 없으면 생성한다."""
    token = await asyncio.to_thread(get_fabric_token)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        params = {"recursive": "false"}
        while True:
            resp = await client.get(f"{FABRIC_API}/folders", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            folder = next(
                (item for item in data.get("value", []) if item["displayName"] == folder_name),
                None,
            )
            if folder:
                return folder["id"]
            continuation_token = data.get("continuationToken")
            if not continuation_token:
                break
            params["continuationToken"] = continuation_token

        resp = await client.post(
            f"{FABRIC_API}/folders",
            headers=headers,
            json={"displayName": folder_name},
        )
        if resp.status_code == 409:
            # 동시에 같은 사용자의 업로드가 시작된 경우 폴더 생성 경합을 한 번 재조회한다.
            resp = await client.get(
                f"{FABRIC_API}/folders", headers=headers, params={"recursive": "false"}
            )
            resp.raise_for_status()
            folder = next(
                (item for item in resp.json().get("value", []) if item["displayName"] == folder_name),
                None,
            )
            if folder:
                return folder["id"]
        resp.raise_for_status()
        return resp.json()["id"]


async def fabric_delete_items(report_id: str, dataset_ids: list[str]):
    """이번 import에서 생성된 항목을 Fabric에서 삭제한다 (롤백 전용).

    기존 항목은 절대 건드리지 않는다. 이 함수는 우리가 방금 생성한 항목에만 사용한다.
    """
    token = await asyncio.to_thread(get_fabric_token)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        for item_id in [report_id, *dataset_ids]:
            try:
                await client.delete(f"{FABRIC_API}/items/{item_id}", headers=headers)
            except Exception:
                logger.warning("FABRIC ROLLBACK WARN | item_id=%s 삭제 실패 (수동 정리 필요)", item_id)


async def fabric_rename_new_items(
    report_id: str, dataset_ids: list[str], display_name: str, owner_username: str
) -> str:
    """이번 import에서 생성된 항목을 원래 파일 이름으로 변경한다.

    Fabric 표시 이름은 워크스페이스 전체에서 유일해야 한다 (폴더가 달라도).
    409 충돌 시 fallback 없이 바로 예외를 발생시킨다 — 호출부에서 사용자에게
    이름 변경을 안내하는 에러로 처리한다.
    """
    token = await asyncio.to_thread(get_fabric_token)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        for item_id in [report_id, *dataset_ids]:
            resp = await client.patch(
                f"{FABRIC_API}/items/{item_id}",
                headers=headers,
                json={"displayName": display_name},
            )
            if resp.status_code == 409:
                raise ValueError(f"name_conflict:{display_name}")
            if resp.status_code != 200:
                resp.raise_for_status()
    return display_name


@app.get("/health")
async def health():
    try:
        await asyncio.to_thread(db_health_check)
    except psycopg2.Error:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = File(...), report_name: str = Form("")):
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    try:
        return await process_upload(request, file, report_name)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("UPLOAD UNEXPECTED FAIL | file=%s", file.filename)
        raise HTTPException(status_code=500, detail="업로드 처리 중 오류가 발생했습니다. 관리자 로그를 확인하세요.") from exc


async def process_upload(request: Request, file: UploadFile, report_name: str):
    ip = request.client.host
    user = await current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    if not file.filename or not file.filename.lower().endswith(".pbix"):
        raise HTTPException(status_code=400, detail=".pbix 파일만 업로드할 수 있습니다.")

    name = report_name.strip() or Path(file.filename).stem.strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="보고서 이름은 1~50자여야 합니다.")

    # 신규 생성 전용: 기존 Fabric/DB 보고서는 누구 소유든 수정하지 않는다.
    existing = await asyncio.to_thread(db_find_report, user["id"], name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"'{name}'은(는) 이미 등록된 보고서입니다. 기존 보고서는 변경하지 않으므로 다른 이름을 사용해 주세요.",
        )

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if not file_size:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if file_size > MAX_PBIX_SIZE:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다 (최대 1GB).")
    signature = file.file.read(4)
    file.file.seek(0)
    if signature[:2] != b"PK":
        raise HTTPException(status_code=400, detail="유효한 PBIX 파일이 아닙니다.")

    logger.info(
        "UPLOAD START | user=%-12s | ip=%s | report=%s | file=%s | bytes=%s",
        user["username"], ip, name, file.filename, file_size,
    )

    # Fabric보다 DB에 먼저 기록한다. 이후 오류가 나도 같은 이름을 재게시하지 않는다.
    job_id = await asyncio.to_thread(db_reserve_upload, user["id"], name)
    logger.info(
        "UPLOAD RESERVED | user=%-12s | report=%s | job_id=%s",
        user["username"], name, job_id,
    )

    try:
        access_token = await asyncio.to_thread(get_access_token)
    except Exception as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"token: {exc}")
        raise
    headers = {"Authorization": f"Bearer {access_token}"}

    # Import API의 subfolderObjectId를 사용하면 보고서와 의미 모델이 함께 폴더에 생성된다.
    try:
        folder_id = await fabric_get_or_create_folder(user["username"])
    except Exception as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"folder: {exc}")
        logger.warning("UPLOAD FAIL| user=%-12s | ip=%s | folder=%s", user["username"], ip, exc)
        raise HTTPException(status_code=502, detail=f"사용자 폴더 준비 실패: {exc}") from exc

    # 1) .pbix 신규 게시. Power BI Import API는 워크스페이스 전체 기준으로 이름 충돌을
    # 체크하므로 항상 충돌 없는 내부 이름으로 올리고, 완료 후 표시 이름을 변경한다.
    import_name = f"{user['username']}__{name}__{uuid.uuid4().hex[:12]}"
    async with httpx.AsyncClient(timeout=300) as client:
        try:
            resp = await client.post(
                f"{PBI_API}/imports",
                params={
                    "datasetDisplayName": f"{import_name}.pbix",
                    "nameConflict": "Abort",
                    "subfolderObjectId": folder_id,
                },
                headers=headers,
                files={"file": (f"{import_name}.pbix", file.file, "application/octet-stream")},
            )
        except httpx.RequestError as exc:
            await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message=f"import request: {exc}")
            raise HTTPException(
                status_code=502,
                detail="Fabric 응답을 확인할 수 없어 재업로드를 차단했습니다. 관리자가 작업 상태를 확인해야 합니다.",
            ) from exc
        if resp.status_code == 409:
            await asyncio.to_thread(db_update_upload_job, job_id, "conflict", error_message="Fabric name conflict")
            raise HTTPException(
                status_code=409,
                detail=f"Fabric에 '{name}'과(와) 같은 이름의 항목이 있습니다. 기존 항목은 변경하지 않았습니다.",
            )
        if resp.status_code not in (200, 202):
            await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"import HTTP {resp.status_code}")
            logger.warning("UPLOAD FAIL| user=%-12s | ip=%s | report=%s (%s)", user["username"], ip, name, resp.status_code)
            raise HTTPException(status_code=502, detail=f"게시 요청 실패: {resp.text}")
        import_id = resp.json()["id"]
        await asyncio.to_thread(
            db_update_upload_job, job_id, "accepted",
            import_id=import_id, pbi_workspace_id=WORKSPACE_ID,
        )
        logger.info(
            "UPLOAD ACCEPT | user=%-12s | report=%s | import_id=%s",
            user["username"], name, import_id,
        )

        # 2) Fabric 변환 완료 대기 (.pbix → 데이터셋 + 보고서)
        for _ in range(100):
            await asyncio.sleep(3)
            resp = await client.get(f"{PBI_API}/imports/{import_id}", headers=headers)
            if resp.status_code != 200:
                await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message=f"poll HTTP {resp.status_code}")
                raise HTTPException(status_code=502, detail=f"게시 상태 조회 실패: {resp.text}")
            result = resp.json()
            state = result.get("importState")
            if state == "Succeeded":
                break
            if state == "Failed":
                await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=str(result.get("error")))
                raise HTTPException(status_code=502, detail=f"게시 실패: {result.get('error')}")
        else:
            await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message="poll timeout")
            raise HTTPException(status_code=504, detail="게시 처리 시간 초과. 잠시 후 워크스페이스를 확인하세요.")

    reports  = result.get("reports", [])
    if not reports:
        await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message="Fabric response has no report")
        raise HTTPException(status_code=502, detail="게시는 됐지만 보고서 정보를 받지 못했습니다.")
    pbi_report_id = reports[0]["id"]
    dataset_ids = [dataset["id"] for dataset in result.get("datasets", [])]
    rename_warning = None
    fabric_display_name = name
    try:
        fabric_display_name = await fabric_rename_new_items(pbi_report_id, dataset_ids, name, user["username"])
    except ValueError as exc:
        if str(exc).startswith("name_conflict:"):
            # 방금 생성한 내부 이름 항목을 Fabric에서 제거 (롤백)
            await fabric_delete_items(pbi_report_id, dataset_ids)
            await asyncio.to_thread(db_update_upload_job, job_id, "conflict",
                                    error_message=f"Fabric 이름 충돌: {name}")
            logger.warning("UPLOAD NAME CONFLICT | user=%s | report=%s | Fabric 항목 롤백 완료",
                           user["username"], name)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"워크스페이스에 '{name}'이라는 보고서가 이미 있습니다. "
                    "다른 이름으로 다시 올려주세요. "
                    "(Fabric은 워크스페이스 전체에서 보고서 이름이 유일해야 합니다)"
                ),
            ) from exc
        rename_warning = f"Fabric display name unchanged: {exc}"
        logger.warning("UPLOAD RENAME WARN | user=%s | report=%s | pbi_report_id=%s | error=%s",
                       user["username"], name, pbi_report_id, exc)
    except Exception as exc:
        rename_warning = f"Fabric display name unchanged: {exc}"
        logger.warning("UPLOAD RENAME WARN | user=%s | report=%s | pbi_report_id=%s | error=%s",
                       user["username"], name, pbi_report_id, exc)
    await asyncio.to_thread(
        db_update_upload_job,
        job_id,
        "fabric_succeeded",
        pbi_report_id=pbi_report_id,
        error_message=rename_warning,
    )
    logger.info(
        "UPLOAD FABRIC OK | user=%-12s | report=%s | import_id=%s | pbi_report_id=%s",
        user["username"], name, import_id, pbi_report_id,
    )

    # 3) 게이트웨이 신규 등록: 업로드한 사용자와 관리자에게 권한 부여
    try:
        gateway_report_id = await asyncio.to_thread(
            db_register_report,
            name,
            pbi_report_id,
            user["id"],
            dataset_ids[0] if dataset_ids else None,
            folder_id,
        )
    except psycopg2.Error as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "db_failed", error_message=str(exc))
        logger.exception(
            "UPLOAD DB FAIL | user=%s | report=%s | pbi_report_id=%s",
            user["username"], name, pbi_report_id,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Fabric 게시에는 성공했지만 게이트웨이 DB 등록에 실패했습니다. "
                "같은 파일을 다시 올리지 말고 관리자에게 로그의 Report ID를 전달해 주세요."
            ),
        ) from exc

    await asyncio.to_thread(db_update_upload_job, job_id, "completed", report_id=gateway_report_id)

    logger.info(
        "UPLOAD DB OK | user=%-12s | report=%s | db_report_id=%s | viewers=%s,admin",
        user["username"], name, gateway_report_id, user["username"],
    )

    logger.info("UPLOAD OK  | user=%-12s | ip=%s | report=%s | new=%s | folder=%s",
                user["username"], ip, name, True, user["username"])
    return {
        "report_name":  name,
        "fabric_display_name": fabric_display_name,
        "new":          True,
        "folder":       user["username"],
    }


@app.get("/docs", response_class=HTMLResponse)
async def docs(request: Request):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return templates.TemplateResponse(request, "docs.html", {})
