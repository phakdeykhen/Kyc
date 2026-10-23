#!/usr/bin/env bash
# Installed by an administrator. Used by a command-restricted CI SSH key.
set -Eeuo pipefail
umask 077
cd /opt/kyc
exec 9>/opt/kyc/deploy.lock
flock -w 900 9
read -r command revision extra <<< "${SSH_ORIGINAL_COMMAND:-}"
if [[ "$command" != deploy || ! "$revision" =~ ^[0-9a-f]{40}$ || -n "$extra" ]]; then
  echo 'Expected: deploy <40-character commit SHA>' >&2
  exit 1
fi
test -s kyc.env
test -s postgres.env
test -s models/face_detection_yunet_2023mar.onnx
release="releases/$revision"
mkdir -p "$release"
# Input is a gzipped docker-save archive, with no shell commands or credentials.
gzip -dc | docker load
api_image="kyc-api:$revision"
web_image="kyc-web:$revision"
docker image inspect "$api_image" "$web_image" >/dev/null
printf 'KYC_API_IMAGE=%s\nKYC_WEB_IMAGE=%s\n' "$api_image" "$web_image" > "$release/images.env"
previous=$(readlink current || true)
compose() { docker compose --env-file "$1" -f compose.yaml "${@:2}"; }
rollback() {
  trap - ERR
  echo 'Release failed; restoring the previous images. Migrations are forward-only and stay applied.' >&2
  if [[ -n "$previous" ]]; then
    compose "$previous/images.env" up -d --wait --wait-timeout 240 || true
  fi
  exit 1
}
trap rollback ERR
compose "$release/images.env" up -d --wait --wait-timeout 120 postgres
# Alembic upgrade and the declared runtime-role grants, as the migration role.
compose "$release/images.env" run --rm migrate
compose "$release/images.env" up -d --wait --wait-timeout 240
curl --fail --silent --show-error --max-time 30 http://127.0.0.1:3400/health/ready \
  | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["status"] == "ready" else 1)'
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:3400/console -o /dev/null
# Production mode: the API must refuse an unauthenticated session request.
status=$(curl --silent -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
  -H 'Host: kyc.opssolution.tech' -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:3400/v1/kyc/sessions)
test "$status" = 401
if [[ -n "$previous" ]]; then ln -sfn "$previous" previous; fi
ln -sfn "$release" current
trap - ERR
echo "Deployed $revision"
