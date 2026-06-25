#!/usr/bin/env bash
# 사내 자체 서명(Self-Signed) SSL 인증서 생성 스크립트
# 용도: 외부 도메인 없는 사내망 전용 서버
#
# 생성 결과:
#   /etc/ssl/powerbi-gateway/cert.pem  (인증서 — Nginx + 클라이언트 배포용)
#   /etc/ssl/powerbi-gateway/key.pem   (개인키  — 서버 전용, 유출 금지)
#
# 클라이언트 신뢰 등록 방법은 스크립트 하단 안내 참고

set -euo pipefail

# ── 설정 (환경에 맞게 수정) ───────────────────────────────────────────────────
SERVER_IP="$(hostname -I | awk '{print $1}')"        # 서버 IP (자동 감지)
SERVER_HOSTNAME="$(hostname -f 2>/dev/null || hostname)"
DOMAIN="qualiportal.com"                             # 접속에 사용할 도메인 (빈 문자열이면 제외)
CERT_DIR="/etc/ssl/powerbi-gateway"
DAYS=3650                                            # 인증서 유효 기간 (10년)
ORG="Company"                                        # 회사명

# ── 수동 지정이 필요한 경우 값 변경 ───────────────────────────────────────────
# SERVER_IP="192.168.1.100"
# SERVER_HOSTNAME="pbi-server"
# DOMAIN=""   # 도메인 없이 IP만 쓰려면 빈 문자열
# ─────────────────────────────────────────────────────────────────────────────

echo "=== PowerBI Gateway SSL 인증서 생성 ==="
echo "  서버 IP   : $SERVER_IP"
echo "  서버 호스트: $SERVER_HOSTNAME"
echo "  도메인    : ${DOMAIN:-(없음)}"
echo "  저장 위치 : $CERT_DIR"
echo "  유효 기간 : ${DAYS}일"
echo ""
read -rp "계속 진행합니까? (y/N) " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "취소됨"; exit 0; }

# 디렉토리 생성 (루트 권한 필요)
sudo mkdir -p "$CERT_DIR"
sudo chmod 750 "$CERT_DIR"

# OpenSSL SAN 설정 파일 생성
#   → 최신 브라우저(Chrome 58+)는 CN 무시, SAN 필수
TMPCONF=$(mktemp /tmp/ssl_openssl_XXXX.cnf)
cat > "$TMPCONF" << EOF
[req]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
x509_extensions    = v3_req

[dn]
C  = KR
O  = ${ORG}
CN = ${SERVER_HOSTNAME}

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
IP.1  = ${SERVER_IP}
IP.2  = 127.0.0.1
DNS.1 = ${SERVER_HOSTNAME}
DNS.2 = localhost
$([ -n "${DOMAIN}" ] && echo "DNS.3 = ${DOMAIN}")
EOF

# 인증서 + 개인키 생성
sudo openssl req -x509 \
    -newkey rsa:2048 \
    -keyout "${CERT_DIR}/key.pem" \
    -out    "${CERT_DIR}/cert.pem" \
    -days   "$DAYS" \
    -nodes \
    -config "$TMPCONF"

rm -f "$TMPCONF"

# 권한 설정 (개인키는 root만 읽기)
sudo chmod 644 "${CERT_DIR}/cert.pem"
sudo chmod 600 "${CERT_DIR}/key.pem"

echo ""
echo "✓ 인증서 생성 완료"
echo "  인증서 : ${CERT_DIR}/cert.pem"
echo "  개인키  : ${CERT_DIR}/key.pem"
echo ""

# 인증서 정보 출력
echo "=== 인증서 정보 ==="
openssl x509 -in "${CERT_DIR}/cert.pem" -noout -text \
    | grep -E "Subject:|DNS:|IP Address:|Not After"
echo ""

cat << 'GUIDE'
=== 클라이언트 PC에서 인증서 신뢰 등록 방법 ===

cert.pem 파일을 클라이언트 PC로 복사한 뒤:

  [Windows — 개별 PC]
    1. cert.pem → cert.crt 로 이름 변경
    2. 더블클릭 → "인증서 설치..."
    3. "로컬 컴퓨터" 선택 → "신뢰할 수 있는 루트 인증 기관"에 설치
    4. 브라우저 재시작

  [Windows — Active Directory(GPO) 일괄 배포]
    1. 그룹 정책 관리 열기 (gpmc.msc)
    2. 정책 생성 or 편집 →
       컴퓨터 구성 > Windows 설정 > 보안 설정
       > 공개 키 정책 > 신뢰할 수 있는 루트 인증 기관
    3. cert.crt 가져오기
    4. 도메인 PC에 자동 배포됨 (gpupdate /force)
    → AD 있으면 이 방법이 가장 편함

  [macOS]
    sudo security add-trusted-cert -d -r trustRoot \
         -k /Library/Keychains/System.keychain cert.pem

  [Linux (Ubuntu/Debian)]
    sudo cp cert.pem /usr/local/share/ca-certificates/powerbi-gateway.crt
    sudo update-ca-certificates

GUIDE

echo "다음 단계: sudo nginx -t && sudo systemctl reload nginx"
