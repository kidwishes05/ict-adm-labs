"""
user-service — создаёт заказы и публикует событие "order created" в Kafka.

Переменные окружения:
  KAFKA_BOOTSTRAP_SERVERS  адрес Kafka (по умолчанию localhost:9092)
  KAFKA_TOPIC              топик для заказов (по умолчанию orders)
  INSTANCE_NAME            имя инстанса (по умолчанию hostname контейнера)
  PORT                     порт HTTP-страницы (по умолчанию 8000)
"""
import json
import os
import random
import socket
import threading
import uuid
from collections import deque
from datetime import datetime

from confluent_kafka import Producer
from flask import Flask, redirect, render_template_string, request, url_for

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "orders")
INSTANCE = os.getenv("INSTANCE_NAME", socket.gethostname())
PORT = int(os.getenv("PORT", "8000"))

CATALOG = [
    "молоко", "хлеб", "яйца 10 шт", "бананы", "сыр", "кофе",
    "йогурт", "помидоры", "курица", "вода 1.5 л", "гречка", "яблоки",
]

# acks=all — брокер подтверждает запись только после записи на все in-sync реплики.
producer = Producer({
    "bootstrap.servers": BOOTSTRAP,
    "client.id": INSTANCE,
    "acks": "all",
    # Как часто перечитывать метаданные топика. По умолчанию 5 минут —
    # уменьшаем, чтобы новые партиции (Часть 3) были видны почти сразу.
    "topic.metadata.refresh.interval.ms": 10000,
})

sent = deque(maxlen=100)   # история отправленного для страницы
lock = threading.Lock()

app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>user-service</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 24px; background: #f6f7f9; color: #1d1d1f; }
  h1 { margin: 0 0 4px; }
  .meta { color: #666; margin-bottom: 20px; }
  .meta code { background: #e8eaee; padding: 2px 6px; border-radius: 4px; }
  form { display: inline-block; margin-right: 8px; }
  button { font-size: 16px; padding: 10px 18px; border: 0; border-radius: 8px; cursor: pointer;
           background: #2f6fed; color: #fff; }
  button.secondary { background: #5a6270; }
  table { border-collapse: collapse; width: 100%; margin-top: 20px; background: #fff; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e3e5e8; font-size: 14px; }
  th { background: #eef0f3; }
  .p { font-weight: 600; color: #2f6fed; }
  .err { color: #c0392b; }
  .pending { color: #b7791f; }
</style>
</head>
<body>
  <h1>🛒 user-service</h1>
  <div class="meta">
    инстанс <code>{{ instance }}</code> · Kafka <code>{{ bootstrap }}</code> · топик <code>{{ topic }}</code>
  </div>

  <form method="post" action="{{ url_for('create_order') }}">
    <input type="hidden" name="count" value="1">
    <button>Создать заказ</button>
  </form>
  <form method="post" action="{{ url_for('create_order') }}">
    <input type="hidden" name="count" value="10">
    <button class="secondary">Создать 10 заказов</button>
  </form>
  <a href="{{ url_for('index') }}">обновить</a>

  <table>
    <tr><th>Время</th><th>order_id</th><th>Товары</th><th>Partition</th><th>Offset</th><th>Статус</th></tr>
    {% for e in sent %}
    <tr>
      <td>{{ e.order.created_at }}</td>
      <td><b>{{ e.order.order_id }}</b></td>
      <td>{{ e.order['items'] | join(', ') }}</td>
      <td class="p">{{ e.partition if e.partition is not none else '—' }}</td>
      <td class="p">{{ e.offset if e.offset is not none else '—' }}</td>
      <td>
        {% if e.error %}<span class="err">❌ {{ e.error }}</span>
        {% elif e.partition is none %}<span class="pending">⏳ ждёт подтверждения</span>
        {% else %}✅ записан в Kafka{% endif %}
      </td>
    </tr>
    {% else %}
    <tr><td colspan="6">Пока ничего не отправлено — нажмите «Создать заказ».</td></tr>
    {% endfor %}
  </table>
</body>
</html>
"""


def make_order() -> dict:
    return {
        "order_id": "ORD-" + uuid.uuid4().hex[:6].upper(),
        "items": random.sample(CATALOG, k=random.randint(1, 4)),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": INSTANCE,
    }


def publish(order: dict) -> None:
    entry = {"order": order, "partition": None, "offset": None, "error": None}
    with lock:
        sent.appendleft(entry)

    # Kafka возвращает partition и offset в delivery-callback после записи.
    def on_delivery(err, msg):
        if err is not None:
            entry["error"] = str(err)
        else:
            entry["partition"] = msg.partition()
            entry["offset"] = msg.offset()

    # Ключ = order_id: все события одного заказа попадут в одну партицию.
    producer.produce(
        TOPIC,
        key=order["order_id"].encode(),
        value=json.dumps(order, ensure_ascii=False).encode(),
        on_delivery=on_delivery,
    )


@app.get("/")
def index():
    with lock:
        snapshot = list(sent)
    return render_template_string(PAGE, sent=snapshot, instance=INSTANCE,
                                  bootstrap=BOOTSTRAP, topic=TOPIC)


@app.post("/order")
def create_order():
    count = max(1, min(int(request.form.get("count", 1)), 100))
    for _ in range(count):
        publish(make_order())
    left = producer.flush(10)  # ждём подтверждения от брокера (до 10 с)
    if left:
        app.logger.warning("%d сообщений не подтверждены — Kafka недоступна?", left)
    return redirect(url_for("index"))


@app.get("/health")
def health():
    return {"status": "ok", "instance": INSTANCE}


if __name__ == "__main__":
    print(f"[user-service] {INSTANCE} -> {BOOTSTRAP}, topic={TOPIC}, port={PORT}", flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
