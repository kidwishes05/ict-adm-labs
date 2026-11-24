"""Генератор тестовых логов для Lab 5. Пишет logs.ndjson (одна запись на строку)."""
import json
import random
import uuid
from datetime import datetime, timedelta, timezone

random.seed(42)

START = datetime(2026, 10, 19, 9, 0, 0, tzinfo=timezone.utc)
WINDOW_SEC = 3 * 3600  # 09:00–12:00 UTC

MESSAGES = {
    "api-gateway": {
        "INFO": ["GET /api/v1/products 200 in {ms}ms", "POST /api/v1/orders 201 in {ms}ms",
                 "GET /api/v1/cart 200 in {ms}ms"],
        "WARN": ["Slow upstream response from checkout: {ms}ms", "Rate limit close to threshold for client 10.0.{a}.{b}"],
        "ERROR": ["Upstream payment returned 502 Bad Gateway", "Request timeout after 30000ms on /api/v1/orders"],
    },
    "auth": {
        "INFO": ["User user_{u} logged in successfully", "Access token refreshed for user_{u}"],
        "WARN": ["Failed login attempt for user_{u} from 10.0.{a}.{b}", "Token close to expiration for user_{u}"],
        "ERROR": ["Invalid JWT signature for user_{u}", "Connection to Redis session store refused"],
    },
    "checkout": {
        "INFO": ["Cart validated for order ord_{o}", "Order ord_{o} created, total {sum} RUB"],
        "WARN": ["Discount code expired for order ord_{o}", "Cart contains item with stale price, recalculating"],
        "ERROR": ["Failed to create order ord_{o}: payment service unavailable", "Database deadlock while saving order ord_{o}"],
    },
    "payment": {
        "INFO": ["Payment authorized for order ord_{o}, amount {sum} RUB", "Refund processed for order ord_{o}"],
        "WARN": ["Payment provider latency high: {ms}ms", "Retrying payment request for order ord_{o} (attempt 2)"],
        "ERROR": ["Payment declined for order ord_{o}: insufficient funds",
                  "Payment provider timeout for order ord_{o}",
                  "Card tokenization failed: provider returned 500"],
    },
    "inventory": {
        "INFO": ["Stock reserved for sku_{s}, qty {q}", "Stock level synced for warehouse SPB-1"],
        "WARN": ["Low stock for sku_{s}: {q} items left", "Warehouse sync took {ms}ms"],
        "ERROR": ["Out of stock for sku_{s}, reservation failed", "Failed to connect to warehouse API: connection reset"],
    },
    "notification": {
        "INFO": ["Email sent to user_{u}: order confirmation", "Push notification delivered to user_{u}"],
        "WARN": ["SMTP server slow to respond: {ms}ms"],
        "ERROR": ["Failed to send email to user_{u}: SMTP 550 mailbox unavailable"],
    },
}

# Вероятность ERROR выше у payment и inventory
LEVEL_WEIGHTS = {
    "api-gateway": (70, 20, 10), "auth": (70, 20, 10), "checkout": (60, 25, 15),
    "payment": (45, 25, 30), "inventory": (50, 25, 25), "notification": (75, 15, 10),
}


def render(tpl: str) -> str:
    return tpl.format(
        ms=random.randint(120, 4800), a=random.randint(0, 255), b=random.randint(1, 254),
        u=random.randint(1000, 9999), o=random.randint(10000, 99999),
        sum=random.randint(500, 45000), s=random.randint(100, 999), q=random.randint(1, 5),
    )


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def trace() -> str:
    return uuid.UUID(int=random.getrandbits(128)).hex[:16]


logs = []

# 1) Сквозные цепочки запросов: один trace_id через несколько сервисов
chains = [
    # (цепочка, упала ли в конце)
    (["api-gateway", "auth", "checkout", "inventory", "payment", "notification"], False),
    (["api-gateway", "auth", "checkout", "payment", "checkout", "api-gateway"], True),
    (["api-gateway", "auth", "checkout", "inventory"], True),
    (["api-gateway", "auth", "checkout", "payment", "notification"], False),
]
for chain, fails in chains:
    tid = trace()
    t = START + timedelta(seconds=random.randint(0, WINDOW_SEC - 60))
    for i, svc in enumerate(chain):
        t += timedelta(milliseconds=random.randint(15, 900))
        last_hops = i >= len(chain) - 3
        level = "ERROR" if fails and last_hops and svc != "auth" else "INFO"
        logs.append({"timestamp": ts(t), "level": level, "service": svc,
                     "message": render(random.choice(MESSAGES[svc][level])), "trace_id": tid})

# 2) Фоновые одиночные записи
while len(logs) < 50:
    svc = random.choice(list(MESSAGES))
    level = random.choices(["INFO", "WARN", "ERROR"], weights=LEVEL_WEIGHTS[svc])[0]
    t = START + timedelta(seconds=random.randint(0, WINDOW_SEC), milliseconds=random.randint(0, 999))
    logs.append({"timestamp": ts(t), "level": level, "service": svc,
                 "message": render(random.choice(MESSAGES[svc][level])), "trace_id": trace()})

logs.sort(key=lambda r: r["timestamp"])

with open("logs.ndjson", "w", encoding="utf-8") as f:
    for rec in logs:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"written {len(logs)} records")
