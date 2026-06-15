FROM python:3.11-slim

WORKDIR /app

# 의존성 먼저 설치 (캐시 레이어 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 복사
COPY main.py .
COPY scripts/ scripts/
COPY templates/ templates/

# 로그 디렉토리
RUN mkdir -p logs

# 포트
EXPOSE 8082

# 시작 시 마이그레이션 실행 후 서버 기동
CMD ["sh", "-c", "python3 scripts/migrate_report_meta.py && uvicorn main:app --host 0.0.0.0 --port 8082 --workers 2"]
