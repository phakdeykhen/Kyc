#!/usr/bin/env bash
# Starts the built images against a throwaway TLS PostgreSQL in production mode, then checks the
# migration, readiness, SPA routing, a JS asset, the API proxy and credential enforcement.
set -Eeuo pipefail
: "${KYC_API_IMAGE:?Set the API image}"
: "${KYC_WEB_IMAGE:?Set the web image}"
run="kyc-smoke-${GITHUB_RUN_ID:-$$}"
work=$(mktemp -d)
cleanup() {
  result=$?
  if (( result != 0 )); then docker logs "$run-db" || true; docker logs "$run-api" || true; docker logs "$run-web" || true; fi
  docker rm -f "$run-web" "$run-api" "$run-db" >/dev/null 2>&1 || true
  docker network rm "$run" >/dev/null 2>&1 || true
  rm -rf "$work"
  exit "$result"
}
trap cleanup EXIT
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=postgres \
  -keyout "$work/server.key" -out "$work/server.crt" 2>/dev/null
chmod 644 "$work/server.crt" "$work/server.key"
key32() { openssl rand -base64 32; }
cat > "$work/kyc.env" <<EOF
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg2://kyc_app:smoke-app-only@db:5432/kyc?sslmode=require
MIGRATION_DATABASE_URL=postgresql+psycopg2://kyc_migrator:smoke-migrator-only@db:5432/kyc?sslmode=require
CAPTURE_ENCRYPTION_KEYS=cap-v1:$(key32)
PII_ENCRYPTION_KEYS=pii-v1:$(key32)
PII_HMAC_KEY=$(key32)
BIOMETRIC_ENCRYPTION_KEYS=bio-v1:$(key32)
WEBHOOK_SECRET_KEYS=webhook-v1:$(key32)
ALLOWED_HOSTS=kyc.opssolution.tech
REQUIRE_DOCUMENT_CONSENT=true
ENABLE_CAPTURE_CLIENT=false
EXPOSE_API_DOCS=false
CAPTURE_STORAGE_DIR=/tmp/captures
EOF
docker network create "$run" >/dev/null
# The key must belong to the postgres user before the server starts with ssl=on.
docker run -d --name "$run-db" --network "$run" --network-alias db \
  -e POSTGRES_USER=kyc_migrator -e POSTGRES_DB=kyc -e POSTGRES_PASSWORD=smoke-migrator-only \
  -e KYC_APP_PASSWORD=smoke-app-only \
  -v "$PWD/infra/postgres-init.sh:/docker-entrypoint-initdb.d/01-kyc-app.sh:ro" \
  -v "$work:/tls-src:ro" \
  --entrypoint sh postgres:17 -c 'install -o postgres -m 600 /tls-src/server.key /tmp/server.key \
    && install -o postgres -m 644 /tls-src/server.crt /tmp/server.crt \
    && exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key' >/dev/null
for attempt in {1..40}; do
  if docker exec "$run-db" pg_isready -U kyc_migrator -d kyc -h 127.0.0.1 >/dev/null 2>&1; then break; fi
  sleep 2
done
docker run --rm --network "$run" --env-file "$work/kyc.env" "$KYC_API_IMAGE" python scripts/bootstrap_local.py
docker run -d --name "$run-api" --network "$run" --network-alias api --env-file "$work/kyc.env" "$KYC_API_IMAGE" >/dev/null
docker run -d --name "$run-web" --network "$run" -p 127.0.0.1::80 "$KYC_WEB_IMAGE" >/dev/null
port=$(docker port "$run-web" 80/tcp | awk -F: '{print $NF}')
base="http://127.0.0.1:$port"
for attempt in {1..45}; do
  if curl -fsS "$base/health/ready" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS "$base/healthz"
curl -fsS "$base/health/ready" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["status"] == "ready" else 1)'
curl -fsS "$base/console" -o "$work/index.html"
grep -q '<div id="root">' "$work/index.html"
asset=$(grep -oE 'src="/assets/[^"]+\.js"' "$work/index.html" | head -1 | cut -d'"' -f2)
test -n "$asset"
curl -fsS "$base$asset" -o /dev/null
status=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Host: kyc.opssolution.tech' \
  -H 'Content-Type: application/json' -d '{}' "$base/v1/kyc/sessions")
test "$status" = 401
status=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.example' "$base/v1/document-types")
test "$status" = 400
echo 'Smoke test passed: production config, TLS database, migrations, readiness, SPA, asset, API proxy, auth and host checks.'
