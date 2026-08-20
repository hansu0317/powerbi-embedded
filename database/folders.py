"""포털 보고서 폴더 — 소유권과 화면 위치를 분리한다."""
from database.pool import db_conn
from database.reports import _CAN_VIEW_REPORT_SQL

# 폴더를 "볼 수 있는가"와 "그 안에 보고서를 넣을 수 있는가"에 같은 규칙을 쓴다:
# 관리자이거나 / 본인 소유이거나 / shared이거나 / 같은 그룹(owner 기준)이면 된다.
# f는 대상 report_folders 행의 별칭 — 이 상수를 쓰는 쿼리는 항상 FROM report_folders f를 가져야 한다.
# 자리표시자 순서: is_admin, user_id(owner_id 비교), user_id(그룹 비교) — 총 3개, 호출부마다 이 순서로 넘긴다.
#
# 주의 — 이건 "업로드 대상으로 고를 수 있는가"에만 쓴다(db_get_writable_folders). 폴더
# 기본값이 visibility='shared'라서, 이 규칙을 탐색 트리에도 그대로 쓰면 보고서 열람
# 권한이 하나도 없는 사용자한테도 폴더 이름(고객사명 등)이 전부 노출된다(2026-08-20
# adcrm1 테스트 계정으로 발견). 탐색 트리는 아래 _CAN_VIEW_FOLDER_SQL을 쓴다.
_CAN_WRITE_FOLDER_SQL = """(
    %s OR f.owner_id=%s OR f.visibility='shared'
    OR (f.visibility='group' AND EXISTS (
        SELECT 1 FROM user_groups me JOIN user_groups owner_group USING(group_id)
        WHERE me.user_id=%s AND owner_group.user_id=f.owner_id))
)"""

# 탐색 트리(사이드바)용 — "그 안에 실제로 볼 수 있는 보고서가 있는가"로 판정한다.
# 자식만 보이고 부모 폴더가 이 규칙에 안 걸리면(예: "공장" 자체엔 보고서가 없고
# 안의 "생산"에만 있는 경우), frontend의 orderFoldersAsTree가 그 자식을 최상위로
# 끌어올려 화면에서 사라지지 않게 처리한다(부모가 목록에 없으면 자동으로 그렇게 됨) —
# 그래서 여기서 조상까지 따로 챙길 필요가 없다.
# 자리표시자 순서: is_admin, user_id(owner_id 비교), user_id(_CAN_VIEW_REPORT_SQL의 u.id) — 총 3개.
_CAN_VIEW_FOLDER_SQL = f"""(
    %s OR f.owner_id=%s OR EXISTS (
        SELECT 1 FROM reports r, users u
        WHERE r.portal_folder_id=f.id AND r.status='active' AND u.id=%s
          AND {_CAN_VIEW_REPORT_SQL}
    )
)"""

_FOLDER_SELECT_COLUMNS = """f.id,f.name,f.parent_id,f.owner_id,f.visibility,f.fabric_folder_id,
                                  ou.username AS owner_username,
                                  (SELECT COUNT(*) FROM reports r WHERE r.portal_folder_id=f.id AND r.status='active') AS report_count"""


def db_get_report_folders(user_id: int, is_admin: bool) -> list:
    """탐색 트리(사이드바)용 — 안에 실제로 볼 수 있는 보고서가 있어야 폴더가 보인다.
    업로드 대상 선택기는 db_get_writable_folders를 쓴다(빈 공용 폴더도 대상으로 보여야
    해서 규칙이 다르다)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT {_FOLDER_SELECT_COLUMNS}
                           FROM report_folders f LEFT JOIN users ou ON ou.id=f.owner_id
                           WHERE {_CAN_VIEW_FOLDER_SQL}
                           ORDER BY f.parent_id NULLS FIRST,f.name""", (is_admin,user_id,user_id))
            return cur.fetchall()


def db_get_writable_folders(user_id: int, is_admin: bool) -> list:
    """업로드 대상 폴더 선택기용 — "쓸 수 있으면 보임"(옛 db_get_report_folders와 동일 규칙).
    아직 보고서가 하나도 없는 빈 공용 폴더도 업로드 대상으로는 보여야 해서
    db_get_report_folders(열람 기준)와 의도적으로 다르다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT {_FOLDER_SELECT_COLUMNS}
                           FROM report_folders f LEFT JOIN users ou ON ou.id=f.owner_id
                           WHERE {_CAN_WRITE_FOLDER_SQL}
                           ORDER BY f.parent_id NULLS FIRST,f.name""", (is_admin,user_id,user_id))
            return cur.fetchall()


def db_can_write_folder(folder_id: int, user_id: int, is_admin: bool) -> bool:
    """이 폴더에 새 보고서를 등록/이동해도 되는지 — 업로드(routes/report.py)에서 쓴다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM report_folders f WHERE f.id=%s AND {_CAN_WRITE_FOLDER_SQL}",
                        (folder_id, is_admin, user_id, user_id))
            return cur.fetchone() is not None

def db_get_folder(folder_id: int) -> dict | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,parent_id,owner_id,visibility,fabric_folder_id FROM report_folders WHERE id=%s",(folder_id,))
            return cur.fetchone()

def db_ensure_folder_path(path: str, fabric_folder_id: str | None, actor_id: int) -> int:
    """category 경로("본부/팀")를 report_folders 트리로 만들고 리프 폴더의 id를 반환한다.

    database/admin.py::db_import_pbi_item(가져오기)이 매 항목마다 호출한다 — Fabric의
    폴더 구조를 그대로 미러링하는 용도라 routes/folders.py의 "임의 폴더 생성은 이 앱이
    다루지 않는다"는 방침과 배치되지 않는다(사용자가 직접 만드는 게 아니라 Fabric을 그대로
    따라감). owner_id=NULL(회사 공용)로 만들고, 이미 같은 이름·부모의 폴더가 있으면
    (report_folders_scope_name_uidx 기준) 재사용하며 fabric_folder_id만 최신화한다.
    visibility는 기본 'personal'(관리자만 봄)로 시작 — 직원 공개는 관리자가 폴더 단위로
    수동 전환한다(공개 즉시 그 폴더의 보고서 전체가 노출되므로 자동으로 열지 않는다)."""
    parts = [p for p in path.split("/") if p]
    parent_id = None
    leaf_id = None
    with db_conn() as conn:
        with conn.cursor() as cur:
            for i, name in enumerate(parts):
                # 리프 세그먼트에만 이 항목의 fabric_folder_id를 붙인다 — 중간 조상 폴더의
                # 실제 Fabric GUID는 이 호출만으로는 알 수 없어(그 폴더에 직접 항목이 없으면
                # 영영 NULL로 남을 수 있음) None으로 둔다.
                fid = fabric_folder_id if i == len(parts) - 1 else None
                cur.execute(
                    """INSERT INTO report_folders (name, parent_id, owner_id, visibility, fabric_folder_id, created_by)
                       VALUES (%s, %s, NULL, 'personal', %s, %s)
                       ON CONFLICT (COALESCE(parent_id,0), COALESCE(owner_id,0), LOWER(name))
                       DO UPDATE SET fabric_folder_id = COALESCE(EXCLUDED.fabric_folder_id, report_folders.fabric_folder_id)
                       RETURNING id""",
                    (name, parent_id, fid, actor_id),
                )
                leaf_id = cur.fetchone()["id"]
                parent_id = leaf_id
        conn.commit()
    return leaf_id


def db_move_report_to_folder(report_id: int, folder_id: int, actor_id: int, is_admin: bool) -> dict | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id,name,visibility FROM report_folders f WHERE id=%s AND {_CAN_WRITE_FOLDER_SQL}",
                        (folder_id,is_admin,actor_id,actor_id))
            folder=cur.fetchone()
            if not folder: return None
            cur.execute("""UPDATE reports SET portal_folder_id=%s,category=%s,visibility=%s,updated_by=%s,updated_at=NOW()
                           WHERE id=%s AND (%s OR owner_id=%s) RETURNING id,pbi_report_id,pbi_workspace_id""",
                        (folder_id,folder['name'],folder['visibility'],actor_id,report_id,is_admin,actor_id))
            row=cur.fetchone()
        conn.commit()
    return row
