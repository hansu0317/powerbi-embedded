"""회사/도메인 축 RLS 확장 — department/data_scope와 완전히 독립적인 두 번째 축.

배경: 보고서 하나 안에 여러 회사(예: SECO 고객사라면 AMT/ECO) 데이터가 같이 들어있고,
"이 회사 데이터만" + "이 부서 데이터만"을 **동시에** 걸어야 하는 경우가 있다. department
축 하나로는 두 기준을 동시에 표현할 수 없어서(둘 다 같은 컬럼 하나를 쓰므로) 완전히
별도인 축을 하나 더 둔다. 값은 고객사마다 다 다르므로(회사명·법인 코드 등) 카탈로그
테이블을 두지 않고 department와 똑같이 자유 텍스트로 둔다 — 특정 고객사 이름을 코드에
고정하지 않는다(범용, 2026-08-20).

구조:
  users.company_code  — 이 사람이 속한 회사/도메인 (자유 텍스트, 예: 'AMT'). 없으면
                         NULL(회사 축 제한 없음 — 기존 SECO 사용자처럼 department/data_scope만
                         쓰는 경우 이 값을 안 채우면 전과 동일하게 동작한다).
  users.company_scope — 'own'(자기 회사만) / 'all'(전체 회사, 임원·관리자용).

v_rls_user_scope 뷰에 company_code/company_scope를 추가한다 — department/data_scope와
같은 방식으로 Power BI DAX가 LOOKUPVALUE(..., USERNAME())로 읽어간다. USERNAME()인 이유:
이 앱은 PPU 계정 1개 + 서비스 주체로 "앱 소유 데이터" 임베딩을 하기 때문에(개인별 AAD
로그인 방식이 아님), PBIX RLS 규칙은 반드시 USERNAME()을 써야 한다 — USERPRINCIPALNAME()은
이 구조에서 항상 빈 값이라 안 된다(2026-08-20, Power BI Desktop 예시 화면 보고 확인).

이 스크립트는 뼈대만 만든다 — 실제 company_code 값은 고객사와 협의 후 관리자 포털
(사용자 편집 → "회사/도메인")에서 사람별로 채운다.

3단계 이상 계층(모회사-자회사)이 필요해지면 이 단일 컬럼으로는 부족하다 — 그때는
company_codes 카탈로그 테이블 + parent_code 패턴을 별도로 설계한다(git 이력의
add_company_hierarchy.py, 2026-08-07~08-12에 있었던 버전 참고, 지금은 그 정도 계층이
필요하다는 확정 요구가 없어 일부러 더 단순한 버전으로 다시 만든다).

실행 방법 (재실행해도 안전 — 전부 IF NOT EXISTS / CREATE OR REPLACE):
    python scripts/add_company_axis.py
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
            cur.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS company_code VARCHAR(30)"
            )
            cur.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS company_scope VARCHAR(16) "
                "NOT NULL DEFAULT 'own' CHECK (company_scope IN ('own', 'all'))"
            )
            # init_schema.py가 매 시작 department/data_scope 2컬럼짜리 기본 뷰를 먼저
            # DROP+CREATE 하므로, 이 스크립트가 항상 그 다음에 실행돼(server.ps1/server.sh
            # 순서) company_code/company_scope를 얹은 최종 형태로 덮어써야 한다. 여기서는
            # 컬럼을 끝에 추가만 하므로 CREATE OR REPLACE로 충분하다(PostgreSQL이 REPLACE로
            # 컬럼 제거·순서변경은 막지만 끝에 추가하는 건 허용).
            cur.execute("""
                CREATE OR REPLACE VIEW v_rls_user_scope AS
                    SELECT pbi_username AS user_key, department, data_scope,
                           company_code, company_scope
                    FROM users
                    WHERE is_active = TRUE
            """)
        conn.commit()
    print("company axis schema ready (users.company_code/company_scope + v_rls_user_scope extended).")


if __name__ == "__main__":
    run()
