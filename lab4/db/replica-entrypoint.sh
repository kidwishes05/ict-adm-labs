#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$(id -u)" = '0' ]; then
  mkdir -p /var/lib/postgresql/data
  chown postgres:postgres /var/lib/postgresql/data
  exec gosu postgres bash "$0" "$@"
fi

# Файл пароля создаётся при каждом запуске контейнера.
# Экранируем специальные символы формата .pgpass.
escaped_password=${REPL_PASSWORD//\\/\\\\}
escaped_password=${escaped_password//:/\\:}

umask 077

printf 'primary:5432:replication:replicator:%s\n' \
  "$escaped_password" > /var/lib/postgresql/.pgpass

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  # Сначала создаём копию в отдельном каталоге.
  # Неудачный backup не должен выглядеть как готовая БД.
  stage="${PGDATA}.bootstrap"

  if [ -e "$stage" ]; then
    echo "Incomplete bootstrap at $stage; inspect it before retrying." >&2
    exit 1
  fi

  trap 'rm -rf -- "$stage"' EXIT

  pg_basebackup \
    --dbname="host=primary port=5432 user=replicator application_name=replica passfile=/var/lib/postgresql/.pgpass" \
    --pgdata="$stage" \
    --wal-method=stream \
    --write-recovery-conf \
    --checkpoint=fast \
    --progress

  mv "$stage" "$PGDATA"
  trap - EXIT
fi

exec postgres -c config_file=/etc/postgresql/postgresql.conf