"""회사 계층 RLS(company_code/company_scope/company_codes) 제거 —
department/data_scope 단일 축으로 되돌린다.

배경: scripts/add_company_hierarchy.py(2026-08 초)가 SECO 자회사 격리 요구 때문에
company_code/company_scope/company_codes를 추가했었다. 이후 검토에서 "부서 축과 회사
축이 같은 모양(그룹 값 + 범위)인데 두 벌로 구현돼 있어 오히려 이해하기 어렵다"는 판단으로,
자회사(AMT/ECO/SECO)를 department 값으로 흡수하고 data_scope(self/department/all) 하나만
쓰기로 결정했다(2026-08-12). 테이블·컬럼을 최대한 줄이는 프로토타입 방향.

이 결정으로 잃는 것: 3단계 이상 계층이나 "그룹은 산하만 보고 전체는 안 보는" 것처럼
`all`(전사)보다 좁은 "산하 전체" 표현은 못 한다 — 지금은 필요 없다고 판단해 받아들인다.
나중에 다시 필요해지면 add_company_hierarchy.py를 참고해 같은 패턴으로 복원 가능하다
(git 이력에 남아있음).

실행 전 반드시 백업:
    pg_dump -h 127.0.0.1 -U <계정> -d powerbi_gateway -F c -f backups/before_company_removal.dump

실행 방법 (재실행해도 안전 — 전부 IF EXISTS):
    python scripts/remove_company_hierarchy.py
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

# 기존 테스트 계정 마이그레이션 — company_code를 department로 흡수한다.
# (username, 새 department, 새 data_scope) — company_code가 없던 사용자는 그대로 둔다.
TEST_USER_REMAP = [
    ("sales1", "AMT",  "self"),
    ("sales2", "ECO",  "self"),
    ("sales3", "SECO", "all"),
]


def run():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # 1) company_code를 쓰던 테스트 계정은 그 값을 department로 옮겨서 데이터가
            #    사라지지 않게 한다 (컬럼을 지우기 전에 먼저 실행해야 함).
            for username, department, data_scope in TEST_USER_REMAP:
                cur.execute(
                    """UPDATE users SET department = %s, data_scope = %s, updated_at = NOW()
                       WHERE username = %s AND company_code IS NOT NULL""",
                    (department, data_scope, username),
                )

            # 2) 뷰를 먼저 지운다 — CREATE OR REPLACE VIEW는 컬럼을 못 지우므로
            #    DROP 후 다시 만들어야 한다(PostgreSQL 제약).
            cur.execute("DROP VIEW IF EXISTS v_rls_user_scope")
            cur.execute("""
                CREATE VIEW v_rls_user_scope AS
                    SELECT u.pbi_username AS user_key, u.department, u.data_scope
                    FROM users u
                    WHERE u.is_active = TRUE
            """)

            # 3) users에서 회사 컬럼 제거 (FK 제약도 컬럼과 함께 자동으로 없어진다).
            cur.execute("ALTER TABLE users DROP COLUMN IF EXISTS company_code")
            cur.execute("ALTER TABLE users DROP COLUMN IF EXISTS company_scope")

            # 4) 카탈로그 테이블 자체를 제거.
            cur.execute("DROP TABLE IF EXISTS company_codes")
        conn.commit()
    print("company 계층 RLS 제거 완료 — department/data_scope 단일 축으로 통일됨")


if __name__ == "__main__":
    run()
