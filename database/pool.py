"""PostgreSQL 커넥션 풀 + db_conn 컨텍스트 매니저.

다른 모든 database 서브모듈이 여기서 db_conn을 가져다 쓴다. 풀 객체(db_pool)는
이 모듈 바깥에서 직접 참조하지 않는다 — 항상 db_conn()을 통해서만 커넥션을 빌린다.
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

from config import DB_CONFIG

# ── 커넥션 풀 ─────────────────────────────────────────────────────────────────
# 요청마다 연결을 새로 맺지 않도록 프로세스당 풀을 사용한다.
# 모든 쓰기 함수는 명시적으로 commit → 반환 전 rollback으로 트랜잭션 잔재만 정리한다.
db_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=int(os.getenv("DB_POOL_MIN", "2")),
    maxconn=int(os.getenv("DB_POOL_MAX", "20")),
    cursor_factory=psycopg2.extras.RealDictCursor,
    **DB_CONFIG,
)


@contextmanager
def db_conn():
    conn = db_pool.getconn()
    broken = False
    try:
        yield conn
        conn.rollback()
    except psycopg2.Error:
        broken = True
        raise
    except Exception:
        try:
            conn.rollback()
        except psycopg2.Error:
            broken = True
        raise
    finally:
        db_pool.putconn(conn, close=broken)


# ── 헬스 ─────────────────────────────────────────────────────────────────────

def db_health_check():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
