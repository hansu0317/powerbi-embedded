"""PostgreSQL 커넥션 풀 + 모든 DB 쿼리 함수."""
import logging
import os
from contextlib import contextmanager

import bcrypt
import psycopg2
import psycopg2.errors
import psycopg2.extras
import psycopg2.pool

import config
from config import DB_CONFIG, WORKSPACE_ID
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
                (username, ip, config.LOGIN_BLOCK_MINUTES),
            )
            if cur.fetchone()["count"] >= config.LOGIN_BLOCK_MAX_FAIL:
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
    """세션 사용자 조회. 비활성 계정은 None — 로그인 이후 비활성화돼도 다음 요청부터 즉시 차단된다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, is_admin "
                "FROM users WHERE username = %s AND is_active = TRUE",
                (username,),
            )
            return cur.fetchone()


# ── 보고서 ────────────────────────────────────────────────────────────────────

def db_get_reports(username: str) -> list:
    """사용자가 열람 가능한 보고서 목록 — 직접 부여 + 그룹 부여 합집합."""
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
                   JOIN users u ON u.username = %s
                   WHERE r.status = 'active' AND (
                       EXISTS (SELECT 1 FROM user_reports ur
                               WHERE ur.user_id = u.id AND ur.report_id = r.id AND ur.can_view)
                    OR EXISTS (SELECT 1 FROM user_groups ug
                               JOIN group_reports gr ON gr.group_id = ug.group_id
                               WHERE ug.user_id = u.id AND gr.report_id = r.id AND gr.can_view))
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


def db_get_user_favorites(user_id: int) -> list:
    """사용자의 즐겨찾기 보고서 ID 목록 (active 보고서만, 최신 등록순)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT f.report_id
                   FROM user_favorites f
                   JOIN reports r ON r.id = f.report_id
                   WHERE f.user_id = %s AND r.status = 'active'
                   ORDER BY f.created_at DESC""",
                (user_id,),
            )
            return [row["report_id"] for row in cur.fetchall()]


def db_set_favorite(user_id: int, report_id: int, on: bool) -> None:
    """즐겨찾기 추가/해제 (멱등)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if on:
                cur.execute(
                    "INSERT INTO user_favorites (user_id, report_id) VALUES (%s, %s) "
                    "ON CONFLICT (user_id, report_id) DO NOTHING",
                    (user_id, report_id),
                )
            else:
                cur.execute(
                    "DELETE FROM user_favorites WHERE user_id = %s AND report_id = %s",
                    (user_id, report_id),
                )
        conn.commit()


def db_get_user_recents(user_id: int, limit: int = 8) -> list:
    """사용자의 최근 본 보고서 ID 목록 (active 보고서만, 최신순)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.report_id
                   FROM user_recent_reports t
                   JOIN reports r ON r.id = t.report_id
                   WHERE t.user_id = %s AND r.status = 'active'
                   ORDER BY t.viewed_at DESC LIMIT %s""",
                (user_id, limit),
            )
            return [row["report_id"] for row in cur.fetchall()]


def db_add_recent(user_id: int, report_id: int, keep: int = 30) -> None:
    """최근 본 보고서 기록 (이미 있으면 viewed_at 갱신). 최신 keep건만 남기고 정리."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_recent_reports (user_id, report_id) VALUES (%s, %s) "
                "ON CONFLICT (user_id, report_id) DO UPDATE SET viewed_at = NOW()",
                (user_id, report_id),
            )
            cur.execute(
                """DELETE FROM user_recent_reports
                   WHERE user_id = %s AND report_id NOT IN (
                       SELECT report_id FROM user_recent_reports
                       WHERE user_id = %s ORDER BY viewed_at DESC LIMIT %s)""",
                (user_id, user_id, keep),
            )
        conn.commit()


def db_get_pbi_report_map() -> dict:
    """활성 보고서의 pbi_report_id → {category, name} 매핑 (Fabric 드리프트 비교용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.pbi_report_id, r.category, r.name
                   FROM report_meta m JOIN reports r ON r.id = m.report_id
                   WHERE r.status = 'active' AND m.pbi_report_id IS NOT NULL"""
            )
            return {
                row["pbi_report_id"]: {"category": row["category"], "name": row["name"]}
                for row in cur.fetchall()
            }


def db_hard_delete_report(report_id: int) -> bool:
    """보고서를 DB에서 완전히 삭제한다 (CASCADE로 하위 테이블 자동 정리)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reports WHERE id = %s", (report_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def db_can_view_report(username: str, report_id: int) -> bool:
    """사용자가 해당 보고서를 열람할 수 있는지 단건 조회.

    열람 가능 = 직접 부여(user_reports) OR 소속 그룹에 부여(group_reports)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM users u
                   JOIN reports r ON r.id = %s AND r.status = 'active'
                   WHERE u.username = %s AND (
                       EXISTS (SELECT 1 FROM user_reports ur
                               WHERE ur.user_id = u.id AND ur.report_id = r.id AND ur.can_view)
                    OR EXISTS (SELECT 1 FROM user_groups ug
                               JOIN group_reports gr ON gr.group_id = ug.group_id
                               WHERE ug.user_id = u.id AND gr.report_id = r.id AND gr.can_view))""",
                (report_id, username),
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
                "SELECT COUNT(*) AS count FROM reports "
                "WHERE owner_id = %s AND report_type = 'personal' AND status <> 'deleted'",
                (user_id,),
            )
            if cur.fetchone()["count"] >= config.MAX_PERSONAL_REPORTS:
                raise AppError.RATE_PERSONAL_MAX.http(max=config.MAX_PERSONAL_REPORTS)
            cur.execute(
                "SELECT COUNT(*) AS count FROM upload_jobs WHERE user_id = %s AND created_at >= CURRENT_DATE",
                (user_id,),
            )
            if cur.fetchone()["count"] >= config.MAX_UPLOADS_PER_DAY:
                raise AppError.RATE_UPLOAD_DAILY.http(max=config.MAX_UPLOADS_PER_DAY)
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


def db_count_other_reports_using_dataset(pbi_dataset_id: str, exclude_report_id: int) -> int:
    """같은 PBI 데이터셋을 쓰는 다른 활성 보고서 수.

    관리자 삭제 시 데이터셋까지 지워도 되는지 판단용 — 0이면 안전하게 삭제 가능."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS count
                   FROM report_meta m JOIN reports r ON r.id = m.report_id
                   WHERE m.pbi_dataset_id = %s AND m.report_id <> %s AND r.status <> 'deleted'""",
                (pbi_dataset_id, exclude_report_id),
            )
            return cur.fetchone()["count"]


def db_fail_stuck_upload_job(job_id: int) -> bool:
    """예상 밖 예외로 중단된 업로드 잡을 실패 처리한다 (업로드 태스크의 최후 방어선).

    publishing·accepted만 대상 — 그 외 상태(failed/conflict/unknown/pbi_succeeded/
    db_failed)는 각 실패 지점에서 이미 기록됐거나 재시작 복구 대상이므로 덮지 않는다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE upload_jobs
                   SET status = 'failed', updated_at = NOW(),
                       error_message = '예상치 못한 오류로 업로드가 중단되었습니다. 다시 시도해 주세요.'
                   WHERE id = %s AND status IN ('publishing', 'accepted')""",
                (job_id,),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def db_fail_stale_publishing_jobs() -> int:
    """서버 시작 시 고아가 된 'publishing' 잡을 실패 처리한다.

    publishing(파일 수신~Import 접수 전)은 메모리의 업로드 태스크만 진행시킬 수
    있으므로, 재시작 직후 남아 있으면 전부 복구 불가다. 방치하면 부분 UNIQUE
    인덱스 때문에 같은 이름 재업로드가 계속 409로 막힌다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE upload_jobs
                   SET status = 'failed', updated_at = NOW(),
                       error_message = '서버 재시작으로 업로드가 중단되었습니다. 다시 업로드해 주세요.'
                   WHERE status = 'publishing'"""
            )
            count = cur.rowcount
        conn.commit()
    return count


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
    """업로드된 보고서를 등록하고 소유자에게 열람 권한을 부여한다.

    관리자는 권한 확인을 우회(is_admin)하므로 user_reports 행을 만들지 않는다 —
    만들면 '열람권한' 수만 부풀린다.
    category는 Fabric 폴더 경로(개인 보고서는 업로더 username)로 전달하면 사이드바 폴더 트리에 반영된다.
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
    """사용자 목록 + 열람 가능 보고서 수 (직접 부여 + 그룹 경유, active만)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.username, u.display_name, u.pbi_username, u.roles,
                          u.is_admin, u.is_active, u.last_login_at, u.created_at,
                          (SELECT COUNT(*) FROM reports r
                           WHERE r.status = 'active' AND (
                               EXISTS (SELECT 1 FROM user_reports ur
                                       WHERE ur.user_id = u.id AND ur.report_id = r.id AND ur.can_view)
                            OR EXISTS (SELECT 1 FROM user_groups ug
                                       JOIN group_reports gr ON gr.group_id = ug.group_id
                                       WHERE ug.user_id = u.id AND gr.report_id = r.id AND gr.can_view))
                          ) AS report_count
                   FROM users u ORDER BY u.id"""
            )
            return cur.fetchall()


def db_get_user_report_list(user_id: int) -> list:
    """사용자가 열람 가능한 보고서 목록 + 경로(직접 부여 여부, 경유 그룹명들).

    관리자 포털 사용자 화면의 '보고서 N' 클릭 팝업용."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.category,
                          (ur.user_id IS NOT NULL) AS direct,
                          COALESCE(ARRAY_AGG(DISTINCT g.name)
                                   FILTER (WHERE g.name IS NOT NULL), '{}') AS via_groups
                   FROM reports r
                   LEFT JOIN user_reports ur
                          ON ur.report_id = r.id AND ur.user_id = %s AND ur.can_view
                   LEFT JOIN user_groups ug ON ug.user_id = %s
                   LEFT JOIN group_reports gr
                          ON gr.group_id = ug.group_id AND gr.report_id = r.id AND gr.can_view
                   LEFT JOIN groups g ON g.id = gr.group_id
                   WHERE r.status = 'active'
                     AND (ur.user_id IS NOT NULL OR gr.group_id IS NOT NULL)
                   GROUP BY r.id, r.name, r.category, ur.user_id
                   ORDER BY r.category NULLS LAST, r.name""",
                (user_id, user_id),
            )
            return cur.fetchall()


def db_admin_add_user(username: str, pw_hash: str, display_name: str,
                      pbi_username: str, roles: list[str], is_admin: bool) -> int:
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
                          COUNT(ur.user_id) FILTER (WHERE NOT vu.is_admin) AS viewer_count,
                          (SELECT COUNT(*) FROM group_reports gr
                           WHERE gr.report_id = r.id AND gr.can_view) AS group_count
                   FROM reports r
                   LEFT JOIN users u ON u.id = r.owner_id
                   LEFT JOIN report_meta m ON m.report_id = r.id
                   LEFT JOIN user_reports ur ON ur.report_id = r.id AND ur.can_view = TRUE
                   LEFT JOIN users vu ON vu.id = ur.user_id
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


def db_get_app_config() -> list:
    """app_config 전체 행 (관리자 포털 설정 화면용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value, description, updated_at FROM app_config ORDER BY key")
            return cur.fetchall()


# ── 그룹 (팀/부서 단위 권한) ─────────────────────────────────────────────────

def db_admin_get_groups() -> list:
    """그룹 목록 + 멤버 수 + 부여된 보고서 수."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT g.id, g.name, g.description, g.created_at,
                          (SELECT COUNT(*) FROM user_groups ug WHERE ug.group_id = g.id) AS member_count,
                          (SELECT COUNT(*) FROM group_reports gr
                           WHERE gr.group_id = g.id AND gr.can_view) AS report_count
                   FROM groups g ORDER BY g.name"""
            )
            return cur.fetchall()


def db_admin_create_group(name: str, description: str, actor_id: int) -> int:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO groups (name, description, created_by) VALUES (%s, %s, %s) RETURNING id",
                (name, description or None, actor_id),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def db_admin_delete_group(group_id: int) -> bool:
    """그룹 삭제 — 멤버·보고서 부여는 CASCADE로 함께 제거된다 (개별 부여는 무관)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM groups WHERE id = %s", (group_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def db_get_group_members(group_id: int) -> list:
    """활성 사용자 전체 + 이 그룹 소속 여부 (그룹 멤버 편집 모달용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.username, u.display_name, u.is_admin,
                          (ug.user_id IS NOT NULL) AS is_member
                   FROM users u
                   LEFT JOIN user_groups ug ON ug.user_id = u.id AND ug.group_id = %s
                   WHERE u.is_active = TRUE
                   ORDER BY u.is_admin DESC, u.username""",
                (group_id,),
            )
            return cur.fetchall()


def db_set_group_member(group_id: int, user_id: int, member: bool, added_by: int) -> None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            if member:
                cur.execute(
                    """INSERT INTO user_groups (user_id, group_id, added_by) VALUES (%s, %s, %s)
                       ON CONFLICT (user_id, group_id) DO NOTHING""",
                    (user_id, group_id, added_by),
                )
            else:
                cur.execute(
                    "DELETE FROM user_groups WHERE user_id = %s AND group_id = %s",
                    (user_id, group_id),
                )
        conn.commit()


def db_get_report_group_access(report_id: int) -> list:
    """그룹 전체 + 이 보고서 부여 여부 (권한 모달 '그룹' 탭용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT g.id, g.name,
                          (SELECT COUNT(*) FROM user_groups ug WHERE ug.group_id = g.id) AS member_count,
                          COALESCE(gr.can_view, FALSE) AS can_view
                   FROM groups g
                   LEFT JOIN group_reports gr ON gr.group_id = g.id AND gr.report_id = %s
                   ORDER BY g.name""",
                (report_id,),
            )
            return cur.fetchall()


def db_set_report_group_access(report_id: int, group_id: int, can_view: bool, granted_by: int) -> None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            if can_view:
                cur.execute(
                    """INSERT INTO group_reports (group_id, report_id, can_view, granted_by)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (group_id, report_id) DO UPDATE
                       SET can_view = TRUE, granted_by = EXCLUDED.granted_by""",
                    (group_id, report_id, granted_by),
                )
            else:
                cur.execute(
                    "DELETE FROM group_reports WHERE group_id = %s AND report_id = %s",
                    (group_id, report_id),
                )
        conn.commit()


def db_update_app_config(key: str, value: str) -> bool:
    """존재하는 app_config 키의 값을 갱신한다. 없는 키는 거부(False).

    키 생성은 마이그레이션 시드에서만 한다 — 오타 키가 조용히 쌓이는 것을 방지."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_config SET value = %s, updated_at = NOW() WHERE key = %s",
                (value, key),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated
