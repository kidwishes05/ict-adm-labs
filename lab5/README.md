# Лабораторная 5 — ELK: Elasticsearch, Kibana, ILM и мониторинг

**Сценарий:** централизованные логи приложения. Single-node Elasticsearch + Kibana в Docker, 50 тестовых логов, поиск и агрегации, визуализация в Kibana, автоматическая ротация (ILM + rollover + alias) и мониторинг кластера через `elasticsearch_exporter` → Prometheus → Grafana.

**Версии:** Elasticsearch / Kibana 9.5.2, elasticsearch-exporter 1.11.0, Prometheus 3.13.4 (LTS), Grafana 13.2.4.

---

## Содержание

1. [Структура и запуск](#1-структура-и-запуск)
2. [Часть 1 — развёртывание и статус кластера](#2-часть-1--развёртывание-и-статус-кластера)
3. [Часть 2 — данные, индекс, поиск, агрегации, Kibana](#3-часть-2--данные-индекс-поиск-агрегации-kibana)
4. [Часть 3 — ILM + rollover](#4-часть-3--ilm--rollover)
5. [Часть 4 — мониторинг и алерты](#5-часть-4--мониторинг-и-алерты)
6. [Инциденты, найденные по ходу работы](#6-инциденты-найденные-по-ходу-работы)
7. [Отличия от продакшена](#7-отличия-от-продакшена)
8. [Скриншоты](#8-скриншоты)

---

## 1. Структура и запуск

```
lab5/
├── README.md
├── docker-compose.yml                  # ES, Kibana, exporter, Prometheus, Grafana
├── monitoring/
│   ├── prometheus.yml                  # scrape-конфиг
│   └── grafana/provisioning/datasources/prometheus.yml   # datasource как код
├── gen_logs.py                         # генератор тестовых логов (seed 42 → детерминированно)
├── logs.ndjson                         # 50 сгенерированных записей
└── screenshots/
```

```bash
docker compose up -d
docker compose ps                       # 5 контейнеров Up, es01 (healthy)
curl -s localhost:5601/api/status | jq '.status.overall.level'   # "available"
```

| Сервис | Адрес | Доступ |
|---|---|---|
| Elasticsearch | http://localhost:9200 | без авторизации (только для лабы) |
| Kibana | http://localhost:5601 | Dev Tools: ☰ → Management → Dev Tools |
| es-exporter | http://localhost:9114/metrics | — |
| Prometheus | http://localhost:9090 | Status → Target health |
| Grafana | http://localhost:3000 | admin / admin |

### Ключевые решения в `docker-compose.yml`

| Настройка | Зачем |
|---|---|
| `discovery.type=single-node` | Узел не ищет соседей и не ждёт выборов мастера; пропускаются production bootstrap checks |
| `xpack.security.enabled=false` | Без TLS и паролей для простоты лабы. **В проде недопустимо** |
| `ES_JAVA_OPTS=-Xms1g -Xmx1g` + `mem_limit: 2g` | Heap = 50% памяти контейнера: вторая половина нужна Lucene под файловый кэш ОС. Xms = Xmx, чтобы JVM не тратила время на изменение размера heap |
| `memlock: -1` | Не отдавать память ES в swap: swap делает паузы GC секундными |
| healthcheck ES (green/yellow) + `depends_on: service_healthy` у Kibana | Kibana стартует только после того, как ES готов |
| `depends_on: service_started` у exporter'а | **Не** healthy: мониторинг обязан работать именно тогда, когда кластер red |

---

## 2. Часть 1 — развёртывание и статус кластера

```
GET _cluster/health
```

Свежий кластер показал **green**: пользовательских индексов ещё нет, а служебные индексы ES/Kibana в основном создаются с `auto_expand_replicas: 0-1` и на одном узле получают 0 реплик. Типичная картина видна после создания обычного индекса:

```
PUT test-index
GET _cluster/health          # status: yellow, unassigned_shards: 1
GET _cat/shards/test-index?v # primary STARTED, replica UNASSIGNED
```

### Ответы на вопросы

**Какой статус?** `yellow`: `active_primary_shards: 1`, `active_shards: 1`, `unassigned_shards: 1`. В `_cat/shards` primary-шард в состоянии `STARTED`, реплика в `UNASSIGNED`.

**Почему на одном узле так?** У каждого нового индекса по умолчанию `number_of_replicas: 1`, а ES **никогда не размещает реплику на том же узле, что и её primary**: копия на том же узле не защищает от его отказа. Второго узла нет, поэтому реплика остаётся нераспределённой. Все primary назначены, реплика нет, значит yellow.

**Что нужно для green?** Чтобы были распределены все шарды, включая реплики. Способов два:

| Способ | Как | Где годится |
|---|---|---|
| Добавить узел | Второй узел в кластере, ES сам переносит на него реплику | **Прод.** Только так реплика даёт отказоустойчивость |
| Убрать реплики | `number_of_replicas: 0` | **Только dev / single-node.** Статус green, но копии данных нет: потеря узла = потеря данных |

```
PUT test-index/_settings
{ "index": { "number_of_replicas": 0 } }
GET _cluster/health          # green
DELETE test-index
```

Поэтому все индексы лабы создаются с `number_of_replicas: 0`. Это осознанное решение для single-node.

### Шум в логах Kibana

При старте Kibana много раз пишет `Error while trying to load prerelease flag from settings`. Это плагин **Fleet** пытается прочитать свою настройку (показывать ли beta-интеграции), которой ещё нет, и берёт значение по умолчанию `false`. На работу лабы не влияет. Готовность Kibana надёжнее проверять через status API, а не по логам. Переход `available (was degraded)` в логе — нормальный старт: пока плагины и подключение к ES инициализируются, общий статус `degraded`.

---

## 3. Часть 2 — данные, индекс, поиск, агрегации, Kibana

### 3.1. Тестовые логи

`gen_logs.py` генерирует 50 записей NDJSON (одна запись на строку, как пишут реальные приложения) за 19.10.2026 09:14–11:49 UTC:

- **уровни:** 30 INFO, 11 WARN, 9 ERROR;
- **6 сервисов:** api-gateway, auth, checkout, payment, inventory, notification. Ошибки чаще в payment и inventory;
- **4 сквозных трейса** с общим `trace_id` через несколько сервисов, два из них упавшие. Ключевой: `47294739614ff3d7`.

### 3.2. Индекс и mapping

```
PUT app-logs
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "timestamp": { "type": "date" },
      "level":     { "type": "keyword" },
      "service":   { "type": "keyword" },
      "message":   { "type": "text" },
      "trace_id":  { "type": "keyword" }
    }
  }
}
```

| Решение | Обоснование |
|---|---|
| Mapping задан до записи | Без него dynamic mapping сделает `level`/`service`/`trace_id` типа `text` + подполе `.keyword` (двойная индексация). Тип существующего поля меняется только через reindex |
| `number_of_shards: 1` | 50 документов. Несколько шардов дали бы только накладные расходы (oversharding). Ориентир в проде — шард 10–50 ГБ |
| `number_of_replicas: 0` | Single-node, см. часть 1 |
| `dynamic: strict` | Документ с неизвестным полем отклоняется, а не расширяет схему молча |
| `timestamp → date` | Фильтры по диапазону, гистограммы и выбор периода в Kibana |
| `level`, `service → keyword` | Точные фильтры и агрегации |
| `message → text` | Полнотекстовый поиск: анализатор разбивает текст на токены в нижнем регистре |
| `trace_id → keyword` | См. ниже |

**Почему `trace_id` — `keyword`, а не `text`?** Это идентификатор, его ценность в точном совпадении. Как `text` он прошёл бы через анализатор: UUID вида `4729-4739-614f` разбился бы по дефисам на три токена. Тогда поиск по одному trace_id находил бы логи чужих запросов с совпадающими кусками, по нему нельзя было бы агрегировать, а анализатор тратил бы ресурсы впустую.

**Почему `message` — `text`, а не `keyword`?** Это свободный текст, и смысл поля — поиск по словам. Как `keyword` поиск `timeout` вернул бы 0 результатов: документ находился бы только по точной строке целиком, вплоть до регистра.

### 3.3. Загрузка через bulk API

Bulk API требует перед каждым документом строку-действие:

```bash
awk '{print "{\"index\":{}}"; print}' logs.ndjson > bulk.ndjson     # 100 строк

curl -s -H 'Content-Type: application/x-ndjson' \
  -XPOST 'localhost:9200/app-logs/_bulk?refresh=true' \
  --data-binary @bulk.ndjson | head -c 300                          # "errors":false
```

- `--data-binary`, а не `-d`: `-d` удаляет переводы строк, на которых держится формат.
- `?refresh=true`: документы видны в поиске сразу, без ожидания ~1 с (near real-time).
- Проверка: `GET app-logs/_count` → 50, `GET _cat/indices/app-logs?v` → green, 16.2 kb.

### 3.4. Запросы

**Как ES видит текст:**

```
POST app-logs/_analyze
{ "field": "message", "text": "Failed to create order ord_86484: payment service unavailable" }
```
→ токены `failed`, `to`, `create`, `order`, `ord_86484`, `payment`, `service`, `unavailable`.

**Полнотекстовый поиск:**

```
GET app-logs/_search
{ "query": { "match": { "message": "payment" } } }
```
→ **8** документов. Среди них логи сервиса `checkout` («…payment service unavailable»), которые фильтр `service = payment` пропустил бы.

```
GET app-logs/_search
{ "query": { "match": { "message": { "query": "payment failed", "operator": "and" } } } }
```
→ **2** документа (без `operator: and` по умолчанию работает OR с ранжированием по BM25).

**Фильтр по уровню и времени:**

```
GET app-logs/_search
{
  "query": {
    "bool": {
      "filter": [
        { "term":  { "level": "ERROR" } },
        { "range": { "timestamp": { "gte": "2026-10-19T10:00:00Z", "lt": "2026-10-19T12:00:00Z" } } }
      ]
    }
  },
  "sort": [ { "timestamp": "asc" } ]
}
```
→ **7** ошибок.

**`filter` vs `must`.** Оба означают «документ обязан совпасть», состав результатов одинаковый. Но `must` считает релевантность (`_score`), а `filter` нет. К тому же результаты `filter` **кэшируются**. Уровень и время — условия «подходит / не подходит», релевантность для них бессмысленна, поэтому `filter` и быстрее, и правильнее. `must` нужен для полнотекстового `match`.

**Поиск по `trace_id`: восстановление инцидента:**

```
GET app-logs/_search
{ "query": { "term": { "trace_id": "47294739614ff3d7" } }, "sort": [ { "timestamp": "asc" } ] }
```
→ **6** записей за ~1,5 с:

1. `api-gateway` INFO — запрос на создание заказа
2. `auth` INFO — пользователь авторизован
3. `checkout` INFO — заказ создан
4. `payment` **ERROR** — `Card tokenization failed: provider returned 500` ← **первопричина**
5. `checkout` **ERROR** — не смог оформить заказ
6. `api-gateway` **ERROR** — клиент получил таймаут

Первая по времени ошибка в цепочке — причина, остальные — последствия. Без `trace_id` это три несвязанные ошибки в трёх сервисах.

### 3.5. Агрегации

```
GET app-logs/_search
{ "size": 0, "aggs": { "by_level": { "terms": { "field": "level" } } } }
```
→ INFO **30**, WARN **11**, ERROR **9**.

```
GET app-logs/_search
{
  "size": 0,
  "query": { "term": { "level": "ERROR" } },
  "aggs": { "errors_by_service": { "terms": { "field": "service" } } }
}
```
→ checkout 3, inventory 2, payment 2, api-gateway 1, notification 1.

**Почему нельзя агрегировать по `message`.** Запрос `terms` по `message` возвращает `Fielddata is disabled on text fields by default`. Агрегации нужна структура «документ → значение» (**doc_values**). Она хранится на диске по колонкам у `keyword`, дат и чисел, но не у `text`: у него есть только инвертированный индекс «термин → документы». `fielddata=true` заставил бы ES строить обратную структуру в **JVM heap** (риск OutOfMemory). Да и результат был бы бессмысленным: бакеты по отдельным словам, а не по сообщениям.

### 3.6. Kibana

1. **Data view:** Stack Management → Data Views → name/pattern `app-logs`, timestamp field `timestamp`.
2. **Discover:** период нужно сменить с `Last 15 minutes` на `Last 24 hours`, потому что логи в прошлом. Kibana показывает время в часовом поясе браузера (МСК = UTC+3). Примеры KQL: `level : "ERROR"`, `trace_id : "47294739614ff3d7"`, `message : payment and not service : "payment"`.
3. **Визуализация:** дашборд `Lab 5 — App logs` → Lens → поле `level` → Bar vertical → INFO 30 / WARN 11 / ERROR 9. Через **Inspect → Requests** видно, что Kibana отправляет в ES ту же `terms`-агрегацию. Kibana ничего не считает сама, вся нагрузка ложится на ES.

**Почему `Last 15 minutes` — правильное значение по умолчанию**, хотя на старых данных показывает пустоту: дежурному при инциденте нужны последние минуты, а узкое окно дёшево для кластера. При ротации индексов по времени ES вообще не трогает индексы вне периода. Запрос «за всё время» на терабайтах логов нагрузил бы все шарды и heap.

---

## 4. Часть 3 — ILM + rollover

### 4.1. Схема

| Компонент | Роль |
|---|---|
| ILM-политика | Когда делать rollover и когда удалять |
| Index template `app-logs-*` | Каждый новый индекс сразу получает mapping, настройки и политику |
| Alias `app-logs-write` | Стабильное имя для записи. ILM переключает его на новый индекс, приложение ничего не знает о ротации |
| Bootstrap-индекс `app-logs-000001` | Начало цепочки. Из суффикса `-000001` rollover вычисляет следующее имя |

Порядок: **политика → шаблон → bootstrap → запись**. Шаблон применяется только при создании индекса, а alias должен существовать до первой записи (см. [инцидент 1](#инцидент-1-автосоздание-индекса-вместо-alias)).

### 4.2. Конфигурация

**Ускорение ILM для демо** (по умолчанию проверка раз в 10 минут):

```
PUT _cluster/settings
{ "persistent": { "indices.lifecycle.poll_interval": "10s" } }
```

**Политика:**

```
PUT _ilm/policy/app-logs-policy
{
  "policy": {
    "phases": {
      "hot": {
        "actions": {
          "rollover": {
            "max_docs": 40,
            "max_primary_shard_size": "5mb",
            "max_age": "1d"
          }
        }
      },
      "delete": {
        "min_age": "1h",
        "actions": { "delete": {} }
      }
    }
  }
}
```

- Условия rollover связаны через **ИЛИ**. `max_docs: 40` — демо-порог, чтобы rollover сработал на нашем пакете из 50 документов. В проде режут по размеру шарда (30–50 ГБ) и возрасту.
- `delete.min_age: 1h` отсчитывается **от момента rollover**. В проде было бы `30d`.
- Фаз warm/cold нет: они переносят индексы на другие узлы, а узел один.

**Шаблон:**

```
PUT _index_template/app-logs-template
{
  "index_patterns": ["app-logs-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "index.lifecycle.name": "app-logs-policy",
      "index.lifecycle.rollover_alias": "app-logs-write"
    },
    "mappings": {
      "dynamic": "strict",
      "properties": {
        "timestamp": { "type": "date" },
        "level":     { "type": "keyword" },
        "service":   { "type": "keyword" },
        "message":   { "type": "text" },
        "trace_id":  { "type": "keyword" }
      }
    }
  }
}
```

**Bootstrap:**

```
PUT app-logs-000001
{ "aliases": { "app-logs-write": { "is_write_index": true } } }

GET _cat/aliases/app-logs-write?v     # проверить ДО записи
```

**Запись — только в alias:**

```bash
curl -s -H 'Content-Type: application/x-ndjson' \
  -XPOST 'localhost:9200/app-logs-write/_bulk?refresh=true' \
  --data-binary @bulk.ndjson | head -c 300        # "_index":"app-logs-000001"
```

### 4.3. Результат

```
GET _cat/indices/app-logs-*?v&s=index
GET _cat/aliases/app-logs-write?v
GET app-logs-*/_ilm/explain
```

| Индекс | docs | alias `is_write_index` | ILM |
|---|---|---|---|
| `app-logs-000001` | 50 | false | `hot` / `complete`, ждёт `delete` |
| `app-logs-000002` | 0 (создан автоматически) | **true** | `hot` / `rollover` / `check-rollover-ready` |

**Фаза delete тоже отработала:** через ~1 час после rollover `app-logs-000001` исчез из `_cat/indices`, а `000002` остался. Полный цикл ILM rollover → delete продемонстрирован.

В `_ilm/explain` для `000001` видно, что точка отсчёта возраста переставлена на rollover: `time_since_index_creation: 5.63m`, но `age: 4.13m`, `lifecycle_date_millis ≠ index_creation_date_millis`.

### 4.4. Ответы на вопросы

**Зачем писать в alias, а не в индекс?** Физическое имя индекса меняется при каждом rollover, а alias — нет. `app-logs-write` — стабильная точка входа, у которой ровно один индекс помечен `is_write_index: true`. Rollover — атомарная операция над метаданными кластера: флаг снимается с `-000001` и вешается на свежесозданный `-000002`. Приложение продолжает слать bulk в `app-logs-write`, ES сам маршрутизирует запись. С именем индекса в конфиге пришлось бы перенастраивать и перезапускать приложение при каждой ротации. На **чтение** alias указывает на все индексы цепочки: `GET app-logs-write/_count` суммирует их, и поиск видит всю историю.

**Почему в `000001` 50 документов при пороге 40?** `max_docs` — условие, а не жёсткий лимит. ILM не проверяет счётчик на каждом документе. Проверка выполняется на шаге `check-rollover-ready`, а ILM просыпается раз в `poll_interval`. К тому же все 50 документов пришли одним bulk-запросом за доли секунды, а порог проверяется между записями, не внутри пачки. В проде индекс с порогом 50 ГБ так же может вырасти до 51–52 ГБ, это нормально.

**Почему `min_age` фазы delete отсчитывается от rollover?** ILM ведёт индекс по фазам строго последовательно. В delete индекс попадает только после завершения hot, а hot завершается ровно в момент rollover: пока индекс — write-индекс, он остаётся в hot. Если бы отсчёт шёл от создания, политика «rollover через 7 дней, delete через 1 день» удаляла бы индексы, едва их успели закрыть, а сам смысл `min_age` как «сколько хранить после того, как перестали писать» терялся бы.

---

## 5. Часть 4 — мониторинг и алерты

### 5.1. Архитектура

```
Elasticsearch :9200 ←(REST JSON)← es-exporter :9114/metrics ←(scrape 15 с)← Prometheus :9090 ←(PromQL)← Grafana :3000
```

ES не отдаёт метрики в формате Prometheus, поэтому нужен **exporter**: при каждом scrape он опрашивает `_cluster/health`, `_nodes/stats`, `_stats` и переводит ответ в текстовый формат Prometheus.

**Почему отдельный стек, а не встроенный Stack Monitoring Kibana:** встроенный мониторинг хранит метрики в том же ES. При падении кластера или read-only по диску мониторинг ослепнет вместе с ним. Мониторинг должен жить отдельно от наблюдаемой системы.

### 5.2. Конфигурация

**Exporter** (`docker-compose.yml`):

```yaml
  es-exporter:
    image: quay.io/prometheuscommunity/elasticsearch-exporter:v1.11.0
    command:
      - --es.uri=http://elasticsearch:9200
      - --es.all              # метрики со всех узлов
      - --es.indices          # метрики по индексам
      - --collector.ilm       # статус ILM (с v1.10 вместо --es.ilm)
    depends_on:
      elasticsearch:
        condition: service_started
```

**Prometheus** (`monitoring/prometheus.yml`):

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
scrape_configs:
  - job_name: elasticsearch
    static_configs:
      - targets: ["es-exporter:9114"]
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```

Плюс `--storage.tsdb.retention.time=7d`: без ограничения диск Prometheus заполнится так же, как диск ES логами.

**Grafana:** datasource Prometheus подключается через provisioning (`monitoring/grafana/provisioning/datasources/prometheus.yml`). Конфигурация как код, при пересоздании контейнера ничего не нужно настраивать руками.

**Проверка цепочки слева направо:**

```bash
curl -s localhost:9114/metrics | grep elasticsearch_cluster_health_status   # exporter отдаёт метрики
```
Prometheus → Status → Target health: оба target'а **UP**. Grafana → Connections → Data sources → Prometheus → **Test**.

### 5.3. Дашборд `Elasticsearch — Lab 5`

Логика раскладки: «здоровье» и «нагрузка», чтобы дежурный одним взглядом видел и «кластер жив?», и «что он сейчас делает».

| # | Панель | PromQL | На какой вопрос отвечает |
|---|---|---|---|
| 1 | Статус кластера (0/1/2 = green/yellow/red) | `max(elasticsearch_cluster_health_status{color="yellow"}) + 2 * max(elasticsearch_cluster_health_status{color="red"})` | Кластер в порядке прямо сейчас? |
| 2 | Нераспределённые шарды | `elasticsearch_cluster_health_unassigned_shards` | Есть ли данные без доступа или реплики, которые некуда положить? |
| 3 | JVM heap, % | `100 * elasticsearch_jvm_memory_used_bytes{area="heap"} / ignoring(area) elasticsearch_jvm_memory_max_bytes{area="heap"}` | Не задыхается ли узел от нехватки памяти? |
| 4 | Заполненность диска данных, % | `100 * (1 - elasticsearch_filesystem_data_available_bytes / elasticsearch_filesystem_data_size_bytes)` | Не уйдёт ли кластер в read-only из-за диска? |
| 5 | Скорость индексации, док/с | `rate(elasticsearch_indices_indexing_index_total[5m])` | Доезжают ли логи до ES и с какой скоростью? |
| 6 | Скорость поиска, запросов/с | `rate(elasticsearch_indices_search_query_total[5m])` | Нагружает ли кто-то кластер поиском? |
| 7 | Отклонённые записи | `rate(elasticsearch_thread_pool_rejected_count{type="write"}[5m])` | Не начал ли ES отбрасывать записи, потому что не успевает? |
| 8 | Exporter жив | `up{job="elasticsearch"}` | Можно ли вообще верить остальному дашборду? |

Панели 5–7 нужны для **контекста**, а не для алертов. Когда срабатывает алерт по heap, дежурный сразу видит причину: всплеск индексации или поисковый шторм. Смысл дашборда — не просто «горит», а «горит, и вот почему».

`rate()` нужен, потому что `*_total` и `*_count` — **счётчики**: они только растут с момента старта ES. `rate()` превращает накопленное значение в скорость за последние 5 минут. `ignoring(area)` позволяет поделить used на max, даже если у них различается набор меток.

Нагрузка для живых графиков (пишем в `app-logs`, а не в `app-logs-write`, чтобы ILM не плодил индексы на каждые 40 документов):

```bash
for i in $(seq 1 60); do
  curl -s -H 'Content-Type: application/x-ndjson' -XPOST 'localhost:9200/app-logs/_bulk' \
    --data-binary @bulk.ndjson > /dev/null
  curl -s 'localhost:9200/app-logs/_search?q=message:payment' > /dev/null
  sleep 2
done
```

### 5.4. Три метрики для алертов

Из четырёх ключевых метрик лекции (статус, unassigned, диск, heap) статус и unassigned — по сути один симптом, поэтому выбран самый острый его вид. Три алерта покрывают три разные плоскости: **доступность данных** (A, наступает внезапно), **ёмкость хранилища** (B, развивается постепенно) и **ресурсы узла** (C, постепенно и каскадно).

#### Алерт A. Кластер red

```promql
elasticsearch_cluster_health_status{color="red"} == 1
```
`for: 1m`, severity: critical.

- **Что ловит:** узел упал, primary-шард не назначен, ES не может отдать часть данных. Внезапная авария: секунду назад green, сейчас red.
- **Чем грозит:** поиск по части индексов падает с ошибкой, дашборды и разбор инцидентов по логам «слепнут». Если primary не восстановится, данные шарда потеряны: на одном узле реплик нет. Для пользователей это «логи пропали» как раз тогда, когда они нужнее всего.
- **Порог:** `red == 1` в течение 1 минуты. Секундный флап при рестарте узла будить не должен, а минута стабильного red — уже авария.
- **Усиление:** ES 9 отдаёт в `_cluster/health` поле `unassigned_primary_shards`. Если exporter его экспортирует, условие стоит дополнить через `or ... > 0`. Это ровно «потерян доступ к данным», без шума от реплик.

#### Алерт B. Диск данных заполняется

```promql
100 * (1 - elasticsearch_filesystem_data_available_bytes / elasticsearch_filesystem_data_size_bytes) > 85
```
`for: 10m`. 85% — warning, 90% — critical.

- **Что ловит:** постепенное заполнение диска (часы, дни). Это единственный алерт, который ловит угрозу **остановки записи во все индексы**.
- **Пороги ES (disk watermarks):** 85% (low) — ES перестаёт размещать новые шарды на узле, новые индексы, в том числе при rollover, могут не создаться. 90% (high) — ES уводит шарды с узла. **95% (flood stage)** — индексы переводятся в `read_only_allow_delete`, все bulk-записи падают. Логи перестают доезжать тихо, если приложение не проверяет ответ bulk.
- **Чем грозит:** запись встаёт целиком, новые данные теряются, ILM не может сделать rollover. В ES ≥ 7.4 блок снимается **автоматически**, когда заполненность опускается ниже high watermark. Руками нужно освободить место. Классический ночной инцидент.
- **Порог:** 85% warning (= low watermark), 90% critical: до flood stage остаётся запас времени. `for: 10m`, чтобы короткий всплеск от merge или снапшота не поднимал тревогу.

#### Алерт C. JVM heap

```promql
100 * elasticsearch_jvm_memory_used_bytes{area="heap"}
  / ignoring(area) elasticsearch_jvm_memory_max_bytes{area="heap"} > 85
```
`for: 5m`. 85% — warning, 90% — critical.

- **Что ловит:** узел подходит к пределу heap. Развивается постепенно, но каскадно: меньше свободного heap → чаще и дольше GC → меньше CPU на работу → очереди растут → `rejected_count` растёт → таймауты → OutOfMemory → узел падает → кластер red (алерт A).
- **Чем грозит:** рост задержек поиска и индексации, затем отказ узла. На одном узле OOM = полная остановка логирования. Восстановление небыстрое: перезапуск ES и recovery шардов занимают минуты.
- **Порог:** 85% / 90%. `for: 5m` обязателен: heap пилообразный из-за GC (на дашборде видна «пила» 15–60%), и без удержания алерт флапал бы на каждой сборке мусора. По-настоящему опасен не высокий пик, а высокие **минимумы** пилы: heap, который не опускается после GC.

**Почему не rejected_count и не CPU.** Rejected — уже *следствие* переполнения heap или перегрузки, алерты C и A сработают раньше и укажут на причину. CPU на общей виртуалке шумит из-за соседей.

### 5.5. Слепые зоны мониторинга

Все три алерта построены на метриках `elasticsearch_*`. Если их нет, выражение возвращает пустой результат, и **Prometheus не считает это срабатыванием**: алерт молча неактивен. Мониторинг ослеп, но молчит. Нужны алерты на сам мониторинг:

| Ситуация | Что видит Prometheus | Алерт |
|---|---|---|
| Упал **exporter** | `up{job="elasticsearch"} == 0` | `up{job="elasticsearch"} == 0`, `for: 2m`, critical |
| Target пропал из конфигурации / Prometheus его не скрейпит | серии `up` нет | `absent(up{job="elasticsearch"})` |
| Упал **ES**, exporter жив | `up == 1`, но метрик кластера нет | `absent(elasticsearch_cluster_health_status{color="green"})` |
| Упал сам Prometheus / Alertmanager | ничего | **Watchdog (dead man's switch)**: алерт, который горит всегда. Тревогу поднимает внешняя система, если он **перестал** приходить |

Третья строка — главная ловушка: `up = 1` значит лишь, что Prometheus достучался до exporter'а, а не что данные о кластере полные. Это наблюдалось вживую: после перезагрузки компьютера несколько минут на панелях с «сырыми» значениями были разрывы при сплошной линии `up = 1`. ES после старта отвечал медленнее таймаута exporter'а (5 с), и тот отдавал неполный набор метрик. Графики с `rate()` разрывы скрывали, потому что сглаживают по 5-минутному окну. Если такое повторяется постоянно, лечится это увеличением `--es.timeout` у exporter'а с соблюдением порядка `es.timeout < scrape_timeout < scrape_interval` (например, 12s < 14s < 15s).

---

## 6. Инциденты, найденные по ходу работы

### Инцидент 1. Автосоздание индекса вместо alias

**Симптом:** `_cat/aliases/app-logs-write` пустой, в `_cat/indices` появился **индекс** `app-logs-write`, в `_ilm/explain` `step: ERROR`:

```
index.lifecycle.rollover_alias [app-logs-write] does not point to index [app-logs-write]
```

ILM повторял попытку на каждом цикле (`is_auto_retryable_error: true`, 11 попыток).

**Причина:** bulk ушёл в `app-logs-write` до bootstrap'а. Ни индекса, ни alias'а с таким именем не было, и ES **автоматически создал обычный индекс**. Имя подошло под `app-logs-*`, поэтому индекс получил шаблон и политику, а rollover не смог найти alias.

**Исправление:** `DELETE app-logs-write` → `PUT app-logs-000001` с alias'ом → проверка `_cat/aliases` → повторная запись.

**Вывод:** alias должен существовать **до первой записи**. Иначе ротация ломается молча, без ошибки при записи. В проде от этого защищает `action.auto_create_index` (запрет автосоздания для `app-logs-*`) или **data streams**, которые сами создают первый backing-индекс и делают эту ошибку невозможной.

### Инцидент 2. Кластер yellow из-за служебных индексов Kibana

**Симптом:** на дашборде статус = 1 (yellow) и unassigned = 2 больше часа, хотя все `app-logs*` созданы с `replicas: 0`.

**Диагностика:**

```
GET _cat/shards?v&h=index,shard,prirep,state,unassigned.reason&s=state:desc
```

```
.ds-.rule-events-2026.10.19-000001    0  r  UNASSIGNED  CLUSTER_RECOVERED
.ds-.alert-actions-2026.10.19-000001  0  r  UNASSIGNED  CLUSTER_RECOVERED
```

Это backing-индексы data streams Kibana (журналы правил и действий алертинга). Большинство служебных индексов создаются с `auto_expand_replicas: 0-1`, а у этих в шаблоне фиксированная `number_of_replicas: 1`. На одном узле реплику некуда положить, ситуация та же, что в части 1. `CLUSTER_RECOVERED` — причина, записанная после перезапуска.

**Исправление:**

```
PUT .ds-.rule-events-2026.10.19-000001,.ds-.alert-actions-2026.10.19-000001/_settings
{ "index": { "number_of_replicas": 0 } }
```

`_cluster/health` → green, 52/52 шарда. На дашборде unassigned 2 → 0 и статус 1 → 0.

**Ограничение:** исправлены текущие backing-индексы. После rollover этих data streams новые индексы снова получат 1 реплику. Постоянное решение в проде — второй узел.

**Вывод:** мониторинг сделал то, ради чего его строят: показал проблему, о которой никто не знал, причём в индексах, которые мы не создавали.

---

## 7. Отличия от продакшена

| В лабе | В проде |
|---|---|
| 1 узел, `replicas: 0` | ≥ 3 узла (кворум мастеров), `replicas ≥ 1` |
| `xpack.security.enabled=false`, пароль Grafana в compose | TLS, аутентификация, секреты вне репозитория |
| `poll_interval: 10s` | по умолчанию 10m |
| rollover по `max_docs: 40`, delete через 1h | rollover по размеру шарда 30–50 ГБ и возрасту, hot → warm → delete через 7/30 дней |
| alias + bootstrap | data streams |
| логи грузятся curl'ом | Filebeat / Elastic Agent / Logstash |
| нет бэкапов | snapshot / restore в S3: реплики не защищают от удаления |
| алерты описаны | правила Prometheus + Alertmanager с маршрутизацией и Watchdog |

**После сдачи** вернуть интервал ILM:

```
PUT _cluster/settings
{ "persistent": { "indices.lifecycle.poll_interval": null } }
```

Остановить стенд: `docker compose down` (данные в volumes сохранятся) или `docker compose down -v` (удалить всё).

---

## 8. Скриншоты

| Файл | Что показывает |
|---|---|
| `screenshots/01-cluster-health.png` | `GET _cluster/health`: yellow с `test-index` / green |
| `screenshots/02-mapping-count.png` | индекс `app-logs`: green, 50 документов |
| `screenshots/03-search-trace-id.png` | поиск по `trace_id 47294739614ff3d7`, 6 записей |
| `screenshots/04-aggregation.png` | агрегации `by_level` и `errors_by_service` |
| `screenshots/05-kibana-visualization.png` | Lens: столбцы по `level` |
| `screenshots/06-ilm-explain-error.png` | инцидент 1: ILM ERROR |
| `screenshots/07-indices-after-rollover.png` | `app-logs-000001` + `app-logs-000002`, alias переключён |
| `screenshots/08-ilm-delete.png` | `000001` удалён фазой delete |
| `screenshots/09-grafana-dashboard.png` | дашборд целиком |
| `screenshots/10-graph-heap.png` | график 1: JVM heap |
| `screenshots/11-graph-indexing.png` | график 2: скорость индексации |
| `screenshots/12-graph-search.png` | график 3: скорость поиска |
| `screenshots/13-yellow-to-green.png` | инцидент 2: unassigned 2 → 0, статус 1 → 0 |
