"""
Fabric 워크스페이스에 이미 있는 보고서를 게이트웨이 DB에 등록하는 스크립트.

on-prem 서버 최초 구축 시나리오:
  Fabric에 "SECO" 같은 폴더로 보고서가 이미 올라가 있을 때, 그것들을 managed 타입으로
  DB에 등록해 게이트웨이 웹에서 열람할 수 있게 한다.

사용법:
    python3 scripts/import_fabric_reports.py [--folder SECO] [--dry-run]

    --folder FOLDER  : 특정 폴더 이름만 등록 (생략 시 워크스페이스 루트 전체)
    --dry-run        : DB에 쓰지 않고 목록만 출력
    --grant USER     : 등록한 보고서에 열람 권한 부여할 사용자명 (여러 번 사용 가능)

예시:
    # SECO 폴더 보고서를 전부 등록하고 user01, user02에게 권한 부여
    python3 scripts/import_fabric_reports.py --folder SECO --grant user01 --grant user02

    # 먼저 뭐가 있는지 확인
    python3 scripts/import_fabric_reports.py --folder SECO --dry-run
"""

import argparse
import os
import sys
import time
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

PBI_API   = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"
FABRIC_API = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"


def get_token(scope: str) -> str:
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=[scope])
    if "access_token" not in result:
        sys.exit(f"토큰 발급 실패: {result.get('error_description')}")
    return result["access_token"]


def list_fabric_folders(token: str) -> dict[str, str]:
    """워크스페이스 루트 폴더 목록 반환. {displayName: folderId}"""
    headers = {"Authorization": f"Bearer {token}"}
    folders = {}
    params = {"recursive": "false"}
    while True:
        resp = httpx.get(f"{FABRIC_API}/folders", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("value", []):
            folders[item["displayName"]] = item["id"]
        ct = data.get("continuationToken")
        if not ct:
            break
        params["continuationToken"] = ct
    return folders


def list_reports_in_folder(pbi_token: str, folder_id: str | None) -> list[dict]:
    """워크스페이스의 보고서 목록 조회. folder_id가 있으면 해당 폴더만."""
    headers = {"Authorization": f"Bearer {pbi_token}"}
    resp = httpx.get(f"{PBI_API}/reports", headers=headers, timeout=30)
    resp.raise_for_status()
    reports = resp.json().get("value", [])
    if folder_id:
        reports = [r for r in reports if r.get("folderId") == folder_id]
    return reports


def list_datasets(pbi_token: str) -> dict[str, str]:
    """데이터셋 ID → datasetId 매핑 반환."""
    headers = {"Authorization": f"Bearer {pbi_token}"}
    resp = httpx.get(f"{PBI_API}/datasets", headers=headers, timeout=30)
    resp.raise_for_status()
    return {d["id"]: d["id"] for d in resp.json().get("value", [])}


def already_registered(cur, pbi_report_id: str) -> bool:
    cur.execute("SELECT 1 FROM report_meta WHERE pbi_report_id = %s", (pbi_report_id,))
    return cur.fetchone() is not None


def register_report(
    cur,
    name: str,
    pbi_report_id: str,
    pbi_dataset_id: str | None,
    pbi_workspace_id: str,
    folder_id: str | None,
    actor_id: int,
):
    cur.execute(
        """INSERT INTO reports (name, report_type, owner_id, status, created_by, updated_by)
           VALUES (%s, 'managed', NULL, 'active', %s, %s)
           RETURNING id""",
        (name, actor_id, actor_id),
    )
    report_id = cur.fetchone()["id"]
    cur.execute(
        """INSERT INTO report_meta (report_id, pbi_report_id, pbi_workspace_id,
                                    pbi_dataset_id, folder_id)
           VALUES (%s, %s, %s, %s, %s)""",
        (report_id, pbi_report_id, pbi_workspace_id, pbi_dataset_id, folder_id),
    )
    cur.execute("INSERT INTO report_settings (report_id) VALUES (%s)", (report_id,))
    cur.execute("INSERT INTO report_rls (report_id) VALUES (%s)", (report_id,))
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
            """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
               VALUES (%s, %s, TRUE, %s)
               ON CONFLICT (user_id, report_id) DO UPDATE SET can_view = TRUE""",
            (uid, report_id, actor_id),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", help="등록할 Fabric 폴더 이름 (예: SECO)")
    parser.add_argument("--dry-run", action="store_true", help="목록만 출력, DB 변경 없음")
    parser.add_argument("--grant", metavar="USERNAME", action="append", default=[], help="열람 권한 부여할 사용자명")
    args = parser.parse_args()

    fabric_token = get_token("https://api.fabric.microsoft.com/.default")
    pbi_token    = get_token("https://analysis.windows.net/powerbi/api/.default")

    # 폴더 탐색
    folder_id = None
    if args.folder:
        folders = list_fabric_folders(fabric_token)
        folder_id = folders.get(args.folder)
        if not folder_id:
            available = ", ".join(folders.keys()) or "(없음)"
            sys.exit(f"폴더 '{args.folder}'를 찾을 수 없습니다.\n사용 가능한 폴더: {available}")
        print(f"폴더: {args.folder} (id={folder_id})")

    reports = list_reports_in_folder(pbi_token, folder_id)
    if not reports:
        print("등록할 보고서가 없습니다.")
        return

    print(f"\nFabric에서 발견된 보고서 {len(reports)}개:")
    for r in reports:
        print(f"  - {r['name']:<30}  id={r['id']}")

    if args.dry_run:
        print("\n--dry-run 모드: DB에 쓰지 않습니다.")
        return

    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # 관리자 계정을 actor로 사용
            cur.execute("SELECT id FROM users WHERE is_admin = TRUE LIMIT 1")
            admin = cur.fetchone()
            if not admin:
                sys.exit("관리자 계정이 없습니다. 먼저 users 테이블에 is_admin=TRUE 계정을 추가하세요.")
            actor_id = admin["id"]

            # 권한 부여 대상 사용자 ID 조회
            grant_ids: list[int] = []
            for uname in args.grant:
                cur.execute("SELECT id FROM users WHERE username = %s", (uname,))
                row = cur.fetchone()
                if not row:
                    print(f"  경고: 사용자 '{uname}'를 찾을 수 없음 — 권한 부여 건너뜀")
                else:
                    grant_ids.append(row["id"])

            registered = 0
            skipped = 0
            for r in reports:
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
                    folder_id=folder_id,
                    actor_id=actor_id,
                )
                if grant_ids:
                    grant_access(cur, report_id, grant_ids, actor_id)
                print(f"  등록 완료: {r['name']:<30}  db_id={report_id}")
                registered += 1

        conn.commit()

    print(f"\n완료 — 등록: {registered}개 / 건너뜀: {skipped}개")
    print("서버 재시작 없이 즉시 반영됩니다.")


if __name__ == "__main__":
    main()
