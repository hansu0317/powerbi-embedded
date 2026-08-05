"""관계사 코드(AMT/ECO 등) GET 필터 기능 — 스키마 확장 스크립트 (PoC).

init_schema.py는 계속 고쳐 쓰지 않기로 했으므로(그 파일 docstring 참고),
이후 스키마 변경은 이렇게 별도 스크립트로 처리한다.

추가하는 것
-----------
1. reports.filter_table / reports.filter_column
   보고서별로 "GET 필터를 적용할 테이블/컬럼"을 지정한다. 둘 다 NULL이면 필터를
   적용하지 않는다(기존 보고서는 전부 동작 그대로).
2. user_company_codes(user_id, company_code, is_default)
   사용자 1명이 여러 관계사 코드를 볼 수 있는 다대다 매핑. is_default=TRUE인
   행이 그 사용자가 보고서를 열 때 기본으로 선택되는 코드.

이번엔 SECO_사례 카테고리 7개 보고서에 filter_table/filter_column 기본값만
채운다 — 실제 최종사용자 계정에 관계사 코드를 배정하는 건 이 스크립트가
임의로 정할 수 없는 값이라(실제 조직 구조를 알아야 함) 하지 않는다.
로컬 검증용으로 테스트 계정(admin, dev1, sales1)에만 예시로 넣는다.

주의 — 진짜 보안 아님: 이 필터는 보고서 필터 창에서 사용자가 직접 지울 수
있다(JS SDK filters의 isLockedInViewMode로 제거 버튼만 숨길 뿐). 접근 자체를
막아야 하면 users.roles 기반 RLS(services/powerbi.py 기존 로직)를 쓸 것.

실행:
    python scripts/add_company_code_filter.py
"""
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "powerbi_gateway"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
}

DDL = [
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_table  VARCHAR(120)",
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_column VARCHAR(120)",
    """CREATE TABLE IF NOT EXISTS user_company_codes (
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        company_code VARCHAR(30) NOT NULL,
        is_default   BOOLEAN NOT NULL DEFAULT FALSE,
        added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, company_code)
    )""",
    # 사용자당 기본 코드는 하나만 — 부분 유니크 인덱스로 강제
    """CREATE UNIQUE INDEX IF NOT EXISTS user_company_codes_default_uidx
       ON user_company_codes (user_id) WHERE is_default = TRUE""",
]

# SECO_사례 카테고리 7개 보고서 — 전부 같은 관계사 차원(DimPartner/CompanyCode)을 쓴다는 전제.
# 실제로 없는 보고서는 Power BI가 필터를 조용히 무시할 뿐 에러는 안 나므로 안전하게 일괄 적용.
SECO_FILTER_TABLE  = "DimPartner"
SECO_FILTER_COLUMN = "CompanyCode"

# 로컬 검증용 테스트 계정 — 실제 조직의 관계사 배정이 아니라 동작 확인용 예시 데이터.
TEST_COMPANY_CODES = [
    # (username, company_code, is_default)
    ("admin",  "AMT", True),
    ("admin",  "ECO", False),   # 관리자는 두 관계사 다 볼 수 있다고 가정 → 드롭다운 노출 확인용
    ("dev1",   "AMT", True),
    ("sales1", "ECO", True),
]


def main():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)

            cur.execute(
                "UPDATE reports SET filter_table = %s, filter_column = %s WHERE category = 'SECO_사례'",
                (SECO_FILTER_TABLE, SECO_FILTER_COLUMN),
            )
            print(f"reports.filter_table/column 적용: {cur.rowcount}건 (category='SECO_사례')")

            for username, code, is_default in TEST_COMPANY_CODES:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                row = cur.fetchone()
                if not row:
                    print(f"  스킵: 사용자 '{username}' 없음")
                    continue
                cur.execute(
                    """INSERT INTO user_company_codes (user_id, company_code, is_default)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (user_id, company_code) DO UPDATE SET is_default = EXCLUDED.is_default""",
                    (row[0], code, is_default),
                )
            print(f"user_company_codes 테스트 데이터: {len(TEST_COMPANY_CODES)}건 upsert")

        conn.commit()
    print("done")


if __name__ == "__main__":
    main()
