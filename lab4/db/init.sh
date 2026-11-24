#!/usr/bin/env bash
set -Eeuo pipefail

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 \
  --set app_password="$APP_PASSWORD" \
  --set repl_password="$REPL_PASSWORD" \
  --set monitor_password="$MONITOR_PASSWORD" <<'SQL'

CREATE ROLE app_user LOGIN PASSWORD :'app_password';
CREATE ROLE replicator LOGIN REPLICATION PASSWORD :'repl_password';
CREATE ROLE monitor LOGIN PASSWORD :'monitor_password';

GRANT pg_monitor TO monitor;

GRANT CONNECT ON DATABASE lab4 TO app_user, monitor;

CREATE TABLE public.notes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT ON public.notes TO app_user;
GRANT USAGE, SELECT ON SEQUENCE public.notes_id_seq TO app_user;

SQL