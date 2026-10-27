#!/bin/sh
# Runs once on an empty data dir. The app connects as vigia_app: not superuser and
# no BYPASSRLS, so row-level security applies to it (the POSTGRES_USER bypasses RLS).
set -eu
psql -v ON_ERROR_STOP=1 -v pw="$APP_DB_PASSWORD" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE vigia_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'pw';
SQL
