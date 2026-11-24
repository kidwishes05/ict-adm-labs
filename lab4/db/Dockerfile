FROM postgres:17-bookworm

RUN mkdir -p /scripts /etc/postgresql \
    && chmod 755 /scripts /etc/postgresql

COPY --chmod=0644 postgresql.conf /etc/postgresql/postgresql.conf
COPY --chmod=0644 pg_hba.conf /etc/postgresql/pg_hba.conf
COPY --chmod=0644 init.sh /docker-entrypoint-initdb.d/01-init.sh
COPY --chmod=0644 replica-entrypoint.sh /scripts/replica-entrypoint.sh