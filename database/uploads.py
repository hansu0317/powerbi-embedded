"""업로드 잡 상태 머신 + Fabric 동기화 보조 쿼리."""
import psycopg2.errors

import config
from config import WORKSPACE_ID
from database.pool import db_conn
from errors import AppError

# ── 업로드 잡 ─────────────────────────────────────────────────────────────────

def db_reserve_upload(user_id: int, report_name: str) -> int:
    """업로드 예약. DB 제약으로 다중 프로세스 경합을 막는다.

    일반 업로드는 항상 새 보고서를 만든다(같은 이름이 이미 있으면 라우트가 이
    함수를 부르기 전에 거부함 — routes/report.py의 _read_and_validate_pbix
    참고) — 그래서 개인 보고서 개수 한도는 매번 검사한다."""
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
    description: str | None = None,
    portal_folder_id: int | None = None,
    visibility: str = "personal",
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
                    "updated_by = %s, category = COALESCE(%s, category), portal_folder_id=COALESCE(%s,portal_folder_id), visibility=%s, "
                    "description = COALESCE(%s, description), "
                    "pbi_report_id = %s, pbi_workspace_id = %s, pbi_dataset_id = %s, pbi_display_name = %s "
                    "WHERE id = %s",
                    (owner_id, category, portal_folder_id, visibility, description, pbi_report_id,
                     config.resolve_workspace_id(pbi_workspace_id), pbi_dataset_id, pbi_display_name, report_id),
                )
            else:
                cur.execute(
                    """INSERT INTO reports (
                           name, report_type, owner_id, status, category, description, created_by, updated_by,
                           pbi_report_id, pbi_workspace_id, pbi_dataset_id, pbi_display_name,
                           portal_folder_id, visibility
                       ) VALUES (%s, 'personal', %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (name, owner_id, category, description, owner_id, owner_id,
                     pbi_report_id, config.resolve_workspace_id(pbi_workspace_id), pbi_dataset_id, pbi_display_name,
                     portal_folder_id, visibility),
                )
                report_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
                   VALUES (%s, %s, TRUE, %s)
                   ON CONFLICT (user_id, report_id) DO UPDATE SET can_view=TRUE""",
                (owner_id, report_id, owner_id),
            )
        conn.commit()
    return report_id


# ── Fabric 동기화 보조 ────────────────────────────────────────────────────────

def db_get_synced_reports():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, status, pbi_report_id,
                          COALESCE(pbi_workspace_id, %s) AS pbi_workspace_id
                   FROM reports
                   WHERE status IN ('active', 'deleted')""",
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
