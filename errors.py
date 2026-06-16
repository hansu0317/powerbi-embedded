"""중앙 에러 레지스트리.

모든 HTTPException은 여기 정의된 AppError를 통해 발생시킨다.
각 항목: (http_status: int, error_code: str, message_ko: str)

message_ko는 str.format(**kwargs) 형식을 지원한다.
  예) AppError.RATE_UPLOAD_DAILY.http(max=10)
"""
from __future__ import annotations

from enum import Enum
from fastapi import HTTPException


class AppError(Enum):
    # ── 인증/권한 ─────────────────────────────────────────────────────────────
    NOT_AUTHENTICATED    = (401, "AUTH_REQUIRED",         "로그인이 필요합니다.")
    FORBIDDEN_ADMIN      = (403, "ADMIN_REQUIRED",        "관리자만 접근할 수 있습니다.")
    FORBIDDEN_REPORT     = (403, "REPORT_FORBIDDEN",      "해당 보고서에 접근 권한이 없습니다.")
    CSRF_INVALID         = (403, "CSRF_INVALID",          "잘못된 요청입니다. 페이지를 새로고침해 주세요.")

    # ── 리소스 없음 ───────────────────────────────────────────────────────────
    REPORT_NOT_FOUND     = (404, "REPORT_NOT_FOUND",      "등록되지 않은 보고서입니다.")
    REPORT_DELETED       = (404, "REPORT_DELETED",        "삭제된 보고서입니다.")
    USER_NOT_FOUND       = (404, "USER_NOT_FOUND",        "사용자를 찾을 수 없거나 admin 계정은 변경 불가합니다.")

    # ── 충돌 ──────────────────────────────────────────────────────────────────
    REPORT_NAME_CONFLICT = (409, "REPORT_NAME_CONFLICT",  "'{name}'은(는) 이미 등록된 보고서입니다. 다른 이름을 사용해 주세요.")
    UPLOAD_IN_PROGRESS   = (409, "UPLOAD_IN_PROGRESS",    "'{name}' 보고서를 이미 처리 중이거나 등록했습니다.")
    UPLOAD_NAME_CONFLICT = (409, "UPLOAD_NAME_CONFLICT",  "'{name}' 보고서 등록 중 이름 충돌이 발생했습니다. 다른 이름으로 다시 올려주세요.")
    IMPORT_NAME_CONFLICT = (409, "IMPORT_NAME_CONFLICT",  "워크스페이스에 같은 이름의 항목이 있습니다. 기존 항목은 변경하지 않았습니다.")
    USER_ALREADY_EXISTS  = (409, "USER_EXISTS",           "'{username}' 아이디가 이미 존재합니다.")
    REPORT_ALREADY_DELETED = (404, "REPORT_ALREADY_DELETED", "보고서가 없거나 이미 삭제됐습니다.")

    # ── 요청 한도 초과 ────────────────────────────────────────────────────────
    RATE_PERSONAL_MAX    = (429, "RATE_PERSONAL_MAX",     "개인 보고서는 최대 {max}개까지 등록할 수 있습니다.")
    RATE_UPLOAD_DAILY    = (429, "RATE_UPLOAD_DAILY",     "하루 업로드는 최대 {max}회입니다.")
    LOGIN_RATE_LIMIT     = (429, "LOGIN_RATE_LIMIT",      "로그인 실패 횟수가 많습니다. 15분 후 다시 시도해 주세요.")

    # ── 파일 유효성 ───────────────────────────────────────────────────────────
    FILE_WRONG_TYPE      = (400, "FILE_WRONG_TYPE",       ".pbix 파일만 업로드할 수 있습니다.")
    FILE_EMPTY           = (400, "FILE_EMPTY",            "빈 파일입니다.")
    FILE_TOO_LARGE       = (413, "FILE_TOO_LARGE",        "파일이 너무 큽니다 (최대 {max_mb}MB).")
    FILE_INVALID_CONTENT = (400, "FILE_INVALID_CONTENT",  "유효한 PBIX 파일이 아닙니다.")
    NAME_INVALID         = (400, "NAME_INVALID",          "보고서 이름은 1~{max}자여야 합니다.")

    # ── 사용자 입력 ───────────────────────────────────────────────────────────
    PASSWORD_TOO_SHORT   = (400, "PASSWORD_TOO_SHORT",    "비밀번호는 {min}자 이상이어야 합니다.")

    # ── 외부 서비스 오류 ──────────────────────────────────────────────────────
    TOKEN_FAILED         = (500, "TOKEN_FAILED",          "Azure AD 토큰 발급 실패: {detail}")
    EMBED_TOKEN_FAILED   = (502, "EMBED_TOKEN_FAILED",    "임베드 토큰 발급 실패: {detail}")
    REPORT_FETCH_FAILED  = (502, "REPORT_FETCH_FAILED",   "보고서 조회 실패: {detail}")
    FOLDER_FAILED        = (502, "FOLDER_FAILED",         "사용자 폴더 준비 실패: {detail}")
    IMPORT_UNCONFIRMED   = (502, "IMPORT_UNCONFIRMED",    "Power BI 응답을 확인할 수 없어 재업로드를 차단했습니다. 관리자가 작업 상태를 확인해야 합니다.")
    IMPORT_REQUEST_FAILED= (502, "IMPORT_REQUEST_FAILED", "게시 요청 실패: {detail}")
    IMPORT_POLL_FAILED   = (502, "IMPORT_POLL_FAILED",    "게시 상태 조회 실패: {detail}")
    IMPORT_FAILED        = (502, "IMPORT_FAILED",         "게시 실패: {detail}")
    IMPORT_NO_REPORT     = (502, "IMPORT_NO_REPORT",      "게시는 됐지만 보고서 정보를 받지 못했습니다.")
    IMPORT_TIMEOUT       = (504, "IMPORT_TIMEOUT",        "게시 처리 시간 초과. 잠시 후 워크스페이스를 확인하세요.")

    # ── 서버/DB ───────────────────────────────────────────────────────────────
    DB_UNAVAILABLE       = (503, "DB_UNAVAILABLE",        "데이터베이스를 사용할 수 없습니다.")
    UPLOAD_INTERNAL      = (500, "UPLOAD_INTERNAL",       "업로드 처리 중 오류가 발생했습니다. 관리자 로그를 확인하세요.")
    UPLOAD_DB_FAILED     = (500, "UPLOAD_DB_FAILED",
                            "Fabric 게시에는 성공했지만 게이트웨이 DB 등록에 실패했습니다. "
                            "같은 파일을 다시 올리지 말고 관리자에게 로그의 Report ID를 전달해 주세요.")

    # ─────────────────────────────────────────────────────────────────────────

    @property
    def status(self) -> int:
        return self.value[0]

    @property
    def code(self) -> str:
        return self.value[1]

    @property
    def message(self) -> str:
        return self.value[2]

    def http(self, **kwargs) -> HTTPException:
        msg = self.message.format(**kwargs) if kwargs else self.message
        return HTTPException(
            status_code=self.status,
            detail={"code": self.code, "message": msg},
        )
