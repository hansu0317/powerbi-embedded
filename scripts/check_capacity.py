"""워크스페이스에 실제로 어떤 Power BI 용량(capacity)이 붙어 있는지 라이브로 재확인한다.

DB·FastAPI 서버 없이 서비스 주체 인증만으로 동작한다(.env의 TENANT_ID/CLIENT_ID/
CLIENT_SECRET/WORKSPACE_ID만 사용). "체험판 배너가 뜨는가"는 이 스크립트로 100%
확정할 수 없다(그건 Power BI가 렌더링하는 화면 요소라 브라우저로 봐야 함) — 대신
"용량이 붙어 있는가/어떤 SKU인가"라는, 배너 여부를 좌우하는 근본 원인을 확인한다.

docs/05_라이선스_용량_비용가이드.md의 "2026-08-14 재확인(라이브 테스트)" 절과
같은 방법이다. 재실행해서 그 문서의 값이 아직 유효한지 확인할 때 쓴다.

사용법: python scripts/check_capacity.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

import config
from services.azure import get_access_token

CAPACITIES_URL = "https://api.powerbi.com/v1.0/myorg/capacities"


def main() -> None:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=30) as client:
        print(f"WORKSPACE_ID = {config.WORKSPACE_ID}")

        # 1) 이 워크스페이스에 실제로 어떤 capacityId/isOnDedicatedCapacity가 붙어 있는가
        ws_resp = client.get(
            "https://api.powerbi.com/v1.0/myorg/groups",
            headers=headers,
            params={"$filter": f"id eq '{config.WORKSPACE_ID}'"},
        )
        print(f"\n[워크스페이스 조회] status={ws_resp.status_code}")
        if ws_resp.status_code == 200:
            rows = ws_resp.json().get("value", [])
            if not rows:
                print("  결과 없음 — 서비스 주체가 이 워크스페이스 멤버가 아닐 수 있음")
            for ws in rows:
                print(f"  name={ws.get('name')}")
                print(f"  isOnDedicatedCapacity={ws.get('isOnDedicatedCapacity')}")
                print(f"  capacityId={ws.get('capacityId')}")
        else:
            print(f"  {ws_resp.text[:500]}")

        # 2) 이 서비스 주체가 볼 수 있는 capacity 목록과 SKU (docs/05 재현 방법)
        cap_resp = client.get(CAPACITIES_URL, headers=headers)
        print(f"\n[capacities 목록] status={cap_resp.status_code}")
        if cap_resp.status_code == 200:
            caps = cap_resp.json().get("value", [])
            if not caps:
                print("  결과 없음")
            for cap in caps:
                print(f"  id={cap.get('id')}")
                print(f"  displayName={cap.get('displayName')} sku={cap.get('sku')} region={cap.get('region')}")
        else:
            print(f"  {cap_resp.text[:500]}")

    print(
        "\n판정: 위 워크스페이스의 isOnDedicatedCapacity가 True이고 capacityId가 "
        "capacities 목록의 id와 일치하면 '용량 없음(Pro 공유)' 상태가 아니다 — "
        "README의 체험판 배너 문단이 가리키는 상황과 다르다는 뜻이니 그 문단을 "
        "재검토할 것. False/빈 목록이면 반대로 README 쪽이 맞다."
    )


if __name__ == "__main__":
    main()
