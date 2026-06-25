"""PostgreSQL 커넥션 풀 + 모든 DB 쿼리 함수."""
import logging
import os
from contextlib import contextmanager

import bcrypt
import psycopg2
import psycopg2.errors
import psycopg2.extras
import psycopg2.pool

from config import (
    DB_CONFIG, WORKSPACE_ID,
    MAX_PERSONAL_REPORTS, MAX_UPLOADS_PER_DAY,
    LOGIN_BLOCK_MAX_FAIL, LOGIN_BLOCK_MINUTES,
)
from errors import AppError

logger = logging.getLogger("powerbi-gateway")

# ── 커넥션 풀 ─────────────────────────────────────────────────────────────────
# 요청마다 연결을 새로 맺지 않도록 프로세스당 풀을 사용한다.
# 모든 쓰기 함수는 명시적으로 commit → 반환 전 rollback으로 트랜잭션 잔재만 정리한다.
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
        broken = True
        raise
    except Exception:
        try:
            conn.rollback()
        except psycopg2.Error:
            broken = True
        raise
    finally:
        db_pool.putconn(conn, close=broken)


# ── 헬스 ─────────────────────────────────────────────────────────────────────

def db_health_check():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")


# ── 인증 ─────────────────────────────────────────────────────────────────────

def db_check_and_get_user(username: str, ip: str):
    """로그인 차단 확인 + 사용자 행 조회를 하나의 DB 커넥션에서 처리.

    반환: ("blocked", None) | ("ok", row | None)

    기존에는 db_login_allowed → db_authenticate 로 두 번 커넥션을 열었다.
    차단 체크와 사용자 SELECT를 같은 커넥션 안에서 순서대로 실행해 1회로 줄인다.
    bcrypt 비교는 CPU 집약적이므로 DB 커넥션을 닫은 뒤 호출자가 수행한다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS count FROM login_attempts "
                "WHERE username = %s AND ip_address = %s AND succeeded = FALSE "
                "AND attempted_at >= NOW() - %s * INTERVAL '1 minute'",
                (username, ip, LOGIN_BLOCK_MINUTES),
            )
            if cur.fetchone()["count"] >= LOGIN_BLOCK_MAX_FAIL:
                return "blocked", None
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, password, is_admin, is_active "
                "FROM users WHERE username = %s",
                (username,),
            )
            return "ok", cur.fetchone()


def db_verify_password(row, password: str):
    """bcrypt 비교 후 사용자 정보 반환. DB 접근 없는 순수 CPU 연산.

    반환: user dict(성공) / "inactive"(비활성 계정) / None(아이디·비밀번호 불일치)
    """
    if not row:
        return None
    if not bcrypt.checkpw(password.encode(), row["password"].encode()):
        return None
    if not row["is_active"]:
        return "inactive"
    return row


def db_record_login(username: str, ip: str, succeeded: bool):
    """로그인 시도를 기록한다. 성공 시 실패 이력 초기화 + last_login_at 갱신."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if succeeded:
                cur.execute("DELETE FROM login_attempts WHERE username = %s AND ip_address = %s", (username, ip))
                cur.execute("UPDATE users SET last_login_at = NOW() WHERE username = %s", (username,))
            cur.execute(
                "INSERT INTO login_attempts (username, ip_address, succeeded) VALUES (%s, %s, %s)",
                (username, ip, succeeded),
            )
        conn.commit()


def db_cleanup_login_attempts():
    """30일 초과 로그인 시도 기록을 삭제한다.

    기존에는 db_record_login() 안에서 매 로그인마다 DELETE를 실행했다.
    로그인 응답 경로에서 제거하고 서버 시작 시 + 일 1회 백그라운드에서 실행한다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM login_attempts WHERE attempted_at < NOW() - INTERVAL '30 days'")
        conn.commit()


# ── 사용자 ────────────────────────────────────────────────────────────────────

def db_get_user(username: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, is_admin "
                "FROM users WHERE username = %s",
                (username,),
            )
            return cur.fetchone()


# ── 보고서 ────────────────────────────────────────────────────────────────────

def db_get_reports(username: str) -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.owner_id, r.category,
                          owner.username AS owner_username,
                          s.preview_image_url, s.tab_type
                   FROM user_reports ur
                   JOIN reports r     ON r.id = ur.report_id
                   JOIN report_meta m ON m.report_id = r.id
                   LEFT JOIN report_settings s ON s.report_id = r.id
                   LEFT JOIN users owner ON owner.id = r.owner_id
                   JOIN users   u ON u.id = ur.user_id
                   WHERE u.username = %s AND ur.can_view = TRUE AND r.status = 'active'
                   ORDER BY r.category NULLS LAST, r.name""",
                (username,),
            )
            return cur.fetchall()


def db_get_all_active_reports() -> list:
    """관리자용: 권한 무관하게 active 보고서 전체 반환."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.owner_id, r.category,
                          owner.username AS owner_username,
                          s.preview_image_url, s.tab_type
                   FROM reports r
                   JOIN report_meta m ON m.report_id = r.id
                   LEFT JOIN report_settings s ON s.report_id = r.id
                   LEFT JOIN users owner ON owner.id = r.owner_id
                   WHERE r.status = 'active'
                   ORDER BY r.category NULLS LAST, r.name"""
            )
            return cur.fetchall()


def db_hard_delete_report(report_id: int) -> bool:
    """보고서를 DB에서 완전히 삭제한다 (CASCADE로 하위 테이블 자동 정리)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reports WHERE id = %s", (report_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def db_can_view_report(username: str, report_id: int) -> bool:
    """사용자가 해당 보고서를 열람할 수 있는지 단건 조회."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM user_reports ur
                   JOIN reports r ON r.id = ur.report_id
                   JOIN users   u ON u.id = ur.user_id
                   WHERE u.username = %s AND ur.report_id = %s
                     AND ur.can_view = TRUE AND r.status = 'active'""",
                (username, report_id),
            )
            return cur.fetchone() is not None


def db_get_report(report_id: int):
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
    """같은 이름의 '살아있는' 보고서 조회. deleted 상태는 재사용 가능."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.owner_id, m.pbi_report_id
                   FROM reports r
                   LEFT JOIN report_meta m ON m.report_id = r.id
                   WHERE r.owner_id = %s AND LOWER(r.name) = LOWER(%s) AND r.status <> 'deleted'""",
                (owner_id, name),
            )
            return cur.fetchone()


# ── 업로드 잡 ─────────────────────────────────────────────────────────────────

def db_reserve_upload(user_id: int, report_name: str) -> int:
    """업로드 예약. DB 제약으로 다중 프로세스 경합을 막는다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (user_id,))
            cur.execute(
                "SELECT COUNT(*) AS count FROM reports WHERE owner_id = %s AND report_type = 'personal'",
                (user_id,),
            )
            if cur.fetchone()["count"] >= MAX_PERSONAL_REPORTS:
                raise AppError.RATE_PERSONAL_MAX.http(max=MAX_PERSONAL_REPORTS)
            cur.execute(
                "SELECT COUNT(*) AS count FROM upload_jobs WHERE user_id = %s AND created_at >= CURRENT_DATE",
                (user_id,),
            )
            if cur.fetchone()["count"] >= MAX_UPLOADS_PER_DAY:
                raise AppError.RATE_UPLOAD_DAILY.http(max=MAX_UPLOADS_PER_DAY)
            try:
                cur.execute(
                    "INSERT INTO upload_jobs (user_id, report_name, status) VALUES (%s, %s, 'publishing') RETURNING id",
                    (user_id, report_name),
                )
                row = cur.fetchone()
            except psycopg2.errors.UniqueViolation as exc:
                conn.rollback()
                raise AppError.UPLOAD_IN_PROGRESS.http(name=report_name) from exc
        conn.commit()
    return row["id"]


def db_get_upload_job(job_id: int, user_id: int) -> dict | None:
    """업로드 잡 단건 조회. user_id로 소유자 검증."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, report_name, report_id, error_message "
                "FROM upload_jobs WHERE id = %s AND user_id = %s",
                (job_id, user_id),
            )
            return cur.fetchone()


def db_update_upload_job(job_id: int, status: str, **values):
    allowed = {"import_id", "pbi_report_id", "error_message", "pbi_workspace_id", "report_id"}
    updates = {k: v for k, v in values.items() if k in allowed}
    assignments = ["status = %s", "updated_at = NOW()"]
    params = [status]
    for k, v in updates.items():
        assignments.append(f"{k} = %s")
        params.append(v)
    params.append(job_id)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE upload_jobs SET {', '.join(assignments)} WHERE id = %s", params)
        conn.commit()


def db_register_report(
    name: str,
    pbi_report_id: str,
    owner_id: int,
    pbi_dataset_id: str | None = None,
    pbi_workspace_id: str | None = None,
    pbi_display_name: str | None = None,
    category: str | None = None,
):
    """업로드된 보고서를 등록하고 소유자와 관리자에게 열람 권한을 부여한다.

    category는 Fabric 폴더명(= 업로더 username)으로 전달하면 사이드바 폴더 트리에 반영된다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("LOCK TABLE reports IN SHARE ROW EXCLUSIVE MODE")
            cur.execute(
                """SELECT setval(
                       pg_get_serial_sequence('reports', 'id'),
                       GREATEST((SELECT COALESCE(MAX(id), 0) FROM reports), 1),
                       (SELECT COUNT(*) > 0 FROM reports)
                   )"""
            )
            cur.execute(
                "SELECT id FROM reports WHERE owner_id = %s AND LOWER(name) = LOWER(%s) FOR UPDATE",
                (owner_id, name),
            )
            existing = cur.fetchone()
            if existing:
                report_id = existing["id"]
                cur.execute(
                    "UPDATE reports SET status = 'active', deleted_at = NULL, updated_at = NOW(), "
                    "updated_by = %s, category = COALESCE(category, %s) WHERE id = %s",
                    (owner_id, category, report_id),
                )
            else:
                cur.execute(
                    "INSERT INTO reports (name, report_type, owner_id, status, category, created_by, updated_by) "
                    "VALUES (%s, 'personal', %s, 'active', %s, %s, %s) RETURNING id",
                    (name, owner_id, category, owner_id, owner_id),
                )
                report_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO report_meta (
                       report_id, pbi_report_id, pbi_workspace_id,
                       pbi_dataset_id, pbi_display_name
                   ) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (report_id) DO UPDATE SET
                       pbi_report_id    = EXCLUDED.pbi_report_id,
                       pbi_workspace_id = EXCLUDED.pbi_workspace_id,
                       pbi_dataset_id   = EXCLUDED.pbi_dataset_id,
                       pbi_display_name = EXCLUDED.pbi_display_name,
                       updated_at       = NOW()""",
                (report_id, pbi_report_id, pbi_workspace_id or WORKSPACE_ID,
                 pbi_dataset_id, pbi_display_name),
            )
            cur.execute("INSERT INTO report_settings (report_id) VALUES (%s) ON CONFLICT DO NOTHING", (report_id,))
            cur.execute("INSERT INTO report_rls (report_id) VALUES (%s) ON CONFLICT DO NOTHING", (report_id,))
            cur.execute(
                """INSERT INTO user_reports (user_id, report_id, can_view, can_edit, can_manage, granted_by)
                   VALUES (%s, %s, TRUE, TRUE, TRUE, %s)
                   ON CONFLICT (user_id, report_id) DO UPDATE SET can_view=TRUE, can_edit=TRUE, can_manage=TRUE""",
                (owner_id, report_id, owner_id),
            )
            cur.execute(
                """INSERT INTO user_reports (user_id, report_id, can_view, can_edit, can_manage, granted_by)
                   SELECT id, %s, TRUE, TRUE, TRUE, %s FROM users WHERE is_admin=TRUE AND id<>%s
                   ON CONFLICT (user_id, report_id) DO UPDATE SET can_view=TRUE, can_edit=TRUE, can_manage=TRUE""",
                (report_id, owner_id, owner_id),
            )
            cur.execute(
                """INSERT INTO report_audit_log (report_id, actor_user_id, action, details)
                   VALUES (%s, %s, 'personal_report_registered',
                           jsonb_build_object('pbi_report_id', %s, 'name', %s))""",
                (report_id, owner_id, pbi_report_id, name),
            )
        conn.commit()
    return report_id


# ── Fabric 동기화 보조 ────────────────────────────────────────────────────────

def db_get_synced_reports():
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


def db_mark_report_deleted(report_id: int, pbi_report_id: str, reason: str) -> bool:
    """PBI 워크스페이스에서 보고서가 사라진 것을 DB에 반영한다 (소프트 삭제)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET status='deleted', deleted_at=NOW(), updated_at=NOW() WHERE id=%s AND status<>'deleted'",
                (report_id,),
            )
            if cur.rowcount:
                cur.execute(
                    """INSERT INTO report_audit_log (report_id, action, details)
                       VALUES (%s, 'pbi_deleted', jsonb_build_object('pbi_report_id', %s, 'reason', %s))""",
                    (report_id, pbi_report_id, reason),
                )
        conn.commit()
        return bool(cur.rowcount)


def db_restore_report(report_id: int, pbi_report_id: str) -> bool:
    """삭제 처리됐던 보고서가 PBI 워크스페이스에 다시 나타나면 active로 되돌린다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET status='active', deleted_at=NULL, updated_at=NOW() WHERE id=%s AND status='deleted'",
                (report_id,),
            )
            if cur.rowcount:
                cur.execute(
                    "INSERT INTO report_audit_log (report_id, action, details) VALUES (%s, 'pbi_restored', jsonb_build_object('pbi_report_id', %s))",
                    (report_id, pbi_report_id),
                )
        conn.commit()
        return bool(cur.rowcount)


def db_get_pending_imports():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.user_id, j.report_name, j.import_id, u.username
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   WHERE j.status IN ('accepted', 'unknown') AND j.import_id IS NOT NULL
                   ORDER BY j.id"""
            )
            return cur.fetchall()


def db_get_recoverable_jobs():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.user_id, j.report_name, j.pbi_report_id, j.pbi_workspace_id,
                          u.username
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   WHERE j.status IN ('pbi_succeeded', 'db_failed') AND j.pbi_report_id IS NOT NULL
                   ORDER BY j.id"""
            )
            return cur.fetchall()


# ── 관리자 ────────────────────────────────────────────────────────────────────

def db_admin_get_stats() -> dict:
    """관리자 대시보드 통계. 4개의 개별 쿼리를 스칼라 서브쿼리 1개로 통합해 왕복 1회로 줄인다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                    (SELECT COUNT(*) FROM users       WHERE is_active = TRUE)           AS active_users,
                    (SELECT COUNT(*) FROM reports     WHERE status    = 'active')        AS active_reports,
                    (SELECT COUNT(*) FROM upload_jobs WHERE created_at >= CURRENT_DATE)  AS today_uploads,
                    (SELECT COUNT(*) FROM upload_jobs WHERE status = 'completed'
                                                       AND created_at >= CURRENT_DATE)  AS today_success"""
            )
            row = cur.fetchone()
    return dict(row)


def db_admin_get_users() -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.username, u.display_name, u.pbi_username, u.roles,
                          u.is_admin, u.is_active, u.last_login_at, u.created_at,
                          COUNT(DISTINCT ur.report_id) AS report_count
                   FROM users u
                   LEFT JOIN user_reports ur ON ur.user_id = u.id
                   GROUP BY u.id ORDER BY u.id"""
            )
            return cur.fetchall()


def db_admin_add_user(username: str, pw_hash: str, display_name: str,
                      pbi_username: str, roles: str, is_admin: bool) -> int:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password, display_name, pbi_username, roles, is_admin) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (username, pw_hash, display_name, pbi_username, roles, is_admin),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def db_admin_toggle_user_active(user_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = NOT is_active, updated_at = NOW() "
                "WHERE id = %s AND username != 'admin' RETURNING is_active",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["is_active"] if row else None


def db_admin_get_reports() -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.status, r.created_at, r.category,
                          u.username AS owner_username,
                          m.pbi_report_id, m.pbi_display_name, m.pbi_dataset_id,
                          COALESCE(m.pbi_workspace_id, %s) AS pbi_workspace_id,
                          COUNT(ur.user_id) AS viewer_count
                   FROM reports r
                   LEFT JOIN users u ON u.id = r.owner_id
                   LEFT JOIN report_meta m ON m.report_id = r.id
                   LEFT JOIN user_reports ur ON ur.report_id = r.id AND ur.can_view = TRUE
                   WHERE r.status <> 'deleted'
                   GROUP BY r.id, u.username, m.pbi_report_id, m.pbi_display_name, m.pbi_dataset_id, m.pbi_workspace_id
                   ORDER BY r.id""",
                (WORKSPACE_ID,)
            )
            return cur.fetchall()


def db_import_managed_report(
    pbi_report_id: str,
    name: str,
    pbi_dataset_id: str | None,
    pbi_workspace_id: str,
    folder_id: str | None,
    category: str | None,
    actor_id: int,
) -> bool:
    """PBI에서 가져온 공용 보고서를 DB에 등록한다.

    이미 등록된 보고서(pbi_report_id 기준)는 건너뛰고 False를 반환한다.
    신규 등록 성공 시 True를 반환한다.
    권한은 부여하지 않는다 — 관리자가 보고서 관리 화면에서 별도로 설정한다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT r.id FROM report_meta rm JOIN reports r ON r.id = rm.report_id "
                "WHERE rm.pbi_report_id = %s",
                (pbi_report_id,),
            )
            existing = cur.fetchone()
            if existing:
                if category:
                    cur.execute(
                        "UPDATE reports SET category = %s, updated_at = NOW() "
                        "WHERE id = %s AND category IS DISTINCT FROM %s",
                        (category, existing["id"], category),
                    )
                    conn.commit()
                return False
            cur.execute(
                """INSERT INTO reports (name, report_type, owner_id, status, category, created_by, updated_by)
                   VALUES (%s, 'managed', NULL, 'active', %s, %s, %s)
                   RETURNING id""",
                (name, category, actor_id, actor_id),
            )
            report_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO report_meta (report_id, pbi_report_id, pbi_workspace_id, pbi_dataset_id, folder_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (report_id, pbi_report_id, pbi_workspace_id, pbi_dataset_id, folder_id),
            )
            cur.execute("INSERT INTO report_settings (report_id) VALUES (%s)", (report_id,))
            cur.execute("INSERT INTO report_rls (report_id) VALUES (%s)", (report_id,))
            cur.execute(
                """INSERT INTO report_audit_log (report_id, actor_user_id, action, details)
                   VALUES (%s, %s, 'managed_report_imported',
                           jsonb_build_object('pbi_report_id', %s, 'name', %s, 'category', %s))""",
                (report_id, actor_id, pbi_report_id, name, category),
            )
        conn.commit()
    return True


def db_admin_set_category(report_id: int, category: str | None, admin_user_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET category=%s, updated_at=NOW(), updated_by=%s WHERE id=%s",
                (category or None, admin_user_id, report_id),
            )
            cur.execute(
                "INSERT INTO report_audit_log (report_id, actor_user_id, action, details) "
                "VALUES (%s, %s, 'admin_set_category', jsonb_build_object('category', %s))",
                (report_id, admin_user_id, category),
            )
        conn.commit()


def db_admin_soft_delete_report(report_id: int, admin_user_id: int) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET status='deleted', deleted_at=NOW(), updated_at=NOW(), updated_by=%s "
                "WHERE id=%s AND status!='deleted' RETURNING id",
                (admin_user_id, report_id),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO report_audit_log (report_id, actor_user_id, action, details) "
                    "VALUES (%s, %s, 'admin_deleted', '{}'::jsonb)",
                    (report_id, admin_user_id),
                )
        conn.commit()
    return bool(row)


def db_admin_get_upload_jobs(limit: int = 30) -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.report_name, j.status, j.error_message,
                          j.created_at, j.updated_at, u.username
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   ORDER BY j.id DESC LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()


def db_get_report_access(report_id: int) -> list:
    """보고서에 대한 모든 활성 사용자의 열람 권한 현황을 반환한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.username, u.display_name, u.is_admin,
                          COALESCE(ur.can_view, FALSE) AS can_view
                   FROM users u
                   LEFT JOIN user_reports ur ON ur.user_id = u.id AND ur.report_id = %s
                   WHERE u.is_active = TRUE
                   ORDER BY u.is_admin DESC, u.username""",
                (report_id,),
            )
            return cur.fetchall()


def db_set_report_access(report_id: int, user_id: int, can_view: bool, granted_by: int) -> None:
    """보고서에 대한 특정 사용자의 열람 권한을 설정한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if can_view:
                cur.execute(
                    """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (user_id, report_id) DO UPDATE SET can_view = TRUE, granted_by = EXCLUDED.granted_by""",
                    (user_id, report_id, granted_by),
                )
            else:
                cur.execute(
                    "UPDATE user_reports SET can_view = FALSE WHERE user_id = %s AND report_id = %s",
                    (user_id, report_id),
                )
        conn.commit()


def db_update_app_config(key: str, value: str) -> None:
    """app_config 키를 INSERT OR UPDATE한다 (관리자 포털 런타임 설정 변경용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO app_config (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = NOW()""",
                (key, value),
            )
