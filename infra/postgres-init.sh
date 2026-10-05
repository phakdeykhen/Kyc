#!/bin/sh
set -eu
# The official image sources non-executable .sh files during first initialization.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=app_password="$KYC_APP_PASSWORD" <<'SQL'
CREATE ROLE kyc_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD :'app_password';
GRANT CONNECT ON DATABASE kyc TO kyc_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
