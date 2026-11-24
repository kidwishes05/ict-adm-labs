import os
import time
import uuid

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16384


def connect(host, *, autocommit=False):
    return psycopg.connect(
        host=host,
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=2,
        options="-c statement_timeout=5000",
        autocommit=autocommit,
        row_factory=dict_row,
    )


@app.get("/")
def index():
    return render_template(
        "index.html",
        write_host=os.environ["WRITE_HOST"],
        read_host=os.environ["READ_HOST"],
    )


@app.get("/api/notes")
def notes():
    # И список, и счётчики читаются только из READ_HOST.
    with connect(os.environ["READ_HOST"]) as conn:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )

        state = conn.execute(
            """
            SELECT
                pg_is_in_recovery() AS is_standby,
                count(*) AS total,
                max(id) AS max_id
            FROM notes
            """
        ).fetchone()

        rows = conn.execute(
            """
            SELECT id, body, created_at
            FROM notes
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

    for row in rows:
        row["created_at"] = row["created_at"].isoformat()

    return jsonify(
        node=os.environ["READ_HOST"],
        **state,
        notes=rows,
    )


@app.post("/api/notes")
def create_note():
    payload = request.get_json(silent=True) or {}

    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object"), 400

    body = payload.get(
        "body",
        "Заметка " + uuid.uuid4().hex[:8],
    )

    if (
        not isinstance(body, str)
        or not body.strip()
        or len(body) > 2000
    ):
        return jsonify(
            error="body must be a nonempty string of at most 2000 characters"
        ), 400

    with connect(os.environ["WRITE_HOST"]) as conn:
        row = conn.execute(
            "INSERT INTO notes(body) VALUES (%s) RETURNING id",
            (body,),
        ).fetchone()

    # Выход из with уже выполнил COMMIT.
    return jsonify(
        id=row["id"],
        node=os.environ["WRITE_HOST"],
    ), 201


@app.post("/api/load")
def load():
    payload = request.get_json(silent=True) or {}

    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object"), 400

    count = payload.get("count", 1000)

    if type(count) is not int or not 1 <= count <= 2000:
        return jsonify(
            error="count must be an integer between 1 and 2000"
        ), 400

    started = time.monotonic()
    committed = 0
    first_id = None
    last_id = None
    batch = uuid.uuid4().hex[:8]

    try:
        # Каждая запись фиксируется отдельной транзакцией.
        with connect(
            os.environ["WRITE_HOST"],
            autocommit=True,
        ) as conn:
            for i in range(count):
                body = (
                    f"batch={batch}, row={i + 1}: "
                    + uuid.uuid4().hex * 32
                )

                row = conn.execute(
                    "INSERT INTO notes(body) VALUES (%s) RETURNING id",
                    (body,),
                ).fetchone()

                committed += 1
                last_id = row["id"]

                if first_id is None:
                    first_id = last_id

    except psycopg.Error:
        app.logger.exception("Load interrupted")

        return jsonify(
            error=(
                "Database unavailable; "
                "last in-flight write may have committed"
            ),
            acknowledged=committed,
            first_id=first_id,
            last_id=last_id,
        ), 503

    return jsonify(
        batch=batch,
        committed=committed,
        first_id=first_id,
        last_id=last_id,
        seconds=round(time.monotonic() - started, 3),
        node=os.environ["WRITE_HOST"],
    )


@app.errorhandler(psycopg.Error)
def database_error(exc):
    app.logger.exception("Database request failed")

    return jsonify(
        error=(
            "Database operation failed; inspect app logs. "
            "A failed response does not prove rollback."
        )
    ), 503