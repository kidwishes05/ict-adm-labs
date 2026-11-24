"""
picker-service — читает топик как consumer group и "собирает" заказы.

Переменные окружения:
  KAFKA_BOOTSTRAP_SERVERS  адрес Kafka (по умолчанию localhost:9092)
  KAFKA_TOPIC              топик (по умолчанию orders)
  KAFKA_GROUP_ID           consumer group (по умолчанию pickers)
  INSTANCE_NAME            имя инстанса (по умолчанию hostname контейнера)
  PROCESS_SECONDS          сколько секунд "собирается" заказ (по умолчанию 2)
  PORT                     порт HTTP-страницы (по умолчанию 8001)
"""
import json
import os
import socket
import threading
import time
from collections import deque
from datetime import datetime

from confluent_kafka import Consumer, KafkaError
from flask import Flask, render_template_string

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "orders")
GROUP = os.getenv("KAFKA_GROUP_ID", "pickers")
INSTANCE = os.getenv("INSTANCE_NAME", socket.gethostname())
PROCESS_SECONDS = float(os.getenv("PROCESS_SECONDS", "2"))
PORT = int(os.getenv("PORT", "8001"))

state = {
    "assigned": [],        # какие партиции сейчас выдала группа этому инстансу
    "current": None,       # что собирается прямо сейчас
    "events": deque(maxlen=30),  # журнал ребалансов
    "error": None,
}
processed = deque(maxlen=200)
lock = threading.Lock()


def log_event(text: str) -> None:
    with lock:
        state["events"].appendleft(f"{datetime.now():%H:%M:%S} {text}")
    print(f"[{INSTANCE}] {text}", flush=True)


def on_assign(consumer, partitions):
    with lock:
        state["assigned"] = sorted(p.partition for p in partitions)
    log_event(f"ребаланс: назначены партиции {state['assigned'] or '— нет (лишний консьюмер)'}")


def on_revoke(consumer, partitions):
    log_event(f"ребаланс: отозваны партиции {sorted(p.partition for p in partitions)}")
    with lock:
        state["assigned"] = []


def consume_loop():
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP,
        "group.id": GROUP,
        "client.id": INSTANCE,
        # Откуда начать, если у группы ЕЩЁ НЕТ сохранённого offset'а.
        "auto.offset.reset": os.getenv("AUTO_OFFSET_RESET", "earliest"),
        # Коммитим offset вручную и только ПОСЛЕ обработки → at-least-once.
        "enable.auto.commit": False,
        # Чтобы новые партиции подхватывались за секунды, а не за 5 минут.
        "topic.metadata.refresh.interval.ms": 10000,
    })
    consumer.subscribe([TOPIC], on_assign=on_assign, on_revoke=on_revoke)
    log_event(f"подписался на '{TOPIC}' в группе '{GROUP}'")

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                with lock:
                    state["error"] = str(msg.error())
                log_event(f"ошибка: {msg.error()}")
            continue

        with lock:
            state["error"] = None
        try:
            order = json.loads(msg.value())
        except Exception:
            order = {"order_id": "???", "items": [], "raw": msg.value().decode(errors="replace")}

        with lock:
            state["current"] = {"order_id": order.get("order_id"), "partition": msg.partition(),
                                "offset": msg.offset()}

        time.sleep(PROCESS_SECONDS)  # "собираем" заказ

        with lock:
            processed.appendleft({
                "time": datetime.now().strftime("%H:%M:%S"),
                "order_id": order.get("order_id"),
                "items": order.get("items", []),
                "partition": msg.partition(),
                "offset": msg.offset(),
                "instance": INSTANCE,
            })
            state["current"] = None

        # Фиксируем в Kafka: "сообщение с этим offset'ом обработано".
        consumer.commit(message=msg, asynchronous=False)


app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>picker {{ instance }}</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 24px; background: #f4f8f4; color: #1d1d1f; }
  h1 { margin: 0 0 4px; }
  .meta { color: #666; margin-bottom: 16px; }
  .meta code { background: #e3ebe3; padding: 2px 6px; border-radius: 4px; }
  .cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .card { background: #fff; border-radius: 10px; padding: 12px 16px; min-width: 200px;
          box-shadow: 0 1px 2px rgba(0,0,0,.06); }
  .card .label { font-size: 12px; color: #777; text-transform: uppercase; }
  .card .value { font-size: 20px; font-weight: 600; margin-top: 4px; }
  .idle { color: #c0392b; }
  table { border-collapse: collapse; width: 100%; background: #fff; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e3e8e3; font-size: 14px; }
  th { background: #e9f0e9; }
  .p { font-weight: 600; color: #1e8449; }
  .log { font-family: ui-monospace, monospace; font-size: 12px; color: #444; white-space: pre-line;
         background: #fff; padding: 10px; border-radius: 8px; max-height: 160px; overflow: auto; }
  .err { color: #c0392b; }
</style>
</head>
<body>
  <h1>📦 picker-service · {{ instance }}</h1>
  <div class="meta">
    Kafka <code>{{ bootstrap }}</code> · топик <code>{{ topic }}</code> · группа <code>{{ group }}</code>
    · сборка {{ process_seconds }} с/заказ
  </div>

  <div class="cards">
    <div class="card">
      <div class="label">Мои партиции</div>
      {% if assigned %}
        <div class="value">{{ assigned | join(', ') }}</div>
      {% else %}
        <div class="value idle">нет — простаиваю</div>
      {% endif %}
    </div>
    <div class="card">
      <div class="label">Сейчас собираю</div>
      <div class="value">
        {% if current %}{{ current.order_id }} <small>(p{{ current.partition }}, off {{ current.offset }})</small>
        {% else %}—{% endif %}
      </div>
    </div>
    <div class="card">
      <div class="label">Собрано (за сессию)</div>
      <div class="value">{{ total }}</div>
    </div>
  </div>

  {% if error %}<p class="err">⚠ {{ error }}</p>{% endif %}

  <h3>Журнал группы</h3>
  <div class="log">{{ events | join('\n') }}</div>

  <h3>Собранные заказы</h3>
  <table>
    <tr><th>Время</th><th>order_id</th><th>Товары</th><th>Partition</th><th>Offset</th><th>Инстанс</th></tr>
    {% for p in processed %}
    <tr>
      <td>{{ p.time }}</td><td><b>{{ p.order_id }}</b></td><td>{{ p['items'] | join(', ') }}</td>
      <td class="p">{{ p.partition }}</td><td class="p">{{ p.offset }}</td><td>{{ p.instance }}</td>
    </tr>
    {% else %}
    <tr><td colspan="6">Пока ничего не собрано.</td></tr>
    {% endfor %}
  </table>
</body>
</html>
"""


@app.get("/")
def index():
    with lock:
        ctx = dict(
            assigned=list(state["assigned"]), current=state["current"],
            events=list(state["events"]), error=state["error"],
            processed=list(processed), total=len(processed),
        )
    return render_template_string(PAGE, instance=INSTANCE, bootstrap=BOOTSTRAP, topic=TOPIC,
                                  group=GROUP, process_seconds=PROCESS_SECONDS, **ctx)


@app.get("/health")
def health():
    return {"status": "ok", "instance": INSTANCE}


if __name__ == "__main__":
    print(f"[picker-service] {INSTANCE} -> {BOOTSTRAP}, topic={TOPIC}, group={GROUP}, port={PORT}",
          flush=True)
    threading.Thread(target=consume_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
