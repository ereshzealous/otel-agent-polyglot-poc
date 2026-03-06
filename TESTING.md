# ShopFlow — Testing & Observability Guide

## Quick Start

```bash
chmod +x *.sh
# One-click start: builds everything, starts infra + all 4 services
./start.sh

# Stop everything
./stop.sh
```

---

## Service Endpoints

| Service | URL | Tech |
|---|---|---|
| Order Service | http://localhost:8080 | Spring Boot (Java) |
| Inventory Service | http://localhost:8081 | FastAPI (Python) |
| Notification Service | http://localhost:8082 | Express (Node.js) |
| Analytics Service | http://localhost:8083 | Go |

## Observability Dashboards

| Dashboard | URL | Credentials |
|---|---|---|
| **Jaeger UI** | http://localhost:16686 | — |
| **Prometheus** | http://localhost:9090 | — |
| **Grafana** | http://localhost:3001 | admin / admin |
| **Kafka UI** | http://localhost:8090 | — |

---

## Test Commands

### 1. Create a Single Order

```bash
curl -X POST http://localhost:8080/api/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customerId": "CUST-001",
    "productId": "PROD-001",
    "quantity": 2,
    "unitPrice": 1299.99
  }'
```

This triggers the full flow:
1. Order Service saves to PostgreSQL, publishes `order.created` to Kafka
2. Inventory Service consumes event, reserves stock (Redis cache + PostgreSQL), publishes `inventory.reserved`
3. Notification Service consumes both events, sends notifications, calls Order Service REST to update status
4. Analytics Service consumes all events, stores in analytics DB

### 2. Fire Multiple Orders (Generate Trace Volume)

```bash
for i in {1..10}; do
  curl -s -X POST http://localhost:8080/api/orders \
    -H "Content-Type: application/json" \
    -d "{\"customerId\":\"CUST-00$i\",\"productId\":\"PROD-00$((i % 5 + 1))\",\"quantity\":$i,\"unitPrice\":49.99}" &
done
wait
echo "All orders submitted"
```

### 3. Check All Orders

```bash
curl http://localhost:8080/api/orders | python3 -m json.tool
```

### 4. Get a Specific Order

```bash
# Replace <order-id> with an actual UUID from the create response
curl http://localhost:8080/api/orders/<order-id>
```

### 5. Check Inventory / Products

```bash
# List all products with stock levels
curl http://localhost:8081/api/products | python3 -m json.tool

# Check specific product stock
curl http://localhost:8081/api/products/PROD-001/stock

# Get product detail
curl http://localhost:8081/api/products/PROD-001
```

### 6. Check Notification Service Status

```bash
curl http://localhost:8082/api/notifications/status
```

### 7. Check Analytics Reports (Generated Every 60s by Cron)

```bash
curl http://localhost:8083/api/reports | python3 -m json.tool
```

### 8. Check Event Summary

```bash
curl http://localhost:8083/api/events/summary
```

### 9. Kafka Batch Trace Test

This endpoint sends N random messages to `batch.test` topic in a single HTTP request.
The Python inventory-service consumes each message and routes it to a different handler
(stock check, price lookup, restock alert, audit log, cache warm, report generate).

The point: **one HTTP request → N producer spans → N consumer spans → all in one trace.**

#### Step 1 — Send a batch

```bash
# sends 5 messages with random action types
curl -s -X POST "http://localhost:8080/api/orders/batch-test?count=5" | python3 -m json.tool
```

You'll see the batch ID, action types, and product IDs assigned to each message.

#### Step 2 — Wait a few seconds, then inspect the trace in Jaeger

Open http://localhost:16686, select **order-service**, operation **POST /api/orders/batch-test**,
click **Find Traces**, and open the trace.

You should see:
```
POST /api/orders/batch-test                ← 1 root HTTP span
├── batch.test publish                     ← producer span for msg 0
│   └── kafka.consume batch.test           ← consumer span for msg 0
│       └── batch.audit_log / batch.stock_check / etc.
├── batch.test publish                     ← producer span for msg 1
│   └── kafka.consume batch.test
│       └── (action handler + DB/Redis/HTTP child spans)
├── batch.test publish                     ← producer span for msg 2
│   └── kafka.consume batch.test
│       └── ...
└── ...                                    ← one branch per message
```

#### What This Proves

| Concept | Verified |
|---|---|
| One HTTP request = one trace ID | All 29 spans share the same `trace_id` |
| Each `send()` = its own producer span | 5 separate producer spans, all children of the HTTP root |
| Each consumer = its own span | 5 consumer spans, each child of its **own** producer (not siblings) |
| span_id is always unique | 29 unique span IDs — never shared |
| traceparent propagated via Kafka headers | Consumers link back to their producer via W3C `traceparent` |
| Auto-instrumentation nests inside manual spans | SQL, Redis, HTTP client spans appear under action handlers |

#### Understanding the Span Hierarchy

```
trace_id = abc123 (same everywhere)

HTTP span
  span_id  = span_http               ← created by Java agent on POST
  parent   = none (root)

  ├── Producer span (msg 0)
  │     span_id  = span_produce_0    ← Java agent wraps kafkaTemplate.send()
  │     parent   = span_http
  │     │
  │     └── Consumer span (msg 0)
  │           span_id  = span_consume_0    ← Python reads traceparent from headers
  │           parent   = span_produce_0    ← NOT span_http, NOT shared
  │           │
  │           └── Action span
  │                 span_id = span_action_0
  │                 parent  = span_consume_0
  │
  ├── Producer span (msg 1)
  │     span_id  = span_produce_1    ← different span_id than msg 0
  │     parent   = span_http         ← same parent (the HTTP root)
  │     │
  │     └── Consumer span (msg 1)
  │           span_id  = span_consume_1
  │           parent   = span_produce_1    ← points to ITS OWN producer
  ...
```

Key insight: the Java agent creates **one producer span per send()**, not one wrapping span.
Each Kafka message header contains the trace_id + its producer's span_id as the parent.
So consumers are NOT siblings — each one is a child of a different producer span.

#### Action Types and What They Trace

Each message gets a random action. Different actions produce different span subtrees:

| Action | What it does | Child spans you'll see |
|---|---|---|
| `STOCK_CHECK` | Queries DB for stock level | Redis GET, SQL SELECT, Redis SETEX |
| `PRICE_LOOKUP` | Fetches product details from DB | SQL SELECT |
| `RESTOCK_ALERT` | Calls inventory's own HTTP `/stock` endpoint | HTTP GET (self-call), nested SQL + Redis |
| `AUDIT_LOG` | Simulates writing an audit record (sleep) | (just the manual span with a delay) |
| `CACHE_WARM` | Pre-loads stock into Redis cache | Redis GET, SQL SELECT, Redis SETEX |
| `REPORT_GENERATE` | Calls analytics-service `/api/reports` via HTTP | HTTP GET → analytics-service span |

#### Trace Fan-Out Warning (Production Consideration)

This test sends 5 messages. In production, if one HTTP request produces 1000+ Kafka messages,
the trace will have 2000+ spans (producers + consumers + handlers). This can overwhelm Jaeger
and the OTel collector.

Common mitigation strategies:
- **Probabilistic sampling**: only propagate trace context on a fraction of messages
- **Link instead of child**: consumer starts its own trace but adds a `LINK` to the producer trace
- **Break the chain**: stop injecting `traceparent` headers after a certain depth

---

## Health Checks

```bash
curl http://localhost:8080/api/orders/health
curl http://localhost:8081/health
curl http://localhost:8082/health
curl http://localhost:8083/health
curl http://localhost:13133  # OTel Collector
```

---

## Observability: What to Look For

### Jaeger (Traces) — http://localhost:16686

1. Select service **"order-service"** from the dropdown
2. Click **Find Traces**
3. Click on a trace to see the full distributed flow across all 4 services:
   - `POST /api/orders` (Spring Boot)
   - `kafka.produce order.created` (auto-instrumented)
   - `kafka.consume` in Inventory Service (Python)
   - Redis GET/SET operations
   - PostgreSQL queries (SELECT FOR UPDATE, UPDATE)
   - `kafka.produce inventory.reserved`
   - `kafka.consume` in Notification Service (Node.js)
   - `PUT /api/orders/{id}/status` REST call back to Order Service
   - `kafka.consume` in Analytics Service (Go)

### Prometheus (Metrics) — http://localhost:9090

Sample queries:

```promql
# Orders created (custom business metric)
shopflow_shopflow_orders_created_total

# HTTP request rate across all services
rate(http_server_request_duration_seconds_count[5m])

# Kafka consumer lag
kafka_consumer_fetch_manager_records_lag

# Inventory reservation count
shopflow_inventory_reservations_total

# Notification processing latency (p95)
histogram_quantile(0.95, rate(shopflow_notification_processing_duration_seconds_bucket[5m]))

# Analytics report generation count
shopflow_analytics_reports_generated_total

# JVM heap usage (order-service)
jvm_memory_used_bytes{area="heap"}
```

### Grafana — http://localhost:3001

1. Login with **admin / admin**
2. Datasources (Prometheus + Jaeger) are pre-provisioned
3. Go to Explore → select **Prometheus** → run PromQL queries
4. Go to Explore → select **Jaeger** → search traces by service

### Kafka UI — http://localhost:8090

- View topics: `order.created`, `inventory.reserved`, `inventory.failed`, `notification.sent`, `order.updated`, `batch.test`
- Check consumer group lag (including `inventory-batch-test-group`)
- Inspect message payloads and headers (look for `traceparent` headers to see W3C trace context)

---

## Metrics Endpoints (Direct Scrape)

```bash
# Spring Boot Actuator (order-service)
curl http://localhost:8080/actuator/prometheus

# FastAPI Prometheus (inventory-service)
curl http://localhost:8081/metrics

# Express prom-client (notification-service)
curl http://localhost:8082/metrics

# Go promhttp (analytics-service)
curl http://localhost:8083/metrics

# OTel Collector metrics
curl http://localhost:8889/metrics
```

---

## OTel Agent Strategy Summary

| Service | Language | OTel Approach | Code Changes |
|---|---|---|---|
| Order Service | Java (Spring Boot) | `-javaagent:opentelemetry-javaagent.jar` | ZERO |
| Inventory Service | Python (FastAPI) | `opentelemetry-instrument` CLI wrapper | ZERO |
| Notification Service | Node.js (Express) | `--require ./tracing.js` (30 lines) | Minimal |
| Analytics Service | Go | OTel SDK in `telemetry/setup.go` | SDK-level |

---

## Troubleshooting

```bash
# Check container status
docker compose ps

# View logs for a specific service
docker compose logs -f order-service
docker compose logs -f inventory-service
docker compose logs -f notification-service
docker compose logs -f analytics-service

# View infra logs
docker compose logs -f kafka
docker compose logs -f otel-collector
docker compose logs -f postgres

# Restart a single service
docker compose restart order-service

# Full rebuild
docker compose down && docker compose up --build -d
```
