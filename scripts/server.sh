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
    # 선택 인자: DB 목표 버전 (예: `server.sh start v2` 또는 `start 2`)
    # 미지정 시 최신 버전까지 적용. DB_TARGET_VERSION 환경변수로도 지정 가능.
    DB_TARGET="$1"
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        echo "이미 실행 중입니다. (PID: $(cat $PID_FILE))"
        return
    fi
    mkdir -p "$LOG_DIR"
    MIGRATE_ARGS=""
    if [ -n "$DB_TARGET" ]; then
        MIGRATE_ARGS="--target ${DB_TARGET#v}"   # 'v2' → '2'
        echo "DB 마이그레이션 목표: v${DB_TARGET#v}"
    fi
    if ! $PYTHON "$PROJECT_ROOT/scripts/migrate_report_meta.py" $MIGRATE_ARGS; then
        echo "DB 마이그레이션 실패. PostgreSQL과 .env 설정을 확인하세요."
        return 1
    fi
    rotate_log
    cd "$PROJECT_ROOT"
    nohup "$PYTHON" -m uvicorn main:app \
    	--host 0.0.0.0 \
    	--port 8247 \
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
        if curl -fsS --max-time 3 http://127.0.0.1:8247/health >/dev/null 2>&1; then
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
    start)   start "$2" ;;
    stop)    stop ;;
    restart) stop; sleep 1; start "$2" ;;
    status)  status ;;
    *)       echo "사용법: $0 {start|stop|restart|status} [DB버전]"
             echo "  예: $0 start        # DB를 최신 버전까지 마이그레이션 후 시작"
             echo "      $0 start v1     # DB를 v1까지만 적용하고 시작" ;;
esac
