"""RLS 대상 계정의 department/data_scope 값을 docs/01의 테스트 표와 대조한다.

department 축 통합(2026-08-12, company_code/company_scope 제거) 이후 실제 DB 값이
여전히 말이 되는지 — 브라우저로 렌더링을 확인하기 전에 먼저 걸러낼 수 있는
저비용 점검이다. 이 스크립트가 통과해도 PBIX DAX가 맞다는 보장은 아니다(그건
`v_rls_user_scope`를 읽는 Power BI Desktop 쪽 문제라 여기서 볼 수 없다) — 반대로
여기서 이상하면 브라우저 확인 전에 이미 답이 나온 것이다.

읽기 전용, 아무것도 바꾸지 않는다.

사용법: python scripts/check_rls_accounts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.pool import db_conn

VALID_SCOPES = {"self", "department", "all"}

# docs/01_RLS_적용가이드.md "테스트 절차" 표에 나오는 예시 계정 — 있으면 표와 대조,
# 없으면 그냥 건너뛴다(환경마다 실제로 만들어져 있는지는 다를 수 있음).
EXPECTED = {
    "dev1":   ("개발팀", "self"),
    "dev2":   ("개발팀", "self"),
    "dev3":   ("개발팀", "department"),
    "sales1": ("AMT", "self"),
    "sales2": ("ECO", "self"),
    "sales3": ("SECO", "all"),
}


def main() -> None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT username, is_active, is_admin, department, data_scope, pbi_username
                   FROM users ORDER BY id"""
            )
            rows = cur.fetchall()

    print(f"전체 계정 {len(rows)}개\n")

    problems = []
    seen = set()
    for r in rows:
        seen.add(r["username"])
        tag = []
        if not r["is_admin"]:
            if not r["pbi_username"]:
                tag.append("pbi_username 비어있음 — Effective Identity 못 만듦")
            if r["data_scope"] not in VALID_SCOPES:
                tag.append(f"data_scope='{r['data_scope']}' 이 유효값(self/department/all) 아님")
            if r["data_scope"] in ("department",) and not r["department"]:
                tag.append("data_scope=department인데 department가 비어있음 — 전원 매칭 실패")
        exp = EXPECTED.get(r["username"])
        if exp and r["is_active"]:
            exp_dept, exp_scope = exp
            if r["department"] != exp_dept or r["data_scope"] != exp_scope:
                tag.append(
                    f"docs/01 기대값과 다름 — 기대 department={exp_dept}/data_scope={exp_scope}, "
                    f"실제 department={r['department']}/data_scope={r['data_scope']}"
                )
        status = "! " if tag else "  "
        print(f"{status}{r['username']:<12} active={r['is_active']!s:<5} admin={r['is_admin']!s:<5} "
              f"department={r['department']!r:<12} data_scope={r['data_scope']!r:<12} pbi_username={r['pbi_username']!r}")
        for t in tag:
            print(f"    -> {t}")
        if tag:
            problems.append(r["username"])

    missing = [u for u in EXPECTED if u not in seen]
    if missing:
        print(f"\ndocs/01 테스트 표에 있는데 이 DB엔 없는 계정: {', '.join(missing)} "
              "(이 환경에서 원래 안 쓰는 계정일 수도 있음 — 확인 필요)")

    print(f"\n{'문제 없음' if not problems else f'문제 있는 계정 {len(problems)}개: ' + ', '.join(problems)}")


if __name__ == "__main__":
    main()
