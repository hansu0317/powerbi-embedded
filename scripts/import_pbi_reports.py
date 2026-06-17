"""Power BI 워크스페이스에 이미 있는 보고서를 게이트웨이 DB에 등록하는 스크립트.

최초 구축 또는 DB 초기화 후 복구 시나리오:
  워크스페이스에 이미 올라가 있는 보고서를 관리 보고서(managed)로 DB에 등록해
  게이트웨이 웹에서 열람할 수 있게 한다.

사용법:
    python3 scripts/import_pbi_reports.py [옵션]

    --dry-run        : DB에 쓰지 않고 목록만 출력
    --grant USERNAME : 등록한 보고서에 열람 권한 부여할 사용자명 (여러 번 사용 가능)
    --filter KEYWORD : 보고서 이름에 이 키워드가 포함된 것만 등록

예시:
    # 워크스페이스 전체 등록 (먼저 뭐가 있는지 확인)
    python3 scripts/import_pbi_reports.py --dry-run

    # 전체 등록 후 user01, user02에게 열람 권한 부여
    python3 scripts/import_pbi_reports.py --grant user01 --grant user02

    # "매출" 키워드 포함 보고서만 등록
    python3 scripts/import_pbi_reports.py --filter 매출
"""

import argparse
import os
import sys
from pathlib import Path

import httpx
import msal
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

TENANT_ID     = os.getenv("TENANT_ID")
CLIENT_ID     = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
WORKSPACE_ID  = os.getenv("WORKSPACE_ID")

DB_CONFIG = {
    "host":            os.getenv("DB_HOST", "127.0.0.1"),
    "port":            int(os.getenv("DB_PORT", "5432")),
    "dbname":          os.getenv("DB_NAME", "powerbi_gateway"),
    "user":            os.getenv("DB_USER"),
    "password":        os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
    "cursor_factory":  psycopg2.extras.RealDictCursor,
}

PBI_API = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"


def get_pbi_token() -> str:
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(
        scopes=["https://analysis.windows.net/powerbi/api/.default"]
    )
    if "access_token" not in result:
        sys.exit(f"토큰 발급 실패: {result.get('error_description')}")
    return result["access_token"]


def list_workspace_reports(token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.get(f"{PBI_API}/reports", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("value", [])


def already_registered(cur, pbi_report_id: str) -> bool:
    cur.execute("SELECT 1 FROM report_meta WHERE pbi_report_id = %s", (pbi_report_id,))
    return cur.fetchone() is not None


def register_report(cur, name: str, pbi_report_id: str, pbi_dataset_id: str | None,
                    pbi_workspace_id: str, actor_id: int) -> int:
    cur.execute(
        """INSERT INTO reports (name, report_type, owner_id, status, created_by, updated_by)
           VALUES (%s, 'managed', NULL, 'active', %s, %s)
           RETURNING id""",
        (name, actor_id, actor_id),
    )
    report_id = cur.fetchone()["id"]
    cur.execute(
        """INSERT INTO report_meta
               (report_id, pbi_report_id, pbi_workspace_id, pbi_dataset_id, pbi_display_name)
           VALUES (%s, %s, %s, %s, %s)""",
        (report_id, pbi_report_id, pbi_workspace_id, pbi_dataset_id, name),
    )
    cur.execute("INSERT INTO report_settings (report_id) VALUES (%s) ON CONFLICT DO NOTHING", (report_id,))
    cur.execute("INSERT INTO report_rls (report_id) VALUES (%s) ON CONFLICT DO NOTHING", (report_id,))
    cur.execute(
        """INSERT INTO report_audit_log (report_id, actor_user_id, action, details)
           VALUES (%s, %s, 'managed_report_imported',
                   jsonb_build_object('pbi_report_id', %s, 'name', %s))""",
        (report_id, actor_id, pbi_report_id, name),
    )
    return report_id


def grant_access(cur, report_id: int, user_ids: list[int], actor_id: int):
    for uid in user_ids:
        cur.execute(
            """INSERT INTO user_reports (user_id, report_id, can_view, can_edit, can_manage, granted_by)
               VALUES (%s, %s, TRUE, FALSE, FALSE, %s)
               ON CONFLICT (user_id, report_id) DO UPDATE SET can_view = TRUE""",
            (uid, report_id, actor_id),
        )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="목록만 출력, DB 변경 없음")
    parser.add_argument("--grant", metavar="USERNAME", action="append", default=[],
                        help="열람 권한 부여할 사용자명 (여러 번 사용 가능)")
    parser.add_argument("--filter", metavar="KEYWORD", dest="keyword",
                        help="보고서 이름에 이 키워드가 포함된 것만 등록")
    args = parser.parse_args()

    token = get_pbi_token()
    all_reports = list_workspace_reports(token)

    if args.keyword:
        all_reports = [r for r in all_reports if args.keyword.lower() in r["name"].lower()]

    if not all_reports:
        print("등록할 보고서가 없습니다.")
        return

    print(f"\nPower BI 워크스페이스 보고서 {len(all_reports)}개:")
    for r in all_reports:
        print(f"  - {r['name']:<40}  id={r['id']}")

    if args.dry_run:
        print("\n--dry-run 모드: DB에 쓰지 않습니다.")
        return

    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE is_admin = TRUE LIMIT 1")
            admin = cur.fetchone()
            if not admin:
                sys.exit("관리자 계정이 없습니다. 먼저 users 테이블에 is_admin=TRUE 계정을 추가하세요.")
            actor_id = admin["id"]

            # 관리자 전체 ID (기본 접근 부여 대상)
            cur.execute("SELECT id FROM users WHERE is_admin = TRUE")
            admin_ids = [row["id"] for row in cur.fetchall()]

            # 추가 권한 부여 대상 사용자 ID
            grant_ids: list[int] = list(admin_ids)
            for uname in args.grant:
                cur.execute("SELECT id FROM users WHERE username = %s", (uname,))
                row = cur.fetchone()
                if not row:
                    print(f"  경고: 사용자 '{uname}'를 찾을 수 없음 — 권한 부여 건너뜀")
                elif row["id"] not in grant_ids:
                    grant_ids.append(row["id"])

            registered = skipped = 0
            for r in all_reports:
                if already_registered(cur, r["id"]):
                    print(f"  건너뜀(이미 등록됨): {r['name']}")
                    skipped += 1
                    continue
                report_id = register_report(
                    cur,
                    name=r["name"],
                    pbi_report_id=r["id"],
                    pbi_dataset_id=r.get("datasetId"),
                    pbi_workspace_id=WORKSPACE_ID,
                    actor_id=actor_id,
                )
                grant_access(cur, report_id, grant_ids, actor_id)
                print(f"  등록 완료: {r['name']:<40}  db_id={report_id}")
                registered += 1

        conn.commit()

    print(f"\n완료 — 등록: {registered}개 / 건너뜀: {skipped}개")
    print("서버 재시작 없이 즉시 반영됩니다.")
    if args.grant:
        print(f"열람 권한 부여: {', '.join(args.grant)}")


if __name__ == "__main__":
    main()
