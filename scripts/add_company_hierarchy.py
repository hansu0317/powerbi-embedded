"""회사 계층(모회사-자회사) RLS 확장 — SECO/아이아/서진/에코 사례.

배경: GET 필터(users.filter_key/filter_value)는 사용자가 Power BI 필터 창에서
손으로 지울 수 있어 진짜 보안이 아니다(2026-08 확인). "아이아가 서진 데이터를
절대 못 봐야 한다" 같은 진짜 차단 요구는 RLS(DAX 레벨)로만 가능해서, 기존
department/data_scope와 똑같은 패턴으로 회사 계층을 추가한다.

구조:
  company_codes(code, parent_code, name) — 회사 계층. parent_code가 NULL이면 최상위(모회사).
  users.company_code  — 이 사람이 소속된 회사 코드 (예: 'AIA'). 없으면 NULL(회사 무관 사용자).
  users.company_scope — 'own'(자기 회사만) / 'group'(같은 parent_code 산하 전체, 모회사 담당자용)

v_rls_user_scope 뷰에 company_code/company_scope/company_parent를 추가해
department/data_scope와 같은 방식으로 Power BI DAX가 LOOKUPVALUE로 읽어가게 한다.

실행 방법 (재실행해도 안전 — 전부 IF NOT EXISTS / CREATE OR REPLACE):
    python scripts/add_company_hierarchy.py

주의: company_codes 실제 값(코드·계층)은 고객사(SECO)와 협의 후 채울 것 —
      48d568e 커밋의 GET 필터 때와 같은 경고. 이 스크립트는 뼈대만 만든다.
"""
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "powerbi_gateway"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
}


def run():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS company_codes (
                    code        VARCHAR(30) PRIMARY KEY,
                    parent_code VARCHAR(30) REFERENCES company_codes(code) ON DELETE SET NULL,
                    name        VARCHAR(100) NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS company_codes_parent_idx
                    ON company_codes (parent_code) WHERE parent_code IS NOT NULL
            """)

            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS company_code VARCHAR(30)
                    REFERENCES company_codes(code) ON DELETE SET NULL
            """)
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS company_scope VARCHAR(16)
                    NOT NULL DEFAULT 'own' CHECK (company_scope IN ('own', 'group'))
            """)

            # department/data_scope와 동일한 패턴 — RLS가 읽는 최소 컬럼만 노출하는 뷰.
            # company_parent: 이 사람 회사의 상위 코드(모회사 담당자 판정용으로 DAX가 바로 씀).
            cur.execute("""
                CREATE OR REPLACE VIEW v_rls_user_scope AS
                    SELECT u.pbi_username AS user_key, u.department, u.data_scope,
                           u.company_code, u.company_scope,
                           cc.parent_code AS company_parent
                    FROM users u
                    LEFT JOIN company_codes cc ON cc.code = u.company_code
                    WHERE u.is_active = TRUE
            """)
        conn.commit()
    print("company hierarchy schema ready (company_codes 테이블 + users.company_code/company_scope + v_rls_user_scope 확장)")


if __name__ == "__main__":
    run()
