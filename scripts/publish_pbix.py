"""
.pbix 파일을 BI_Report 워크스페이스에 게시(업로드)하는 스크립트.

Power BI Desktop의 "게시" 버튼과 같은 일을 리눅스 서버에서 수행한다.
서버가 이미 쓰고 있는 서비스 주체(CLIENT_ID/SECRET)로 인증하므로 별도 로그인 불필요.

사용법:
    python3 scripts/publish_pbix.py <pbix파일> [보고서이름]

    보고서이름을 생략하면 파일명(확장자 제외)을 사용.
    같은 이름의 보고서가 워크스페이스에 이미 있으면 중단한다.

예시:
    python3 scripts/publish_pbix.py /tmp/신규보고서.pbix        # 신규 게시
    python3 scripts/publish_pbix.py /tmp/신규.pbix 월간실적     # "월간실적" 이름으로 게시
"""
import sys
import time
import os
from pathlib import Path

import httpx
import msal
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

TENANT_ID     = os.getenv("TENANT_ID")
CLIENT_ID     = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
WORKSPACE_ID  = os.getenv("WORKSPACE_ID")

API = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"


def get_access_token() -> str:
    """서비스 주체(Client Credentials)로 Azure AD 토큰 발급."""
    msal_app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = msal_app.acquire_token_for_client(
        scopes=["https://analysis.windows.net/powerbi/api/.default"]
    )
    if "access_token" not in result:
        sys.exit(f"토큰 발급 실패: {result.get('error_description')}")
    return result["access_token"]


def publish(pbix_path: Path, report_name: str):
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 1) .pbix 신규 업로드 (기존 항목은 수정하지 않음)
    print(f"업로드 중: {pbix_path.name} → 보고서 이름 '{report_name}'")
    url = f"{API}/imports"
    with open(pbix_path, "rb") as f:
        resp = httpx.post(
            url,
            params={"datasetDisplayName": f"{report_name}.pbix", "nameConflict": "Abort"},
            headers=headers,
            files={"file": (f"{report_name}.pbix", f, "application/octet-stream")},
            timeout=300,
        )
    if resp.status_code not in (200, 202):
        sys.exit(f"업로드 실패 ({resp.status_code}): {resp.text}")
    import_id = resp.json()["id"]

    # 2) 처리 완료까지 상태 확인 (Fabric이 .pbix를 데이터셋+보고서로 변환하는 시간)
    print("Fabric 처리 대기 중", end="", flush=True)
    while True:
        time.sleep(3)
        print(".", end="", flush=True)
        resp = httpx.get(f"{API}/imports/{import_id}", headers=headers, timeout=30)
        state = resp.json().get("importState")
        if state == "Succeeded":
            print(" 완료")
            break
        if state == "Failed":
            sys.exit(f"\n게시 실패: {resp.json().get('error')}")

    report = resp.json()["reports"][0]
    report_id = report["id"]
    print(f"\n게시 성공!")
    print(f"  보고서 이름: {report_name}")
    print(f"  Report ID:   {report_id}")

    # 3) 게이트웨이 등록 안내 (Report ID는 DB의 report_meta에서 관리)
    print(f"\n게이트웨이 신규 등록 SQL:")
    print(f"  INSERT INTO reports (name, report_type, owner_id)")
    print(f"    VALUES ('{report_name}', 'managed', NULL);")
    print(f"  INSERT INTO report_meta (report_id, pbi_report_id, pbi_workspace_id)")
    print(f"    VALUES ((SELECT id FROM reports WHERE name = '{report_name}'), '{report_id}', '{WORKSPACE_ID}');")
    print(f"  INSERT INTO user_reports (user_id, report_id) VALUES (")
    print(f"    (SELECT id FROM users WHERE username = '대상사용자'),")
    print(f"    (SELECT id FROM reports WHERE name = '{report_name}'));")
    print(f"\n서버 재시작 불필요 — DB 등록 즉시 반영됩니다.")
    print(f"참고: 웹 화면의 '내 보고서 올리기' 버튼을 쓰면 이 과정이 전부 자동입니다.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    pbix = Path(sys.argv[1])
    if not pbix.exists() or pbix.suffix.lower() != ".pbix":
        sys.exit(f"파일을 찾을 수 없거나 .pbix가 아닙니다: {pbix}")
    name = sys.argv[2] if len(sys.argv) > 2 else pbix.stem
    publish(pbix, name)
