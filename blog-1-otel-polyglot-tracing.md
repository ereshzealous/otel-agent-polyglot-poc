# Zero-Code Distributed Tracing Across Four Languages — A Hands-On OpenTelemetry Proof of Concept

*A practical exploration of wiring Java, Python, Node.js, and Go microservices into a single distributed trace — flowing through Kafka, PostgreSQL, and Redis — with minimal instrumentation code.*

---

## The Observability Gap in Polyglot Architectures

Microservices deliver on their promise of independent deployability and team autonomy. What they quietly introduce, however, is a debugging problem that grows with every service added to the graph.

Consider a customer reporting a failed order. In a monolith, one stack trace tells the story. In a distributed system, the evidence is scattered across four log streams, four formats, and four runtimes:

```mermaid
flowchart LR
    subgraph "What you see in logs"
        L1["order-service: INFO Order created id=abc-123"]
        L2["inventory-service: INFO Reserving product=PROD-001 qty=2"]
        L3["notification-service: INFO Sending email for order ???"]
        L4["analytics-service: INFO Stored event type=ORDER_CREATED"]
    end

    L1 ~~~ L2 ~~~ L3 ~~~ L4

    style L1 fill:#e8f5e9,stroke:#388e3c
    style L2 fill:#e3f2fd,stroke:#1976d2
    style L3 fill:#fff3e0,stroke:#f57c00
    style L4 fill:#f3e5f5,stroke:#7b1fa2
```

Which order does the notification belong to? Did inventory even process it? Correlating timestamps across services is fragile at best and impossible at worst.

**OpenTelemetry (OTel) addresses this by assigning a unique trace ID to every request** and propagating it across service boundaries — whether the communication happens over HTTP, Kafka, gRPC, or any other protocol. Each operation within the request becomes a **span** with precise timing, attributes, and parent-child relationships.

The critical question for engineering teams adopting OTel: **how much code do you actually need to write?**

We built a proof of concept to answer that definitively. Four services, four languages, one trace. This article documents what we built, what worked automatically, and where we had to intervene.

---

## What We Built

**ShopFlow** is a simulated e-commerce backend with four microservices communicating through Apache Kafka events:

```mermaid
flowchart TB
    subgraph Client
        curl["curl / browser"]
    end

    subgraph Services
        order["Order Service<br/><i>Java 17 · Spring Boot 3</i><br/>:8080"]
        inventory["Inventory Service<br/><i>Python 3.12 · FastAPI</i><br/>:8081"]
        notification["Notification Service<br/><i>Node.js 20 · Express</i><br/>:8082"]
        analytics["Analytics Service<br/><i>Go 1.22 · net/http + sarama</i><br/>:8083"]
    end

    subgraph Messaging
        kafka["Apache Kafka<br/><i>Event bus</i>"]
    end

    subgraph "Data Stores"
        pg["PostgreSQL<br/><i>ordersdb · inventorydb · analyticsdb</i>"]
        redis["Redis<br/><i>Stock-level cache</i>"]
    end

    subgraph "Observability Stack"
        collector["OTel Collector<br/><i>:4317 gRPC · :4318 HTTP</i>"]
        jaeger["Jaeger<br/><i>:16686</i>"]
        prometheus["Prometheus<br/><i>:9090</i>"]
        grafana["Grafana<br/><i>:3001</i>"]
    end

    curl -->|"POST /api/orders"| order
    order -->|"order.created"| kafka
    kafka -->|consume| inventory
    kafka -->|consume| notification
    kafka -->|consume| analytics
    inventory -->|"inventory.reserved"| kafka
    notification -->|"notification.sent"| kafka
    notification -->|"PUT /status"| order

    order --> pg
    inventory --> pg
    inventory --> redis
    analytics --> pg

    order -.->|"traces + metrics"| collector
    inventory -.->|"traces + metrics"| collector
    notification -.->|"traces + metrics"| collector
    analytics -.->|"traces + metrics"| collector

    collector -->|OTLP| jaeger
    collector -->|"remote write"| prometheus
    prometheus --> grafana
    jaeger --> grafana
```

The supporting infrastructure — PostgreSQL (three databases), Redis, Kafka, OTel Collector, Jaeger, Prometheus, and Grafana — runs alongside the services via Docker Compose. Thirteen containers total.

---

## The Event Chain — What a Single Order Triggers

A single `POST /api/orders` initiates an asynchronous event chain that touches all four services:

```mermaid
sequenceDiagram
    participant C as Client
    participant O as Order Service<br/>(Java)
    participant K as Kafka
    participant I as Inventory Service<br/>(Python)
    participant N as Notification Service<br/>(Node.js)
    participant A as Analytics Service<br/>(Go)

    C->>O: POST /api/orders
    Note over O: Save order to PostgreSQL<br/>Status = PENDING

    O->>K: publish order.created
    Note over O,K: Java agent auto-injects<br/>W3C traceparent into<br/>Kafka headers

    par Consumed in parallel by three services
        K->>I: consume order.created
        Note over I: Extract traceparent<br/>from Kafka headers (manual)
        I->>I: SELECT ... FOR UPDATE<br/>(reserve stock)
        I->>I: Invalidate Redis cache
        I->>K: publish inventory.reserved
        Note over I,K: Re-inject traceparent<br/>for downstream consumers

        K->>N: consume order.created
        Note over N: kafkajs instrumentation<br/>extracts trace context
        N->>N: Send order confirmation<br/>(simulated)
        N->>O: PUT /api/orders/{id}/status<br/>→ CONFIRMED
        N->>K: publish notification.sent

        K->>A: consume order.created
        Note over A: Manual header extraction<br/>in Go code
        A->>A: INSERT INTO order_events
    end

    K->>N: consume inventory.reserved
    N->>N: Send inventory confirmation
    N->>O: PUT /api/orders/{id}/status<br/>→ INVENTORY_RESERVED
    N->>K: publish notification.sent

    K->>A: consume inventory.reserved
    K->>A: consume notification.sent
    K->>A: consume order.updated (x2)

    Note over C,A: All spans share ONE trace ID —<br/>visible as a single trace in Jaeger
```

The goal: every span in this flow should share the same trace ID, producing a single, navigable trace in Jaeger.

---

## The Four Instrumentation Approaches

Each language handles OpenTelemetry differently. The amount of instrumentation code ranges from literally zero to a full SDK setup. The following diagram summarizes the spectrum:

```mermaid
flowchart LR
    subgraph "Zero Code"
        java["Java<br/><b>JVM bytecode agent</b><br/>One flag: -javaagent"]
    end

    subgraph "Near-Zero Code"
        python["Python<br/><b>CLI wrapper</b><br/>opentelemetry-instrument"]
    end

    subgraph "Minimal Code"
        node["Node.js<br/><b>require hook</b><br/>~30 lines setup file"]
    end

    subgraph "Full SDK"
        go["Go<br/><b>Manual wiring</b><br/>~100 lines setup + wrappers"]
    end

    java --> python --> node --> go

    style java fill:#e8f5e9,stroke:#388e3c
    style python fill:#e3f2fd,stroke:#1976d2
    style node fill:#fff3e0,stroke:#f57c00
    style go fill:#fce4ec,stroke:#c62828
```

### 1. Java — The Gold Standard (Zero Code)

The OTel Java agent uses **JVM bytecode manipulation** to intercept library calls at the classloader level. It supports 200+ libraries out of the box. For this PoC, the entire setup was a single JVM flag in `docker-compose.yml`:

```yaml
JAVA_TOOL_OPTIONS: >-
  -javaagent:/otel/opentelemetry-javaagent.jar
  -Dotel.service.name=order-service
  -Dotel.exporter.otlp.endpoint=http://otel-collector:4317
```

**Lines of OTel code in the Java service: 0**

What the agent captures automatically:

```mermaid
flowchart TD
    req["Incoming HTTP request<br/><i>Spring MVC</i>"]
    db["JDBC queries<br/><i>PostgreSQL via HikariCP</i>"]
    kafka["Kafka send()<br/><i>Spring KafkaTemplate</i>"]
    tx["@Transactional<br/><i>Spring transaction boundaries</i>"]
    headers["W3C traceparent<br/><i>injected into Kafka headers</i>"]

    req --> db
    req --> kafka
    req --> tx
    kafka --> headers

    style req fill:#e8f5e9
    style db fill:#e8f5e9
    style kafka fill:#e8f5e9
    style tx fill:#e8f5e9
    style headers fill:#c8e6c9
```

The Kafka trace context propagation is the critical piece. When the order service publishes `order.created`, the agent automatically injects `traceparent` and `tracestate` headers into the Kafka message. Without this, downstream consumers would start isolated traces.

> **Screenshot placeholder**: *[Insert screenshot of the Dockerfile showing the javaagent download and the JAVA_TOOL_OPTIONS in docker-compose.yml]*

---

### 2. Python — Near-Zero Code, With Notable Gaps

Python uses `opentelemetry-instrument`, a CLI wrapper that monkey-patches libraries at import time. The Dockerfile change is minimal — wrapping the application command:

```dockerfile
CMD ["opentelemetry-instrument",
     "uvicorn", "app.main:app",
     "--host", "0.0.0.0", "--port", "8081"]
```

This auto-instruments FastAPI, SQLAlchemy, Redis, and httpx. However, we encountered a significant gap:

```mermaid
flowchart TD
    subgraph "Auto-Instrumented"
        fastapi["FastAPI endpoints"]
        sqla["SQLAlchemy queries"]
        redis_py["Redis operations"]
        httpx["httpx HTTP calls"]
    end

    subgraph "NOT Auto-Instrumented"
        aiokafka["aiokafka<br/><i>No OTel plugin exists</i>"]
    end

    aiokafka -.-|"Manual code<br/>required"| fix["extract_trace_ctx()<br/>build_trace_headers()"]

    style fastapi fill:#e8f5e9
    style sqla fill:#e8f5e9
    style redis_py fill:#e8f5e9
    style httpx fill:#e8f5e9
    style aiokafka fill:#ffcdd2
    style fix fill:#fff9c4
```

**`aiokafka` has no auto-instrumentation.** The OTel Python ecosystem covers `confluent-kafka` and `kafka-python`, but not the async client we were using. We had to write manual trace context extraction on the consumer side and injection on the producer side — approximately 15 lines of code per direction.

Without this manual intervention, the inventory service appeared as a completely isolated trace in Jaeger, disconnected from the order that triggered it.

> **Screenshot placeholder**: *[Insert screenshot showing the inventory-service trace in Jaeger BEFORE the fix (isolated trace) and AFTER (connected to order-service)]*

---

### 3. Node.js — One Setup File, One Dependency Gotcha

Node.js uses a `--require` hook to load a tracing setup file before the application code runs. This file initializes the OTel SDK and registers instrumentations:

```mermaid
flowchart TD
    subgraph "tracing.js (~30 lines)"
        sdk["NodeSDK initialization"]
        auto["getNodeAutoInstrumentations()"]
        kafkajs_inst["KafkaJsInstrumentation()"]
        exporter["OTLPTraceExporter"]
    end

    subgraph "What it enables"
        express["Express routes"]
        http["HTTP client calls"]
        pg["PostgreSQL queries"]
        kafkajs["kafkajs produce/consume"]
    end

    sdk --> auto
    sdk --> kafkajs_inst
    sdk --> exporter
    auto -.-> express
    auto -.-> http
    auto -.-> pg
    kafkajs_inst -.-> kafkajs

    style kafkajs_inst fill:#fff9c4
    style kafkajs fill:#fff9c4
```

The gotcha: **`@opentelemetry/auto-instrumentations-node` does not reliably include kafkajs instrumentation** in many versions. We had to add `@opentelemetry/instrumentation-kafkajs` as a separate dependency and register it explicitly. Without it, the notification service consumed Kafka messages correctly but created entirely isolated traces.

**Lines of OTel code: ~30** (the tracing.js setup file)

> **Screenshot placeholder**: *[Insert screenshot of the tracing.js file and the package.json showing the kafkajs instrumentation dependency]*

---

### 4. Go — Full Manual, Full Control

Go compiles to a static binary. There is no classloader to hook into, no monkey-patching, no module system to intercept. The OTel SDK must be wired up explicitly in `main()`:

```mermaid
flowchart TD
    main["main()"]
    setup["telemetry.Init()<br/><i>~60 lines setup</i>"]
    tp["TracerProvider"]
    mp["MeterProvider"]
    prop["W3C TextMapPropagator"]

    handler["otelhttp.NewHandler()<br/><i>HTTP server spans</i>"]
    consumer["Manual header extraction<br/><i>Kafka consumer spans</i>"]
    db_spans["Manual span creation<br/><i>Database operations</i>"]

    main --> setup
    setup --> tp
    setup --> mp
    setup --> prop

    tp --> handler
    tp --> consumer
    tp --> db_spans

    style setup fill:#fce4ec
    style handler fill:#fce4ec
    style consumer fill:#fce4ec
    style db_spans fill:#fce4ec
```

For Kafka consumption, we manually extract trace context from Sarama message headers. For HTTP, we use the `otelhttp` middleware wrapper. Every span is something the developer explicitly chose to create.

**Lines of OTel code: ~100** (setup.go + manual spans in consumer and handlers)

Go provides the most control over what gets traced, at the cost of the most boilerplate.

> **Screenshot placeholder**: *[Insert screenshot of the Go telemetry/setup.go and the consumer trace extraction code]*

---

### Instrumentation Effort Summary

```mermaid
flowchart TD
    subgraph "Java"
        j_code["OTel code: 0 lines"]
        j_how["JVM flag in docker-compose"]
        j_kafka["Kafka context: Automatic"]
    end

    subgraph "Python"
        p_code["OTel code: ~30 lines"]
        p_how["CLI wrapper in Dockerfile"]
        p_kafka["Kafka context: Manual<br/>(aiokafka gap)"]
    end

    subgraph "Node.js"
        n_code["OTel code: ~30 lines"]
        n_how["--require tracing.js"]
        n_kafka["Kafka context: Explicit pkg<br/>(kafkajs gap)"]
    end

    subgraph "Go"
        g_code["OTel code: ~100 lines"]
        g_how["SDK in main()"]
        g_kafka["Kafka context: Manual"]
    end

    style j_code fill:#e8f5e9
    style j_kafka fill:#e8f5e9
    style p_code fill:#e3f2fd
    style p_kafka fill:#ffcdd2
    style n_code fill:#fff3e0
    style n_kafka fill:#fff9c4
    style g_code fill:#fce4ec
    style g_kafka fill:#fce4ec
```

---

## The Moment of Truth — One Trace, Four Services

After starting the stack with `./start.sh`, we create a single order:

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

Opening Jaeger at `http://localhost:16686` and selecting `order-service` reveals the full distributed trace:

> **Screenshot placeholder**: *[Insert screenshot of the Jaeger trace list showing a trace with all 4 services]*

> **Screenshot placeholder**: *[Insert FULL screenshot of the Jaeger trace detail view showing the span timeline across all 4 services — this is the hero image of the article]*

### What Each Span Represents

```mermaid
flowchart TD
    subgraph "order-service (Java)"
        http["POST /api/orders"]
        insert["INSERT orders<br/><i>Hibernate → PostgreSQL</i>"]
        produce["order.created publish<br/><i>KafkaTemplate.send()</i>"]
    end

    subgraph "inventory-service (Python)"
        consume_i["kafka.consume order.created"]
        select_lock["SELECT ... FOR UPDATE<br/><i>Stock reservation</i>"]
        redis_ops["Redis GET / SETEX<br/><i>Cache invalidation</i>"]
        produce_i["inventory.reserved publish"]
    end

    subgraph "notification-service (Node.js)"
        consume_n["kafka.consume order.created"]
        send_notif["Send confirmation<br/><i>Simulated email</i>"]
        callback["PUT /api/orders/{id}/status<br/><i>REST callback</i>"]
    end

    subgraph "analytics-service (Go)"
        consume_a["kafka.consume order.created"]
        store["INSERT INTO order_events<br/><i>Event storage</i>"]
    end

    http --> insert
    http --> produce
    produce --> consume_i
    produce --> consume_n
    produce --> consume_a
    consume_i --> select_lock
    consume_i --> redis_ops
    consume_i --> produce_i
    consume_n --> send_notif
    consume_n --> callback
    consume_a --> store

    style http fill:#e8f5e9
    style insert fill:#e8f5e9
    style produce fill:#e8f5e9
    style consume_i fill:#e3f2fd
    style select_lock fill:#e3f2fd
    style redis_ops fill:#e3f2fd
    style produce_i fill:#e3f2fd
    style consume_n fill:#fff3e0
    style send_notif fill:#fff3e0
    style callback fill:#fff3e0
    style consume_a fill:#f3e5f5
    style store fill:#f3e5f5
```

All spans share **one trace ID**. One request, one trace, four services, full visibility.

### Verifying via CLI

```bash
curl -s "http://localhost:16686/api/traces?service=order-service&limit=1" | \
python3 -c "
import json, sys
data = json.load(sys.stdin)
trace = data['data'][0]
services = set()
for span in trace['spans']:
    pid = span['processID']
    svc = trace['processes'][pid]['serviceName']
    services.add(svc)
print(f'Trace ID: {trace[\"traceID\"]}')
print(f'Spans:    {len(trace[\"spans\"])}')
print(f'Services: {sorted(services)}')
"
```

Expected output:
```
Trace ID: 8defd3093da9ea72...
Spans:    40+
Services: ['analytics-service', 'inventory-service', 'notification-service', 'order-service']
```

> **Screenshot placeholder**: *[Insert screenshot of the terminal showing this CLI output]*

---

## How Trace Context Propagation Works Through Kafka

This is the mechanism that makes or breaks distributed tracing across message brokers. HTTP propagation is well understood — trace context travels as HTTP headers. Kafka does not have HTTP headers, but it does have **message headers** — key-value pairs attached to each record.

### The W3C Traceparent Header

OTel uses the [W3C Trace Context](https://www.w3.org/TR/trace-context/) standard. The `traceparent` header encodes four fields:

```mermaid
flowchart LR
    subgraph "traceparent header"
        ver["00<br/><i>version</i>"]
        tid["8defd309...f654<br/><i>trace ID (32 hex)</i>"]
        sid["98a4986c61df3575<br/><i>parent span ID (16 hex)</i>"]
        flags["01<br/><i>sampled</i>"]
    end

    ver --- tid --- sid --- flags

    style ver fill:#f5f5f5
    style tid fill:#e8f5e9
    style sid fill:#e3f2fd
    style flags fill:#fff3e0
```

Format: `00-{trace_id}-{parent_span_id}-{flags}`

### How It Flows Through Kafka

```mermaid
sequenceDiagram
    participant P as Producer<br/>(order-service)
    participant K as Kafka Broker
    participant C as Consumer<br/>(inventory-service)

    Note over P: Current span context:<br/>trace_id = 8defd309...<br/>span_id = 98a4986c

    P->>P: OTel agent intercepts<br/>kafkaTemplate.send()
    P->>P: Creates producer span<br/>span_id = 3d15191c<br/>parent = 98a4986c

    P->>K: Kafka message with headers:<br/>traceparent: 00-8defd309...-3d15191c-01<br/>tracestate: (empty)

    K->>C: Deliver message

    C->>C: Extract headers into carrier map
    C->>C: propagate.extract(carrier)<br/>→ recovers trace context

    C->>C: Start consumer span<br/>trace_id = 8defd309... (same)<br/>span_id = 02c29abd (new)<br/>parent = 3d15191c (producer)

    Note over C: All child spans<br/>(DB, Redis, HTTP calls)<br/>inherit this trace context
```

### The Key Insight

The Kafka message header carries the **trace ID** and the **producer's span ID**. When the consumer reads these headers, it does not create a new trace — it creates a new span under the existing trace, with the producer span as its parent.

```mermaid
flowchart TD
    subgraph "How each language handles Kafka trace context"
        java_in["Java<br/><b>Automatic</b><br/>Agent injects/extracts"]
        python_in["Python<br/><b>Manual</b><br/>propagate.inject() /<br/>propagate.extract()"]
        node_in["Node.js<br/><b>Explicit package</b><br/>KafkaJsInstrumentation<br/>handles it"]
        go_in["Go<br/><b>Manual</b><br/>propagator.Inject() /<br/>propagator.Extract()"]
    end

    style java_in fill:#e8f5e9
    style python_in fill:#ffcdd2
    style node_in fill:#fff9c4
    style go_in fill:#fce4ec
```

---

## What Auto-Instrumentation Covers (And What It Does Not)

This is where marketing meets reality. "Zero-code observability" applies only to libraries with a matching instrumentation plugin.

### What Gets Auto-Instrumented

```mermaid
flowchart TD
    subgraph "Well Covered (All Languages)"
        http_s["HTTP Servers<br/><i>Spring MVC, FastAPI, Express, net/http</i>"]
        http_c["HTTP Clients<br/><i>OkHttp, httpx, http/https, otelhttp</i>"]
        sql["SQL Databases<br/><i>JDBC, asyncpg, pg, otelsql</i>"]
    end

    subgraph "Mostly Covered"
        redis_auto["Redis<br/><i>Auto: Java, Python, Node</i><br/><i>Manual: Go</i>"]
        mongo["MongoDB<br/><i>Auto: Java, Python, Node</i><br/><i>Wrapper: Go</i>"]
        grpc["gRPC<br/><i>Auto: Java, Python, Node</i><br/><i>Wrapper: Go</i>"]
    end

    subgraph "Gaps and Gotchas"
        kafka_gap["Kafka<br/><i>Java: auto</i><br/><i>Python aiokafka: MANUAL</i><br/><i>Node kafkajs: explicit pkg</i><br/><i>Go: manual</i>"]
        neo4j_gap["Neo4j<br/><i>No auto-instrumentation<br/>in any language</i>"]
        click_gap["ClickHouse<br/><i>Auto only in Java</i><br/><i>Manual everywhere else</i>"]
        biz["Business Logic<br/><i>Never auto-instrumented</i><br/><i>Always manual spans</i>"]
    end

    style http_s fill:#e8f5e9
    style http_c fill:#e8f5e9
    style sql fill:#e8f5e9
    style redis_auto fill:#c8e6c9
    style mongo fill:#c8e6c9
    style grpc fill:#c8e6c9
    style kafka_gap fill:#ffcdd2
    style neo4j_gap fill:#ffcdd2
    style click_gap fill:#ffcdd2
    style biz fill:#ffcdd2
```

### Gaps We Encountered in This PoC

| Gap | Impact | Resolution |
|---|---|---|
| Python `aiokafka` not instrumented | Inventory service traces were isolated | Wrote manual `extract_trace_ctx()` and `build_trace_headers()` (~30 lines) |
| Node.js `kafkajs` not bundled | Notification service traces were isolated | Added `@opentelemetry/instrumentation-kafkajs` as explicit dependency |
| Go has no runtime agent | All spans required explicit creation | Wired OTel SDK in `main()`, used `otelhttp` wrapper, manual Kafka header extraction |

### Additional Caveats Worth Noting

| Caveat | Detail |
|---|---|
| **GraalVM Native Image** | Incompatible with the Java agent (bytecode manipulation vs. SubstrateVM). Use Spring Boot Starter or Quarkus OTel extension instead |
| **Gunicorn + Python** | `BatchSpanProcessor` background thread + `--preload` forking = deadlocks. Use `UvicornWorker` or initialize OTel in `post_fork` |
| **Version ceilings** | Python `confluent-kafka` support: 1.8.2–2.11.0 only. `SQLAlchemy`: 1.0.0–2.1.0. Outside these ranges, instrumentation silently skips |
| **Node.js `instrumentation-fs`** | Causes 3x memory spikes — disabled by default in `auto-instrumentations-node` |
| **Go `otelsarama`** | References deprecated `Shopify/sarama` import path (now `IBM/sarama`). The contrib package has not fully caught up |

---

## Performance Overhead — The Cost of Tracing

OTel agents are not free. They share your application's process, memory, and CPU.

### Startup Impact

```mermaid
flowchart LR
    subgraph "Startup Overhead by Language"
        java_s["Java<br/><b>+20–100%</b><br/><i>Bytecode transformation</i>"]
        python_s["Python<br/><b>+1–3s</b><br/><i>Monkey-patching imports</i>"]
        node_s["Node.js<br/><b>+0.5–5s</b><br/><i>require-in-the-middle</i>"]
        go_s["Go<br/><b>None</b><br/><i>Compiled into binary</i>"]
    end

    java_s ~~~ python_s ~~~ node_s ~~~ go_s

    style java_s fill:#ffcdd2
    style python_s fill:#fff9c4
    style node_s fill:#fff9c4
    style go_s fill:#e8f5e9
```

### Runtime Impact

| Metric | Typical Range | Notes |
|---|---|---|
| CPU | +2–5% | Can reach 20% at 100% sampling under heavy load |
| Memory | +50–200 MB (Java), +20–80 MB (others) | Span buffers, metric aggregations, exporter queues |
| Request latency | +0.5–2ms per request | Span creation, attribute recording, context propagation |
| Throughput | -1–5% | Community reports up to 33% drop at 100% sampling in high-QPS Java services |

**Production recommendation**: This PoC uses 100% sampling for maximum visibility. Production deployments should use **probabilistic sampling** (e.g., 10% of traces) or **tail-based sampling** (keep errors and slow requests, sample the rest) to control overhead.

---

## Metrics — The Other Half of Observability

Traces reveal individual request flows. Metrics reveal aggregate behavior over time. Each service exposes Prometheus-compatible endpoints:

```mermaid
flowchart LR
    subgraph "Custom Business Metrics"
        m1["shopflow.orders.created.total"]
        m2["inventory_reservations_total"]
        m3["notification_processing_duration"]
        m4["analytics_reports_generated_total"]
    end

    subgraph "Auto-Captured Metrics"
        m5["http_server_request_duration"]
        m6["jvm_memory_used_bytes"]
        m7["process_cpu_seconds_total"]
    end

    m1 --> prom["Prometheus<br/><i>:9090</i>"]
    m2 --> prom
    m3 --> prom
    m4 --> prom
    m5 --> prom
    m6 --> prom
    m7 --> prom
    prom --> grafana["Grafana<br/><i>:3001</i>"]

    style prom fill:#fff3e0
    style grafana fill:#e3f2fd
```

> **Screenshot placeholder**: *[Insert screenshot of Prometheus graph showing order creation rate or notification latency]*

> **Screenshot placeholder**: *[Insert screenshot of Grafana Explore page with Jaeger datasource showing a trace search]*

---

## Running It Yourself

### Prerequisites

- Docker and Docker Compose (v2+)
- ~4 GB of free RAM
- Ports: 3001, 4317, 4318, 5432, 6379, 8080–8083, 8090, 9090, 16686

### Start

```bash
git clone <repo-url>
cd otel-agent-polyglot-poc
chmod +x start.sh stop.sh
./start.sh
```

The script builds all four services, starts 13 containers, waits for health checks, and prints all URLs.

### Test

```bash
# Create an order — triggers the full event chain
curl -X POST http://localhost:8080/api/orders \
  -H "Content-Type: application/json" \
  -d '{"customerId":"CUST-001","productId":"PROD-001","quantity":2,"unitPrice":1299.99}'

# Open Jaeger to see the trace
open http://localhost:16686
```

### Clean Up

```bash
docker compose down -v
```

---

## Lessons Learned

### 1. Auto-instrumentation is a starting point, not a complete solution

It covers HTTP and popular databases out of the box. Async Kafka clients, graph databases, and custom protocols require manual intervention.

### 2. Kafka trace propagation is the hardest part

Getting spans for produce/consume operations is straightforward. Getting the **trace context** to flow through message headers so consumers join the same trace as the producer — that is where three out of four services required explicit work.

### 3. Go is the outlier

No agent, no monkey-patching, no runtime hooks. Every span is an explicit choice. The Go eBPF auto-instrumentation (released in beta in 2025) may change this, but it is not yet production-ready and is limited to a small set of libraries.

### 4. The Java agent is impressively comprehensive

Zero lines of OTel code. It handles Spring MVC, JDBC, Kafka (including trace context propagation through message headers), Redis, and transaction boundaries. The tradeoff is startup time — plan for 20–100% slower cold starts.

### 5. One trace across Kafka is powerful but dangerous at scale

In this PoC, one order produces ~40 spans across four services. In production, where a single event may trigger hundreds of downstream messages, trace fan-out can overwhelm your tracing backend. We explore this in detail in the [companion article on batch tracing](blog-2-kafka-batch-fanout-tracing.md).

---

## What's Next

In the [next article](blog-2-kafka-batch-fanout-tracing.md), we explore Kafka batch tracing in depth:
- What happens when one HTTP request sends N messages to Kafka?
- How are trace IDs, span IDs, and parent relationships assigned?
- The fan-out problem and strategies for controlling it in production

---

*The complete source code for this PoC is available at [GitHub repo link]. See TESTING.md for the full list of curl commands, PromQL queries, and troubleshooting steps.*

---

> **Screenshot placeholder summary** — Images to capture before publishing:
> 1. Dockerfile showing javaagent download + docker-compose.yml JAVA_TOOL_OPTIONS
> 2. Jaeger trace list showing a trace spanning all 4 services
> 3. Jaeger trace detail view (full span timeline) — **hero image**
> 4. Before/after: inventory-service trace isolated vs connected
> 5. Node.js tracing.js file and package.json kafkajs dependency
> 6. Go telemetry/setup.go and consumer trace extraction code
> 7. Terminal output of the CLI trace verification script
> 8. Prometheus graph (order creation rate or notification latency)
> 9. Grafana Explore page with Jaeger datasource
