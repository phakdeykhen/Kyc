#!/usr/bin/env bash
# Run once as root after copying deploy/*, infra/postgres-init.sh, kyc-ci-key.pub and the
# verified face models (models/) to /opt/kyc. Generates secrets on the VM only; never overwrites them.
set -Eeuo pipefail
test "$(id -u)" = 0
cd /opt/kyc
umask 077
test -s kyc-ci-key.pub
test -s postgres-init.sh
test -s models/face_detection_yunet_2023mar.onnx
chmod 700 /opt/kyc receive-release.sh
chmod 644 postgres-init.sh
chmod -R a+rX models

key32() { openssl rand -base64 32; }
if [[ ! -s postgres.env ]]; then
  migrator=$(openssl rand -hex 32)
  app=$(openssl rand -hex 32)
  printf 'POSTGRES_USER=kyc_migrator\nPOSTGRES_DB=kyc\nPOSTGRES_PASSWORD=%s\nKYC_APP_PASSWORD=%s\n' "$migrator" "$app" > postgres.env
  {
    echo "# Generated on the VM by bootstrap.sh. Back up separately from the database: losing it loses the data."
    echo "ENVIRONMENT=production"
    echo "DATABASE_URL=postgresql+psycopg2://kyc_app:$app@postgres:5432/kyc?sslmode=require"
    echo "MIGRATION_DATABASE_URL=postgresql+psycopg2://kyc_migrator:$migrator@postgres:5432/kyc?sslmode=require"
    echo "CAPTURE_ENCRYPTION_KEYS=cap-v1:$(key32)"
    echo "PII_ENCRYPTION_KEYS=pii-v1:$(key32)"
    echo "PII_HMAC_KEY=$(key32)"
    echo "BIOMETRIC_ENCRYPTION_KEYS=bio-v1:$(key32)"
    echo "WEBHOOK_SECRET_KEYS=webhook-v1:$(key32)"
    echo "ALLOWED_HOSTS=kyc.opssolution.tech"
    echo "REQUIRE_DOCUMENT_CONSENT=true"
    echo "ENABLE_CAPTURE_CLIENT=false"
    echo "EXPOSE_API_DOCS=false"
    echo "WEBHOOK_ALLOW_PRIVATE_TARGETS=false"
    echo "FACE_RECOGNITION_ENABLED=false"
    # Shared 2-vCPU VM: one API worker, OCR inline.
    echo "WEB_CONCURRENCY=1"
    echo "DB_POOL_SIZE=5"
    echo "DB_MAX_OVERFLOW=5"
    echo "DOCUMENT_PROCESSING_MODE=inline"
    echo "SESSION_TTL_SECONDS=900"
  } > kyc.env
  echo "Generated postgres.env and kyc.env (mode 600); no secrets printed."
fi
chmod 600 postgres.env kyc.env

# Self-signed TLS for the private database link (postgres runs as uid 999 in the image).
if [[ ! -s pg-tls/server.key ]]; then
  install -d -m 755 pg-tls
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=postgres \
    -keyout pg-tls/server.key -out pg-tls/server.crt 2>/dev/null
fi
chown 999:999 pg-tls/server.key pg-tls/server.crt
chmod 600 pg-tls/server.key
chmod 644 pg-tls/server.crt

install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
key=$(cat kyc-ci-key.pub)
if ! grep -qF "$key" /root/.ssh/authorized_keys; then
  printf 'restrict,command="/opt/kyc/receive-release.sh" %s\n' "$key" >> /root/.ssh/authorized_keys
fi
install -m 644 nginx.conf /etc/nginx/sites-available/kyc
ln -sfn /etc/nginx/sites-available/kyc /etc/nginx/sites-enabled/kyc
nginx -t
systemctl reload nginx
certbot --nginx --non-interactive --agree-tos --redirect \
  --cert-name kyc.opssolution.tech -d kyc.opssolution.tech
