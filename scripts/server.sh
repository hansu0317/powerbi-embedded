#!/bin/bash
PYTHON=/home/test01/.pyenv/versions/3.11.9/bin/python3
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_ROOT/.server.pid"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/server.log"

SSL_CERT="/etc/ssl/powerbi-gateway/cert.pem"
SSL_KEY="/etc/ssl/powerbi-gateway/key.pem"

# 인증서가 있으면 HTTPS, 없으면 HTTP
if [ -f "$SSL_CERT" ] && [ -f "$SSL_KEY" ]; then
    SSL_ARGS="--ssl-keyfile $SSL_KEY --ssl-certfile $SSL_CERT"
    SCHEME="https"
else
    SSL_ARGS=""
    SCHEME="http"
fi

# 기존 로그를 시각이 포함된 파일명으로 보관하고 30일이 지난 로그를 정리한다.
rotate_log() {
    if [ -f "$LOG_FILE" ]; then
        ARCHIVE_DATE=$(date +%Y%m%d)
        ARCHIVE_TIME=$(date +%H%M%S)
        ARCHIVE_DIR="$LOG_DIR/$ARCHIVE_DATE"
        mkdir -p "$ARCHIVE_DIR"
        mv "$LOG_FILE" "$ARCHIVE_DIR/server-$ARCHIVE_TIME.log"
        echo "이전 로그 → $ARCHIVE_DIR/server-$ARCHIVE_TIME.log"
    fi
    find "$LOG_DIR" -mindepth 1 -type f -name 'server-*.log' -mtime +30 -delete 2>/dev/null
}

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        echo "이미 실행 중입니다. (PID: $(cat $PID_FILE))"
        return
    fi
    mkdir -p "$LOG_DIR"
    if ! $PYTHON "$PROJECT_ROOT/scripts/migrate_report_meta.py"; then
        echo "DB 마이그레이션 실패. PostgreSQL과 .env 설정을 확인하세요."
        return 1
    fi
    rotate_log
    cd "$PROJECT_ROOT"
    [ -n "$SSL_ARGS" ] && echo "SSL 모드: $SSL_CERT"
    # shellcheck disable=SC2086
    setsid -f $PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8247 $SSL_ARGS \
        </dev/null > "$LOG_FILE" 2>&1
    sleep 2
    PID=$(pgrep -n -f "$PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8247")
    if [ -z "$PID" ]; then
        echo "서버 시작 실패. 로그를 확인하세요: $LOG_FILE"
        return 1
    fi
    echo "$PID" > "$PID_FILE"
    # 시작 시 복구 작업 때문에 포트 바인딩이 늦을 수 있어 최대 15초까지 재시도한다.
    HEALTHY=""
    for _ in $(seq 1 15); do
        if curl -fsS --max-time 3 --insecure "${SCHEME}://127.0.0.1:8247/health" >/dev/null 2>&1; then
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
    echo "서버 시작됨 (PID: $PID) — ${SCHEME}://$(hostname -I | awk '{print $1}'):8247"
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
