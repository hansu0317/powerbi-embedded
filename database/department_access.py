"""부서 단위 보고서 접근(1층 열람권한) — GET 필터(2층·화면 표시 필터, users.department를
그대로 씀)와는 다른 층이지만 같은 department 컬럼을 축으로 쓴다.

옛 groups/user_groups/group_reports(팀을 따로 만들고 소속을 관리하던 방식, 2026-08-26
폐기 — 실사용 0건이었다)를 대체한다. 부서는 별도로 만들거나 삭제하지 않는다 —
users.department에 이미 쓰이고 있는 값이 곧 부서 목록이다. 개인별 예외(부서 소속이어도
특정 인원만 차단, 비소속이어도 특정 인원만 허용)는 user_reports가 그대로 담당하며
database/reports.py::_CAN_VIEW_REPORT_SQL에서 항상 최우선으로 적용된다."""
import psycopg2.errors

from database.pool import db_conn
from errors import AppError

# ── 부서 단위 보고서 접근 ────────────────────────────────────────────────────

def db_list_departments() -> list[str]:
    """현재 사용자들에게 실제로 쓰이고 있는 department 값 목록 (부여 UI 선택지용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT department FROM users "
                "WHERE department IS NOT NULL AND department <> '' ORDER BY department"
            )
            return [row["department"] for row in cur.fetchall()]


def db_get_report_department_access(report_id: int) -> list:
    """이 보고서에 부여된 부서 목록 + 각 부서 현재 인원 수 (권한 모달 '부서' 탭용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT dra.department,
                          (SELECT COUNT(*) FROM users u
                           WHERE u.department = dra.department AND u.is_active) AS member_count
                   FROM department_report_access dra
                   WHERE dra.report_id = %s AND dra.can_view
                   ORDER BY dra.department""",
                (report_id,),
            )
            return cur.fetchall()


def db_set_report_department_access(report_id: int, department: str, can_view: bool, granted_by: int) -> None:
    """부서 단위 열람 권한 부여/해제. 없는 보고서면 404 (FK 위반 → 500 방지)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
          try:
            if can_view:
                cur.execute(
                    """INSERT INTO department_report_access (department, report_id, can_view, granted_by)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (department, report_id) DO UPDATE
                       SET can_view = TRUE, granted_by = EXCLUDED.granted_by""",
                    (department, report_id, granted_by),
                )
            else:
                cur.execute(
                    "DELETE FROM department_report_access WHERE department = %s AND report_id = %s",
                    (department, report_id),
                )
          except psycopg2.errors.ForeignKeyViolation as exc:
            conn.rollback()
            raise AppError.REPORT_NOT_FOUND.http() from exc
        conn.commit()
