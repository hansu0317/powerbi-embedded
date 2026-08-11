"""관리자 포털 전용 — 사용자/보고서 관리, PBI 가져오기, 런타임 설정(app_config)."""
import psycopg2.errors

from config import WORKSPACE_ID
from database.pool import db_conn
from database.reports import _CAN_VIEW_REPORT_SQL
from errors import AppError

# ── 관리자 ────────────────────────────────────────────────────────────────────

def db_admin_get_stats() -> dict:
    """관리자 현황 화면이 쓰는 집계 전부. 스칼라 서브쿼리로 묶어 DB 왕복 1회로 처리한다.

    부트스트랩(stats)과 자가진단(/api/admin/system-status)이 같은 화면을 채우므로
    한 함수로 합쳤다 — 예전엔 두 함수가 active_reports를 각자 세고 있었다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                    (SELECT COUNT(*) FROM users       WHERE is_active = TRUE)           AS active_users,
                    (SELECT COUNT(*) FROM reports     WHERE status    = 'active')        AS active_reports,
                    (SELECT COUNT(*) FROM upload_jobs WHERE created_at >= CURRENT_DATE)  AS today_uploads,
                    (SELECT COUNT(*) FROM upload_jobs WHERE status = 'completed'
                                                       AND created_at >= CURRENT_DATE)  AS today_success,
                    (SELECT COUNT(*) FROM upload_jobs
                     WHERE status IN ('failed','unknown','db_failed')
                       AND created_at >= NOW() - INTERVAL '7 days')                      AS failed_jobs_7d,
                    (SELECT COUNT(*) FROM event_log WHERE log_type = 'activity')         AS activity_rows"""
            )
            row = cur.fetchone()
    return dict(row)


def db_admin_get_users() -> list:
    """사용자 목록 + 열람 가능 보고서 수 (직접 부여 + 그룹 경유, active만).

    department/data_scope/company_code/company_scope는 권한(위 report_count)과
    완전히 별개인 RLS(2층·행 단위) 속성이다 — docs/01_RLS_적용가이드.md 참고."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT u.id, u.username, u.display_name, u.pbi_username,
                          u.is_admin, u.is_active, u.can_upload, u.last_login_at, u.created_at,
                          u.department, u.data_scope, u.company_code, u.company_scope,
                          (SELECT COUNT(*) FROM reports r
                           WHERE r.status = 'active' AND {_CAN_VIEW_REPORT_SQL}
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
                      pbi_username: str, is_admin: bool,
                      can_upload: bool = True, group_ids: list[int] | None = None,
                      department: str | None = None, data_scope: str = "self",
                      company_code: str | None = None, company_scope: str = "own") -> int:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password, display_name, pbi_username, is_admin, "
                "can_upload, department, data_scope, company_code, company_scope) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (username, pw_hash, display_name, pbi_username, is_admin,
                 can_upload, department, data_scope, company_code, company_scope),
            )
            row = cur.fetchone()
            user_id = row["id"]
            if group_ids:
                cur.executemany(
                    "INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s)",
                    [(user_id, gid) for gid in group_ids],
                )
        conn.commit()
    return user_id


def db_admin_update_user(user_id: int, display_name: str, pbi_username: str,
                         department: str | None, data_scope: str,
                         company_code: str | None, company_scope: str) -> bool:
    """사용자 표시정보·RLS 식별자(pbi_username) 수정.

    RLS 역할 이름은 더 이상 사용자별 컬럼이 아니라 config.PBI_RLS_ROLE_NAME 고정값이라
    여기서 다룰 게 없다. department/data_scope도 여기서 안 건드린다 — 지금 관리
    화면에서 뺀 필드라(동적 RLS 작업 보류 중, 값은 나중에 SQL로 직접 채울 수 있음)
    이 함수가 계속 건드리면 다른 필드 수정할 때마다 매번 기본값으로 조용히 덮어써진다.

    admin 계정은 제외한다 — 실수로 관리자 계정을 건드리는 걸 막는다
    (toggle_active·toggle_upload와 동일한 보호 원칙)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE users SET display_name = %s, pbi_username = %s, department = %s,
                          data_scope = %s, company_code = %s, company_scope = %s, updated_at = NOW()
                   WHERE id = %s AND username != 'admin' RETURNING id""",
                (display_name, pbi_username, department, data_scope, company_code, company_scope, user_id),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None

def db_admin_get_company_codes() -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT code, name, parent_code, created_at FROM company_codes ORDER BY code")
            return cur.fetchall()

def db_admin_upsert_company(code: str, name: str, parent_code: str | None) -> dict:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO company_codes(code,name,parent_code) VALUES(%s,%s,%s)
                         ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,parent_code=EXCLUDED.parent_code
                         RETURNING code,name,parent_code,created_at""", (code,name,parent_code))
            row = cur.fetchone()
        conn.commit()
    return row

def db_admin_delete_company(code: str) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM company_codes WHERE code=%s RETURNING code", (code,))
            ok = cur.fetchone() is not None
        conn.commit()
    return ok


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


def db_admin_toggle_user_upload(user_id: int):
    """업로드 권한 토글. 반환: 변경 후 can_upload (없는 사용자·관리자는 None).

    관리자 계정은 차단 대상에서 제외한다 — toggle_active의 admin 보호와 같은 원칙."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET can_upload = NOT can_upload, updated_at = NOW() "
                "WHERE id = %s AND NOT is_admin RETURNING can_upload",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["can_upload"] if row else None


def db_admin_get_reports() -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.status, r.created_at, r.category, r.description,
                          u.username AS owner_username,
                          r.pbi_report_id, r.pbi_display_name, r.pbi_dataset_id,
                          COALESCE(r.pbi_workspace_id, %s) AS pbi_workspace_id,
                          COUNT(ur.user_id) FILTER (WHERE NOT vu.is_admin) AS viewer_count,
                          (SELECT COUNT(*) FROM group_reports gr
                           WHERE gr.report_id = r.id AND gr.can_view) AS group_count
                   FROM reports r
                   LEFT JOIN users u ON u.id = r.owner_id
                   LEFT JOIN user_reports ur ON ur.report_id = r.id AND ur.can_view = TRUE
                   LEFT JOIN users vu ON vu.id = ur.user_id
                   WHERE r.status <> 'deleted'
                   GROUP BY r.id, u.username
                   ORDER BY r.id""",
                (WORKSPACE_ID,)
            )
            return cur.fetchall()


def db_import_pbi_item(
    pbi_item_id: str,
    name: str,
    pbi_workspace_id: str,
    folder_id: str | None,
    category: str | None,
    actor_id: int,
    pbi_dataset_id: str | None = None,
    is_dashboard: bool = False,
) -> bool:
    """PBI에서 가져온 공용 항목(보고서 또는 대시보드)을 DB에 등록한다.

    이미 등록된 항목(pbi_report_id 기준)은 category만 갱신하고 False를 반환한다.
    신규 등록 성공 시 True. 권한은 부여하지 않는다 — 관리자가 별도로 설정한다.

    보고서와 대시보드는 등록 절차가 같고 세 가지만 다르다:
      report_type, tab_type(뷰어 임베드 분기 신호), 그리고 대시보드는
      pbi_dataset_id가 없다(여러 데이터셋의 타일 모음이라 단일 ID가 없음).

    이름이 '계정__보고서명' 형식이고 그 계정이 존재하면 개인 보고서로 복원한다
    (DB 재구축 후 가져오기에서 개인 보고서가 전부 공용이 되는 것을 막는다).
    """
    tab_type    = "dashboard" if is_dashboard else "report"
    dataset_id  = None if is_dashboard else pbi_dataset_id
    audit_event = "dashboard_imported" if is_dashboard else "managed_report_imported"

    # PBI 표시 이름이 '계정__보고서명' 규칙이면 원래 개인 보고서였다는 뜻이다.
    # DB를 새로 구축하고 가져오기를 하면 이 정보가 없어 전부 공용이 돼버리므로,
    # 접두사의 계정이 실제로 존재할 때만 개인 보고서로 되돌린다.
    owner_id, display_name = None, name
    if not is_dashboard and "__" in name:
        prefix, _, rest = name.partition("__")
        if prefix and rest:
            with db_conn() as probe:
                with probe.cursor() as pcur:
                    pcur.execute("SELECT id FROM users WHERE username = %s", (prefix,))
                    row = pcur.fetchone()
            if row:
                owner_id, display_name = row["id"], rest

    report_type = "dashboard" if is_dashboard else ("personal" if owner_id else "managed")
    if owner_id:
        audit_event = "personal_report_restored"

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM reports WHERE pbi_report_id = %s", (pbi_item_id,))
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
                """INSERT INTO reports (
                       name, report_type, owner_id, status, category, created_by, updated_by,
                       pbi_report_id, pbi_workspace_id, pbi_dataset_id, folder_id, tab_type
                   ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (display_name, report_type, owner_id, category, actor_id, actor_id,
                 pbi_item_id, pbi_workspace_id, dataset_id, folder_id, tab_type),
            )
            report_id = cur.fetchone()["id"]
            if owner_id:
                # 소유자에게 열람 권한을 돌려준다 (업로드 시 부여했던 것과 동일)
                cur.execute(
                    """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (user_id, report_id) DO UPDATE SET can_view = TRUE""",
                    (owner_id, report_id, actor_id),
                )
            cur.execute(
                """INSERT INTO event_log (log_type, report_id, user_id, event, details)
                   VALUES ('audit', %s, %s, %s,
                           jsonb_build_object('pbi_item_id', %s, 'name', %s, 'category', %s))""",
                (report_id, actor_id, audit_event, pbi_item_id, display_name, category),
            )
        conn.commit()
    return True


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
                    "INSERT INTO event_log (log_type, report_id, user_id, event, details) "
                    "VALUES ('audit', %s, %s, 'admin_deleted', '{}'::jsonb)",
                    (report_id, admin_user_id),
                )
        conn.commit()
    return bool(row)


def db_admin_get_upload_jobs(limit: int = 30) -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.report_name, j.status, j.error_message,
                          j.created_at, j.updated_at, u.username,
                          r.category
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   LEFT JOIN reports r ON r.id = j.report_id
                   ORDER BY j.id DESC LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()


def db_get_report_access(report_id: int) -> list:
    """보고서에 대한 모든 활성 사용자의 열람 권한 현황을 반환한다.

    direct: 개별 user_reports 행의 값 — true(직접 허용) / false(명시적 차단) / null(개별 설정 없음).
    via_group: 소속 그룹 중 하나라도 이 보고서에 권한이 있는지.
    can_view: 최종 열람 가능 여부(_CAN_VIEW_REPORT_SQL과 동일 규칙 — 차단이 그룹 권한보다 우선)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.username, u.display_name, u.is_admin,
                          ur.can_view AS direct,
                          COALESCE(vg.via_group, FALSE) AS via_group,
                          CASE
                              WHEN ur.can_view = FALSE THEN FALSE
                              ELSE COALESCE(ur.can_view, FALSE) OR COALESCE(vg.via_group, FALSE)
                          END AS can_view
                   FROM users u
                   LEFT JOIN user_reports ur ON ur.user_id = u.id AND ur.report_id = %s
                   LEFT JOIN LATERAL (
                       SELECT TRUE AS via_group
                       FROM user_groups ug
                       JOIN group_reports gr ON gr.group_id = ug.group_id
                       WHERE ug.user_id = u.id AND gr.report_id = %s AND gr.can_view
                       LIMIT 1
                   ) vg ON TRUE
                   WHERE u.is_active = TRUE
                   ORDER BY u.is_admin DESC, u.username""",
                (report_id, report_id),
            )
            return cur.fetchall()


def db_set_report_access(report_id: int, user_id: int, can_view: bool, granted_by: int) -> None:
    """보고서에 대한 특정 사용자의 열람 권한을 설정한다.

    can_view=False는 단순히 "부여 안 함"이 아니라 명시적 차단이다 — 이 행이 있으면
    소속 그룹으로 부여된 권한이 있어도 이 사용자만 못 보게 우선 적용된다
    (_CAN_VIEW_REPORT_SQL 참고). 그래서 그룹으로만 권한이 있던(개별 행이 아예 없던)
    사용자를 차단할 때도 UPSERT로 새 행을 만들어야 한다 — UPDATE만 하면 기존 행이
    없을 때 아무 효과가 없다.

    예외: 개인 보고서(reports.owner_id)의 소유자 본인 접근은 admin이라도 차단 못 한다.
    막고 싶으면 이 권한 하나만 끄는 게 아니라 보고서 자체를 삭제/아카이브해야 한다 —
    "보고서는 active로 남아있는데 만든 사람 본인만 못 보는" 상태가 더 헷갈리기 때문.

    없는 보고서·사용자 ID면 FK 위반이 나는데, 이는 서버 장애가 아니라 잘못된 요청이므로
    404로 변환한다 (그대로 두면 500).
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
          if not can_view:
              cur.execute("SELECT owner_id FROM reports WHERE id = %s", (report_id,))
              row = cur.fetchone()
              if row and row["owner_id"] == user_id:
                  raise AppError.OWNER_ACCESS_PROTECTED.http()
          try:
            cur.execute(
                """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id, report_id) DO UPDATE
                   SET can_view = EXCLUDED.can_view, granted_by = EXCLUDED.granted_by""",
                (user_id, report_id, can_view, granted_by),
            )
          except psycopg2.errors.ForeignKeyViolation as exc:
            conn.rollback()
            raise AppError.REPORT_NOT_FOUND.http() from exc
        conn.commit()


def db_get_app_config() -> list:
    """app_config 전체 행 (관리자 포털 설정 화면용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value, description, updated_at FROM app_config ORDER BY key")
            return cur.fetchall()


def db_update_app_config(key: str, value: str) -> bool:
    """존재하는 app_config 키의 값을 갱신한다. 없는 키는 거부(False).

    키 생성은 init_schema.py의 APP_CONFIG_DEFAULTS에서만 한다 — 오타 키가 조용히 쌓이는 것을 방지."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_config SET value = %s, updated_at = NOW() WHERE key = %s",
                (value, key),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated
