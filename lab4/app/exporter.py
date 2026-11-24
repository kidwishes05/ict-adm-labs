import logging
import os
import time

import psycopg
from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import GaugeMetricFamily


def metric(name, help_text, value):
    return GaugeMetricFamily(
        name,
        help_text,
        value=float(value),
    )


class PostgresCollector:
    def collect(self):
        metrics = []

        try:
            with psycopg.connect(
                host=os.environ["DB_HOST"],
                dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
                connect_timeout=2,
                options="-c statement_timeout=1000",
                autocommit=True,
            ) as conn:
                standby = conn.execute(
                    "SELECT pg_is_in_recovery()"
                ).fetchone()[0]

                used, maximum = conn.execute(
                    """
                    SELECT
                        (
                            SELECT count(*)
                            FROM pg_stat_activity
                            WHERE backend_type = 'client backend'
                        ),
                        current_setting('max_connections')::int
                    """
                ).fetchone()

                metrics.extend([
                    metric(
                        "lab4_pg_is_primary",
                        "1 if this node is currently primary",
                        not standby,
                    ),
                    metric(
                        "lab4_pg_connections",
                        "Client connections including monitoring",
                        used,
                    ),
                    metric(
                        "lab4_pg_max_connections",
                        "Configured connection limit",
                        maximum,
                    ),
                ])

                if standby:
                    paused = conn.execute(
                        "SELECT pg_get_wal_replay_pause_state()"
                    ).fetchone()[0]

                    metrics.append(
                        metric(
                            "lab4_pg_replay_paused",
                            "1 if WAL replay is fully paused",
                            paused == "paused",
                        )
                    )

                else:
                    rows = conn.execute(
                        """
                        SELECT
                            application_name,
                            state,
                            pg_wal_lsn_diff(
                                pg_current_wal_lsn(),
                                replay_lsn
                            )
                        FROM pg_stat_replication
                        """
                    ).fetchall()

                    metrics.append(
                        metric(
                            "lab4_pg_streaming_replicas",
                            "Number of senders in streaming state",
                            sum(
                                state == "streaming"
                                for _, state, _ in rows
                            ),
                        )
                    )

                    lag = GaugeMetricFamily(
                        "lab4_pg_replication_lag_bytes",
                        (
                            "Primary current WAL minus last reported "
                            "standby replay LSN"
                        ),
                        labels=["standby"],
                    )

                    for name, _, behind in rows:
                        if behind is not None:
                            lag.add_metric(
                                [name],
                                max(0, float(behind)),
                            )

                    metrics.append(lag)

            yield metric(
                "lab4_pg_up",
                "1 if all monitoring queries succeeded",
                1,
            )

            yield from metrics

        except psycopg.Error:
            logging.exception(
                "PostgreSQL collection failed for %s",
                os.environ.get("DB_HOST"),
            )

            # При ошибке не выдаём прежние роль и lag как свежие.
            yield metric(
                "lab4_pg_up",
                "1 if all monitoring queries succeeded",
                0,
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    registry = CollectorRegistry()
    registry.register(PostgresCollector())

    start_http_server(9187, registry=registry)

    while True:
        time.sleep(3600)