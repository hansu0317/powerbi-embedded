"""로그인 인증 + 세션 사용자 조회."""
import bcrypt

import config
from database.pool import db_conn

# ── 인증 ─────────────────────────────────────────────────────────────────────

def db_check_and_get_user(username: str, ip: str):
    """로그인 차단 확인 + 사용자 행 조회를 하나의 DB 커넥션에서 처리.

    반환: ("blocked", None) | ("ok", row | None)

    기존에는 db_login_allowed → db_authenticate 로 두 번 커넥션을 열었다.
    차단 체크와 사용자 SELECT를 같은 커넥션 안에서 순서대로 실행해 1회로 줄인다.
    bcrypt 비교는 CPU 집약적이므로 DB 커넥션을 닫은 뒤 호출자가 수행한다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS count FROM event_log "
                "WHERE log_type = 'login' AND username = %s AND ip = %s AND succeeded = FALSE "
                "AND created_at >= NOW() - %s * INTERVAL '1 minute'",
                (username, ip, config.LOGIN_BLOCK_MINUTES),
            )
            if cur.fetchone()["count"] >= config.LOGIN_BLOCK_MAX_FAIL:
                return "blocked", None
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, password, is_admin, is_active "
                "FROM users WHERE username = %s",
                (username,),
            )
            return "ok", cur.fetchone()


def db_verify_password(row, password: str):
    """bcrypt 비교 후 사용자 정보 반환. DB 접근 없는 순수 CPU 연산.

    반환: user dict(성공) / "inactive"(비활성 계정) / None(아이디·비밀번호 불일치)
    """
    if not row:
        return None
    if not bcrypt.checkpw(password.encode(), row["password"].encode()):
        return None
    if not row["is_active"]:
        return "inactive"
    return row


def db_record_login(username: str, ip: str, succeeded: bool):
    """로그인 시도를 기록한다. 성공 시 실패 이력 초기화 + last_login_at 갱신."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if succeeded:
                cur.execute(
                    "DELETE FROM event_log WHERE log_type = 'login' AND username = %s AND ip = %s",
                    (username, ip),
                )
                cur.execute("UPDATE users SET last_login_at = NOW() WHERE username = %s", (username,))
            cur.execute(
                "INSERT INTO event_log (log_type, username, ip, succeeded) VALUES ('login', %s, %s, %s)",
                (username, ip, succeeded),
            )
        conn.commit()


def db_cleanup_login_attempts():
    """30일 초과 로그인 시도 기록을 삭제한다.

    기존에는 db_record_login() 안에서 매 로그인마다 DELETE를 실행했다.
    로그인 응답 경로에서 제거하고 서버 시작 시 + 일 1회 백그라운드에서 실행한다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM event_log WHERE log_type = 'login' AND created_at < NOW() - INTERVAL '30 days'"
            )
        conn.commit()


# ── 사용자 ────────────────────────────────────────────────────────────────────

def db_get_user(username: str):
    """세션 사용자 조회. 비활성 계정은 None — 로그인 이후 비활성화돼도 다음 요청부터 즉시 차단된다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, is_admin, can_upload "
                "FROM users WHERE username = %s AND is_active = TRUE",
                (username,),
            )
            return cur.fetchone()
