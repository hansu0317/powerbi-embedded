#!/bin/bash
# Azure App Service + Azure Database for PostgreSQL 배포 스크립트
# 실행 전: az login 완료 상태

set -e

# ── 변수 수정 필요 ────────────────────────────────────────
RESOURCE_GROUP="rg-powerbi-gateway"
LOCATION="koreacentral"
APP_NAME="powerbi-gateway"
PLAN_NAME="plan-powerbi-gateway"
DB_SERVER="psql-powerbi-gateway"
DB_NAME="powerbi_gateway"
DB_USER="pgadmin"
DB_PASSWORD="변경필수!Str0ng"
# ─────────────────────────────────────────────────────────

echo "1. 리소스 그룹 생성"
az group create --name $RESOURCE_GROUP --location $LOCATION

echo "2. App Service 플랜 생성 (B2: 2vCPU, 3.5GB)"
az appservice plan create \
  --name $PLAN_NAME \
  --resource-group $RESOURCE_GROUP \
  --sku B2 \
  --is-linux

echo "3. Web App 생성 (Python 3.11)"
az webapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --plan $PLAN_NAME \
  --runtime "PYTHON:3.11"

echo "4. PostgreSQL Flexible Server 생성"
az postgres flexible-server create \
  --name $DB_SERVER \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --admin-user $DB_USER \
  --admin-password $DB_PASSWORD \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --version 15

echo "5. DB 생성"
az postgres flexible-server db create \
  --server-name $DB_SERVER \
  --resource-group $RESOURCE_GROUP \
  --database-name $DB_NAME

echo "6. App Service 환경변수 설정"
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    DB_HOST="$DB_SERVER.postgres.database.azure.com" \
    DB_PORT=5432 \
    DB_NAME=$DB_NAME \
    DB_USER="$DB_USER" \
    DB_PASSWORD="$DB_PASSWORD" \
    DB_POOL_MIN=2 \
    DB_POOL_MAX=10 \
    COOKIE_SECURE=true \
    FABRIC_SYNC_INTERVAL=600

echo "7. 시작 명령어 설정"
az webapp config set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --startup-file "python3 scripts/migrate_report_meta.py && uvicorn main:app --host 0.0.0.0 --port 8000"

echo ""
echo "✅ 완료"
echo "남은 작업:"
echo "  - az webapp config appsettings set ... SECRET_KEY=랜덤값 추가"
echo "  - az webapp config appsettings set ... TENANT_ID, CLIENT_ID, CLIENT_SECRET, WORKSPACE_ID 추가"
echo "  - git push 또는 az webapp deploy로 코드 배포"
echo "  - 사이트: https://$APP_NAME.azurewebsites.net"
