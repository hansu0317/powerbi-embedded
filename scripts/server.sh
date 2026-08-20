#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
# 프로젝트 venv가 있으면 우선 사용, 없으면 PATH의 python3 (환경별 경로 하드코딩 금지)
if [ -z "$PYTHON" ]; then
    if [ -x "$PROJECT_ROOT/venv/bin/python3" ]; then
        PYTHON="$PROJECT_ROOT/venv/bin/python3"
    else
        PYTHON="$(command -v python3)"
    fi
fi
PID_FILE="$PROJECT_ROOT/.server.pid"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/server.log"
# 포트는 .env의 PORT로 오버라이드 가능(없으면 8249) — 환경마다 로컬 포트 점유
# 상황이 달라서(예: 이 저장소를 쓰는 다른 PC는 VS Code가 8247을 물고 있어 8249로
# 피함, 반면 상시 배포된 서버는 nginx가 이미 특정 포트로 프록시 중이라 그 값을 써야
# 함) 스크립트에 고정값을 박아두면 배포 환경마다 서로 값을 덮어쓰는 merge 충돌이
# 계속 난다(2026-08-20 발견). .env는 배포마다 다르고 git 추적도 안 되니 여기가 맞다.
PORT="$(grep -m1 '^PORT=' "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r')"
PORT="${PORT:-8249}"


# 기존 로그를 시각이 포함된 파일명으로 보관하고 15일이 지난 로그를 정리한다.
rotate_log() {
    if [ -f "$LOG_FILE" ]; then
        ARCHIVE_DATE=$(date +%Y%m%d)
        ARCHIVE_TIME=$(date +%H%M%S)
        ARCHIVE_DIR="$LOG_DIR/$ARCHIVE_DATE"
        mkdir -p "$ARCHIVE_DIR"
        mv "$LOG_FILE" "$ARCHIVE_DIR/server-$ARCHIVE_TIME.log"
        echo "이전 로그 → $ARCHIVE_DIR/server-$ARCHIVE_TIME.log"
    fi
    find "$LOG_DIR" -mindepth 1 -type f -name 'server-*.log' -mtime +15 -delete 2>/dev/null
}

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        echo "이미 실행 중입니다. (PID: $(cat $PID_FILE))"
        return
    fi
    mkdir -p "$LOG_DIR"
    # DB 스키마 확인/생성 (버전 이력 없는 단일 스크립트 — 매번 실행해도 안전)
    if ! $PYTHON "$PROJECT_ROOT/scripts/init_schema.py"; then
        echo "DB 스키마 초기화 실패. PostgreSQL과 .env 설정을 확인하세요."
        return 1
    fi
    # GET 필터(PoC) 컬럼 5개 — init_schema.py에 없어 새 DB에서는 로그인 즉시
    # "column filter_key does not exist"로 죽는다(2026-08-12 발견). init_schema.py는
    # 고쳐 쓰지 않는 방침이라, 그 대신 여기서 매 시작마다 같이 실행한다(ALTER ... IF NOT
    # EXISTS라 안전, 재실행 비용 무시할 수준).
    if ! $PYTHON "$PROJECT_ROOT/scripts/add_get_filter_columns.py"; then
        echo "GET 필터 컬럼 추가 실패. PostgreSQL과 .env 설정을 확인하세요."
        return 1
    fi
    rotate_log
    cd "$PROJECT_ROOT"
    nohup "$PYTHON" -m uvicorn main:app \
    	--host 0.0.0.0 \
    	--port "$PORT" \
    	--no-access-log \
    	</dev/null > "$LOG_FILE" 2>&1 &

    PID=$!
    if [ -z "$PID" ]; then
        echo "서버 시작 실패. 로그를 확인하세요: $LOG_FILE"
        return 1
    fi
    echo "$PID" > "$PID_FILE"
    # 시작 시 복구 작업 때문에 포트 바인딩이 늦을 수 있어 최대 15초까지 재시도한다.
    HEALTHY=""
    for _ in $(seq 1 15); do
        if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
            HEALTHY=1
            break
        fi
        sleep 1
    done
    if [ -z "$HEALTHY" ]; then
        echo "서버 상태 확인 실패. 로그를 확인하세요: $LOG_FILE"
        kill "$PID" 2>/dev/null
        rm -f "$PID_FILE"
        return 1
    fi
    echo "서버 시작됨 (PID: $PID)"
    echo "로그: $LOG_FILE"
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "실행 중인 서버가 없습니다."
        return
    fi
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        rm -f "$PID_FILE"
        echo "서버 종료됨 (PID: $PID)"
    else
        rm -f "$PID_FILE"
        echo "이미 종료된 상태입니다."
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        echo "실행 중 (PID: $(cat $PID_FILE))"
    else
        echo "중지됨"
    fi
}

case "$1" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    *)       echo "사용법: $0 {start|stop|restart|status}" ;;
esac
