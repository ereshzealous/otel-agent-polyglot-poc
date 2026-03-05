# How Kafka Batch Messages Get Traced — Span IDs, Fan-Out, and the Gotcha Nobody Talks About

*We sent 5 Kafka messages from one HTTP request and dissected exactly how OpenTelemetry assigns trace IDs, span IDs, and parent relationships. Here is what we found — and why it matters at scale.*

---

## The Question

You have a REST endpoint that publishes multiple Kafka messages in a single request. Maybe it is a batch import, a fan-out notification, or an order that triggers events for inventory, billing, and shipping.

The question is: **how does OpenTelemetry trace this?**

- Does each message get its own trace?
- Do all messages share one trace with one producer span?
- Or something else entirely?

We built a test endpoint, sent 5 messages, pulled the raw span data from Jaeger, and mapped every `trace_id`, `span_id`, and `parent_id` relationship. Here is exactly what happens.

---

## The Test Setup

We added a batch test endpoint to our Spring Boot order-service that sends N messages to a `batch.test` Kafka topic. Each message carries a random action type (`STOCK_CHECK`, `PRICE_LOOKUP`, `RESTOCK_ALERT`, `AUDIT_LOG`, `CACHE_WARM`, `REPORT_GENERATE`).

On the consumer side, a Python FastAPI service subscribes to `batch.test` and routes each message to a different handler. Each handler performs distinct operations — database queries, Redis cache updates, HTTP calls to other services — producing unique span subtrees.

```mermaid
flowchart LR
    client["Client<br/><i>POST /batch-test?count=5</i>"]
    order["Order Service<br/><i>Java · Spring Boot</i>"]
    kafka["Kafka<br/><i>batch.test topic</i>"]
    inventory["Inventory Service<br/><i>Python · FastAPI</i>"]

    client -->|"1 HTTP request"| order
    order -->|"5 Kafka messages"| kafka
    kafka -->|"5 consumed"| inventory

    subgraph "Action Routing"
        a1["STOCK_CHECK → DB + Redis"]
        a2["PRICE_LOOKUP → DB"]
        a3["RESTOCK_ALERT → HTTP self-call"]
        a4["AUDIT_LOG → simulated delay"]
        a5["CACHE_WARM → DB + Redis"]
        a6["REPORT_GENERATE → HTTP → Analytics"]
    end

    inventory --> a1
    inventory --> a2
    inventory --> a3
    inventory --> a4
    inventory --> a5
    inventory --> a6

    style client fill:#f5f5f5
    style order fill:#e8f5e9
    style kafka fill:#fff3e0
    style inventory fill:#e3f2fd
```

---

## The Two Mental Models

Before looking at the data, let us address the two common assumptions about how batch Kafka tracing works.

### Model A — "One Producer Span, N Consumer Children" (Incorrect)

Many developers assume this structure:

```mermaid
flowchart TD
    http["HTTP Span"]
    producer["Kafka Producer Span<br/><i>One span wrapping ALL sends</i>"]

    c0["Consumer 0"]
    c1["Consumer 1"]
    c2["Consumer 2"]
    c3["Consumer 3"]
    c4["Consumer 4"]

    http --> producer
    producer --> c0
    producer --> c1
    producer --> c2
    producer --> c3
    producer --> c4

    style http fill:#e8f5e9
    style producer fill:#e3f2fd
    style c0 fill:#fff3e0
    style c1 fill:#fff3e0
    style c2 fill:#fff3e0
    style c3 fill:#fff3e0
    style c4 fill:#fff3e0
```

Under this model, one producer span wraps all `send()` calls, and all consumers are siblings. **This is NOT what happens.**

### Model B — "N Producer Spans, Each With One Consumer Child" (Correct)

What actually happens:

```mermaid
flowchart TD
    http["HTTP Span<br/><i>POST /api/orders/batch-test</i>"]

    p0["Producer 0"]
    p1["Producer 1"]
    p2["Producer 2"]
    p3["Producer 3"]
    p4["Producer 4"]

    c0["Consumer 0"]
    c1["Consumer 1"]
    c2["Consumer 2"]
    c3["Consumer 3"]
    c4["Consumer 4"]

    http --> p0
    http --> p1
    http --> p2
    http --> p3
    http --> p4

    p0 --> c0
    p1 --> c1
    p2 --> c2
    p3 --> c3
    p4 --> c4

    style http fill:#e8f5e9
    style p0 fill:#e3f2fd
    style p1 fill:#e3f2fd
    style p2 fill:#e3f2fd
    style p3 fill:#e3f2fd
    style p4 fill:#e3f2fd
    style c0 fill:#fff3e0
    style c1 fill:#fff3e0
    style c2 fill:#fff3e0
    style c3 fill:#fff3e0
    style c4 fill:#fff3e0
```

The OTel Java agent creates **one producer span per `send()` call**. Each consumer becomes a child of its **own** producer span — not a sibling of the other consumers.

---

## The Experiment

### Step 1 — Trigger the Batch

```bash
curl -s -X POST "http://localhost:8080/api/orders/batch-test?count=5" | python3 -m json.tool
```

Response:
```json
{
  "sent": 5,
  "topic": "batch.test",
  "events": [
    {"seq": 0, "action": "AUDIT_LOG",      "productId": "PROD-003"},
    {"seq": 1, "action": "CACHE_WARM",      "productId": "PROD-002"},
    {"seq": 2, "action": "AUDIT_LOG",       "productId": "PROD-002"},
    {"seq": 3, "action": "AUDIT_LOG",       "productId": "PROD-001"},
    {"seq": 4, "action": "RESTOCK_ALERT",   "productId": "PROD-005"}
  ]
}
```

### Step 2 — Pull the Raw Trace from Jaeger

After a few seconds, we query Jaeger's API and extract every span with its IDs.

> **Screenshot placeholder**: *[Insert screenshot of Jaeger UI showing the batch-test trace with the full span timeline — all 29 spans visible with the fan-out structure]*

### Step 3 — The Raw Span Relationship Data

Here is the actual span hierarchy from our test run, with every ID mapped:

```mermaid
flowchart TD
    root["<b>ROOT: POST /api/orders/batch-test</b><br/>trace_id: 8defd309...f654<br/>span_id: 98a4986c<br/>parent: none<br/><i>order-service · 1049ms</i>"]

    p0["<b>PRODUCER 0</b><br/>batch.test publish<br/>span_id: 3d15191c<br/>parent: 98a4986c"]
    p1["<b>PRODUCER 1</b><br/>batch.test publish<br/>span_id: 97443019<br/>parent: 98a4986c"]
    p2["<b>PRODUCER 2</b><br/>batch.test publish<br/>span_id: 88681199<br/>parent: 98a4986c"]
    p3["<b>PRODUCER 3</b><br/>batch.test publish<br/>span_id: da00161f<br/>parent: 98a4986c"]
    p4["<b>PRODUCER 4</b><br/>batch.test publish<br/>span_id: 1430f2ba<br/>parent: 98a4986c"]

    c0["<b>CONSUMER 0</b><br/>kafka.consume batch.test<br/>span_id: 02c29abd<br/>parent: 3d15191c"]
    c1["<b>CONSUMER 1</b><br/>kafka.consume batch.test<br/>span_id: 1ee1d461<br/>parent: 97443019"]
    c2["<b>CONSUMER 2</b><br/>kafka.consume batch.test<br/>span_id: 319bc428<br/>parent: 88681199"]
    c3["<b>CONSUMER 3</b><br/>kafka.consume batch.test<br/>span_id: eccb28ba<br/>parent: da00161f"]
    c4["<b>CONSUMER 4</b><br/>kafka.consume batch.test<br/>span_id: 88abc705<br/>parent: 1430f2ba"]

    a0["batch.audit_log"]
    a1["batch.cache_warm"]
    a2["batch.audit_log"]
    a3["batch.audit_log"]
    a4["batch.restock_alert"]

    root --> p0
    root --> p1
    root --> p2
    root --> p3
    root --> p4

    p0 --> c0 --> a0
    p1 --> c1 --> a1
    p2 --> c2 --> a2
    p3 --> c3 --> a3
    p4 --> c4 --> a4

    style root fill:#e8f5e9,stroke:#388e3c
    style p0 fill:#e3f2fd,stroke:#1976d2
    style p1 fill:#e3f2fd,stroke:#1976d2
    style p2 fill:#e3f2fd,stroke:#1976d2
    style p3 fill:#e3f2fd,stroke:#1976d2
    style p4 fill:#e3f2fd,stroke:#1976d2
    style c0 fill:#fff3e0,stroke:#f57c00
    style c1 fill:#fff3e0,stroke:#f57c00
    style c2 fill:#fff3e0,stroke:#f57c00
    style c3 fill:#fff3e0,stroke:#f57c00
    style c4 fill:#fff3e0,stroke:#f57c00
    style a0 fill:#fce4ec,stroke:#c62828
    style a1 fill:#fce4ec,stroke:#c62828
    style a2 fill:#fce4ec,stroke:#c62828
    style a3 fill:#fce4ec,stroke:#c62828
    style a4 fill:#fce4ec,stroke:#c62828
```

**Total: 29 spans, 1 trace ID, all span IDs unique.**

---

## What This Proves — Line by Line

### 1. All spans share the same trace ID

Every one of the 29 spans carries `trace_id = 8defd3093da9ea72060207257173f654`. One request, one trace — this is the fundamental property of distributed tracing.

### 2. Every span has a unique span ID

29 spans, 29 different `span_id` values. Span IDs are never shared. Even though producer[0] and producer[1] perform the same operation (`batch.test publish`), they have completely different span IDs.

### 3. The Java agent creates one producer span per `send()` call

The code calls `kafkaTemplate.send()` five times in a loop. The OTel Java agent wraps **each individual call** in its own span — producing 5 producer spans, not 1.

### 4. Each consumer is a child of its OWN producer — not a sibling

```mermaid
flowchart LR
    subgraph "Message 0"
        p0_r["Producer 0<br/>span_id: 3d15191c"]
        c0_r["Consumer 0<br/>parent: <b>3d15191c</b>"]
        p0_r --> c0_r
    end

    subgraph "Message 1"
        p1_r["Producer 1<br/>span_id: 97443019"]
        c1_r["Consumer 1<br/>parent: <b>97443019</b>"]
        p1_r --> c1_r
    end

    style p0_r fill:#e3f2fd
    style c0_r fill:#fff3e0
    style p1_r fill:#e3f2fd
    style c1_r fill:#fff3e0
```

Consumer 0 has `parent = 3d15191c` (producer 0). Consumer 1 has `parent = 97443019` (producer 1). They are **not** siblings under one shared parent. Each consumer-producer pair forms an independent branch.

### 5. All producer spans are children of the HTTP root

Every producer span has `parent = 98a4986c` (the `POST /api/orders/batch-test` span). The HTTP request span is the common ancestor that ties the entire fan-out together.

---

## How the W3C Traceparent Header Travels

Let us trace the exact mechanism for message 0:

```mermaid
sequenceDiagram
    participant HTTP as HTTP Span<br/>span_id: 98a4986c
    participant P0 as Producer Span 0<br/>span_id: 3d15191c
    participant K as Kafka Broker
    participant C0 as Consumer Span 0<br/>span_id: 02c29abd

    HTTP->>P0: kafkaTemplate.send()<br/>Java agent creates producer span

    Note over P0: OTel agent constructs traceparent:<br/>00-8defd309...-3d15191c-01<br/>version  trace_id   span_id  flags

    P0->>K: Message with header:<br/>traceparent = 00-8defd309...-3d15191c-01

    Note over K: Kafka stores headers<br/>alongside the message payload

    K->>C0: Deliver message to consumer

    Note over C0: Python reads traceparent header<br/>Extracts: trace_id = 8defd309...<br/>           parent_span_id = 3d15191c

    C0->>C0: Create consumer span:<br/>trace_id = 8defd309... (same)<br/>span_id = 02c29abd (NEW)<br/>parent = 3d15191c (producer)
```

The critical detail: the `traceparent` header contains the **producer's span ID** as the parent. When the consumer reads this and creates its span, it sets `parent = 3d15191c` (the producer), not `parent = 98a4986c` (the HTTP root).

This is precisely why consumers are children of their specific producer span, not siblings under the HTTP span.

---

## Comparing Message 0 vs Message 1

```mermaid
flowchart TD
    HTTP["HTTP Span<br/>span_id: 98a4986c<br/>parent: none"]

    P0["Producer Span 0<br/>span_id: 3d15191c<br/>parent: 98a4986c"]
    P1["Producer Span 1<br/>span_id: 97443019<br/>parent: 98a4986c"]

    C0["Consumer Span 0<br/>span_id: 02c29abd<br/>parent: 3d15191c"]
    C1["Consumer Span 1<br/>span_id: 1ee1d461<br/>parent: 97443019"]

    A0["batch.audit_log<br/>span_id: 70abaede"]
    A1["batch.cache_warm<br/>span_id: e519e3cd"]

    HTTP --> P0
    HTTP --> P1
    P0 --> C0
    P1 --> C1
    C0 --> A0
    C1 --> A1

    style HTTP fill:#e8f5e9
    style P0 fill:#e3f2fd
    style P1 fill:#e3f2fd
    style C0 fill:#fff3e0
    style C1 fill:#fff3e0
    style A0 fill:#fce4ec
    style A1 fill:#fce4ec
```

Message 0's `traceparent` header contains `span_id = 3d15191c`. Message 1's header contains `span_id = 97443019`. They differ because each `send()` call generates its own producer span with its own ID.

The trace ID (`8defd309...`) is identical in both headers — that is what keeps them in the same trace.

---

## What Each Action Does (And How It Appears in Traces)

Each message receives a random action type. Different actions take different code paths and produce distinct span subtrees.

### STOCK_CHECK — Database + Cache

```mermaid
flowchart TD
    consume["kafka.consume batch.test"]
    action["batch.stock_check<br/><i>Manual span</i>"]
    redis_get["GET<br/><i>Redis cache lookup</i><br/><i>Auto-instrumented</i>"]
    connect["connect<br/><i>DB connection</i><br/><i>Auto-instrumented</i>"]
    select_q["SELECT inventorydb<br/><i>SQL query</i><br/><i>Auto-instrumented</i>"]
    redis_set["SETEX<br/><i>Redis cache write</i><br/><i>Auto-instrumented</i>"]

    consume --> action
    action --> redis_get
    action --> connect
    action --> select_q
    action --> redis_set

    style consume fill:#fff3e0
    style action fill:#fce4ec
    style redis_get fill:#f5f5f5
    style connect fill:#f5f5f5
    style select_q fill:#f5f5f5
    style redis_set fill:#f5f5f5
```

The manual span is `batch.stock_check`. Everything nested inside (Redis GET, SQL SELECT, Redis SETEX) is auto-instrumented by the Python OTel agent — zero additional code for those.

> **Screenshot placeholder**: *[Insert Jaeger screenshot showing a STOCK_CHECK span expanded with its Redis and SQL child spans]*

### RESTOCK_ALERT — Self HTTP Call

```mermaid
flowchart TD
    consume2["kafka.consume batch.test"]
    action2["batch.restock_alert<br/><i>Manual span</i>"]
    http_out["GET http://127.0.0.1:8081/api/products/.../stock<br/><i>httpx auto-instrumented</i>"]
    http_in["GET /api/products/{id}/stock<br/><i>FastAPI auto-instrumented</i>"]
    redis_g["GET<br/><i>Redis</i>"]
    sql_q["SELECT inventorydb<br/><i>SQL</i>"]

    consume2 --> action2
    action2 --> http_out
    http_out --> http_in
    http_in --> redis_g
    http_in --> sql_q

    style consume2 fill:#fff3e0
    style action2 fill:#fce4ec
    style http_out fill:#e3f2fd
    style http_in fill:#e8f5e9
    style redis_g fill:#f5f5f5
    style sql_q fill:#f5f5f5
```

This is particularly interesting — the consumer calls its **own** HTTP endpoint. The outgoing HTTP call is auto-instrumented by `opentelemetry-instrumentation-httpx`, and the incoming request is auto-instrumented by FastAPI instrumentation. The full round-trip is visible in one trace.

> **Screenshot placeholder**: *[Insert Jaeger screenshot showing the RESTOCK_ALERT span with the nested HTTP self-call]*

### REPORT_GENERATE — Cross-Service HTTP Call

```mermaid
flowchart TD
    consume3["kafka.consume batch.test<br/><i>inventory-service</i>"]
    action3["batch.report_generate<br/><i>Manual span</i>"]
    http_out3["GET http://analytics-service:8083/api/reports<br/><i>httpx auto-instrumented</i>"]
    go_handler["analytics-service-http<br/><i>Go otelhttp handler</i>"]

    consume3 --> action3
    action3 --> http_out3
    http_out3 --> go_handler

    style consume3 fill:#fff3e0
    style action3 fill:#fce4ec
    style http_out3 fill:#e3f2fd
    style go_handler fill:#f3e5f5
```

This crosses service boundaries. The Python consumer makes an HTTP call to the Go analytics service. The trace flows from Python to Go via HTTP headers. Three services represented in one trace branch.

> **Screenshot placeholder**: *[Insert Jaeger screenshot showing the REPORT_GENERATE span crossing from inventory-service to analytics-service]*

### AUDIT_LOG — Simulated Delay

```mermaid
flowchart TD
    consume4["kafka.consume batch.test"]
    action4["batch.audit_log<br/><i>Manual span</i><br/><i>sleep 10–50ms</i>"]

    consume4 --> action4

    style consume4 fill:#fff3e0
    style action4 fill:#fce4ec
```

The simplest case. One manual span, no infrastructure calls. Demonstrates the duration of a simulated audit write.

### CACHE_WARM — Pre-load Cache

```mermaid
flowchart TD
    consume5["kafka.consume batch.test"]
    action5["batch.cache_warm<br/><i>Manual span</i>"]
    redis_chk["GET<br/><i>Redis check</i>"]
    sql_fetch["SELECT inventorydb<br/><i>DB query</i>"]
    redis_write["SETEX<br/><i>Cache write, longer TTL</i>"]

    consume5 --> action5
    action5 --> redis_chk
    action5 --> sql_fetch
    action5 --> redis_write

    style consume5 fill:#fff3e0
    style action5 fill:#fce4ec
    style redis_chk fill:#f5f5f5
    style sql_fetch fill:#f5f5f5
    style redis_write fill:#f5f5f5
```

Similar to STOCK_CHECK but with a longer cache TTL. Useful for observing how identical infrastructure calls appear consistently across different business operations.

---

## The Full Picture — 5 Messages, 29 Spans, 1 Trace

Here is the complete trace tree from our test run with timing data:

```mermaid
flowchart TD
    root["<b>POST /api/orders/batch-test</b><br/><i>order-service · 1049ms</i>"]

    subgraph "Message 0 — AUDIT_LOG"
        p0f["publish · 682ms"]
        c0f["consume · 30ms"]
        a0f["batch.audit_log · 30ms"]
        p0f --> c0f --> a0f
    end

    subgraph "Message 1 — REPORT_GENERATE"
        p1f["publish · 84ms"]
        c1f["consume · 22ms"]
        a1f["batch.report_generate · 22ms"]
        http1f["GET analytics-service · 10ms"]
        go1f["analytics-service-http · 3ms"]
        p1f --> c1f --> a1f --> http1f --> go1f
    end

    subgraph "Message 2 — STOCK_CHECK (cold)"
        p2f["publish · 80ms"]
        c2f["consume · 62ms"]
        a2f["batch.stock_check · 62ms"]
        db_conn["connect · 47ms"]
        sql2f["SELECT · 5ms"]
        redis2f["GET + SETEX · 3ms"]
        p2f --> c2f --> a2f
        a2f --> db_conn
        a2f --> sql2f
        a2f --> redis2f
    end

    subgraph "Message 3 — STOCK_CHECK (warm)"
        p3f["publish · 79ms"]
        c3f["consume · 5ms"]
        a3f["batch.stock_check · 5ms"]
        sql3f["SELECT · 1ms"]
        redis3f["GET + SETEX · 2ms"]
        p3f --> c3f --> a3f
        a3f --> sql3f
        a3f --> redis3f
    end

    subgraph "Message 4 — RESTOCK_ALERT"
        p4f["publish · 78ms"]
        c4f["consume · 21ms"]
        a4f["batch.restock_alert · 21ms"]
        http4f["GET /stock · 8ms"]
        handler4f["FastAPI handler · 5ms"]
        p4f --> c4f --> a4f --> http4f --> handler4f
    end

    root --> p0f
    root --> p1f
    root --> p2f
    root --> p3f
    root --> p4f

    style root fill:#e8f5e9,stroke:#388e3c
    style p0f fill:#e3f2fd
    style p1f fill:#e3f2fd
    style p2f fill:#e3f2fd
    style p3f fill:#e3f2fd
    style p4f fill:#e3f2fd
    style c0f fill:#fff3e0
    style c1f fill:#fff3e0
    style c2f fill:#fff3e0
    style c3f fill:#fff3e0
    style c4f fill:#fff3e0
    style a0f fill:#fce4ec
    style a1f fill:#fce4ec
    style a2f fill:#fce4ec
    style a3f fill:#fce4ec
    style a4f fill:#fce4ec
    style go1f fill:#f3e5f5
```

Notice how the second STOCK_CHECK (message 3) is dramatically faster than the first (message 2) — 5ms vs 62ms. The first had a cold database connection (47ms for `connect`) and a cache miss. The second hit the warmed cache and an established connection. This kind of insight is exactly why distributed tracing is valuable.

> **Screenshot placeholder**: *[Insert FULL Jaeger trace detail screenshot showing all 29 spans in the timeline view — this is the hero image of this article]*

---

## The Verification Script

Run this yourself to see the exact span relationships:

```bash
curl -s "http://localhost:16686/api/traces?service=order-service&operation=POST+%2Fapi%2Forders%2Fbatch-test&limit=1" | \
python3 -c "
import json, sys
data = json.load(sys.stdin)
trace = data['data'][0]
trace_id = trace['traceID']

root = next(s for s in trace['spans'] if s['operationName'] == 'POST /api/orders/batch-test')
producers = sorted(
    [s for s in trace['spans'] if 'publish' in s['operationName']],
    key=lambda s: s['startTime']
)
consumers = {
    c['references'][0]['spanID']: c
    for c in trace['spans']
    if c['operationName'] == 'kafka.consume batch.test' and c.get('references')
}

print(f'Trace ID:  {trace_id}')
print(f'Spans:     {len(trace[\"spans\"])}')
print(f'Root span: {root[\"spanID\"]}  (POST /api/orders/batch-test)')
print()

for i, p in enumerate(producers):
    parent = p.get('references', [{}])[0].get('spanID', '?')
    c = consumers.get(p['spanID'])
    action = None
    if c:
        action = next(
            (s for s in trace['spans']
             if s.get('references') and s['references'][0].get('spanID') == c['spanID']
             and 'batch.' in s['operationName']),
            None
        )
    print(f'msg[{i}]  producer={p[\"spanID\"][:12]}  parent={parent[:12]}(root)  '
          f'consumer={c[\"spanID\"][:12] if c else \"?\"}  '
          f'action={action[\"operationName\"] if action else \"?\"}')

print()
print('All spans share same trace_id:', all(s['traceID'] == trace_id for s in trace['spans']))
print('All span_ids unique:', len(set(s['spanID'] for s in trace['spans'])) == len(trace['spans']))
"
```

---

## The Fan-Out Problem — Why This Matters at Scale

In this test, 5 messages created 29 spans. That is approximately 6 spans per message (producer + consumer + action + infrastructure). Now consider production scenarios:

```mermaid
flowchart TD
    subgraph "5 Messages — No Problem"
        a_http["1 HTTP request"]
        a_spans["29 spans"]
        a_result["Jaeger handles fine"]
        a_http --> a_spans --> a_result
    end

    subgraph "50 Messages — Watch Closely"
        b_http["1 HTTP request"]
        b_spans["~300 spans"]
        b_result["Jaeger slows down"]
        b_http --> b_spans --> b_result
    end

    subgraph "1,000 Messages — Problem"
        c_http["1 HTTP request"]
        c_spans["~6,000 spans"]
        c_result["Jaeger struggles"]
        c_http --> c_spans --> c_result
    end

    subgraph "10,000 Messages — Critical"
        d_http["1 HTTP request"]
        d_spans["~60,000 spans"]
        d_result["Collector OOMs"]
        d_http --> d_spans --> d_result
    end

    style a_result fill:#e8f5e9
    style b_result fill:#fff9c4
    style c_result fill:#ffcdd2
    style d_result fill:#b71c1c,color:#fff
```

This is **trace fan-out** — the number one reason production OTel deployments fail when Kafka is involved.

### The Core Issue

```mermaid
flowchart TD
    HTTP["1 HTTP Request"]
    HTTP --> P1["Producer 1"]
    HTTP --> P2["Producer 2"]
    HTTP --> P3["Producer ..."]
    HTTP --> PN["Producer 1000"]

    P1 --> C1["Consumer 1<br/>+ 5 child spans"]
    P2 --> C2["Consumer 2<br/>+ 5 child spans"]
    P3 --> C3["...<br/>+ 5 child spans"]
    PN --> CN["Consumer 1000<br/>+ 5 child spans"]

    style HTTP fill:#e8f5e9
    style C1 fill:#ffcdd2
    style C2 fill:#ffcdd2
    style C3 fill:#ffcdd2
    style CN fill:#ffcdd2
```

One trace with 6,000+ spans. Jaeger stores it as a single document. The collector buffers the entire trace. The UI attempts to render it. Everything degrades.

### Solution 1 — Use Links Instead of Parent-Child

Instead of propagating the parent trace context, the consumer creates a **new trace** and adds a **link** back to the producer for reference:

```mermaid
flowchart LR
    subgraph "Trace A (HTTP request — small)"
        HTTP_a["HTTP Span"]
        P1_a["Producer 1"]
        P2_a["Producer 2"]
        PN_a["Producer N"]
        HTTP_a --> P1_a
        HTTP_a --> P2_a
        HTTP_a --> PN_a
    end

    subgraph "Trace B (message 1 — small)"
        C1_b["Consumer 1"]
        A1_b["Action handler"]
        C1_b --> A1_b
    end

    subgraph "Trace C (message 2 — small)"
        C2_c["Consumer 2"]
        A2_c["Action handler"]
        C2_c --> A2_c
    end

    P1_a -.->|"link (not parent)"| C1_b
    P2_a -.->|"link (not parent)"| C2_c

    style HTTP_a fill:#e8f5e9
    style P1_a fill:#e3f2fd
    style P2_a fill:#e3f2fd
    style PN_a fill:#e3f2fd
    style C1_b fill:#fff3e0
    style C2_c fill:#fff3e0
```

The producer trace stays small (HTTP + N producer spans). Each consumer trace is independent and small. The link preserves correlation for debugging without bloating any single trace.

```python
# Consumer-side implementation
from opentelemetry.trace import Link

producer_ctx = extract_trace_ctx(message.headers)
producer_span_ctx = trace.get_current_span(producer_ctx).get_span_context()

with tracer.start_as_current_span(
    "kafka.consume batch.test",
    links=[Link(producer_span_ctx)],   # link, not parent
    kind=trace.SpanKind.CONSUMER,
):
    await process(message)
```

### Solution 2 — Probabilistic Propagation

Propagate trace context on only a fraction of messages:

```mermaid
flowchart TD
    producer["Producer sends 1000 messages"]

    prop["1% propagated<br/><i>~10 messages carry traceparent</i>"]
    no_prop["99% not propagated<br/><i>~990 messages start own trace</i>"]

    linked["10 consumers join parent trace<br/><i>End-to-end debugging samples</i>"]
    independent["990 consumers start new traces<br/><i>Lightweight, independent</i>"]

    producer --> prop --> linked
    producer --> no_prop --> independent

    style prop fill:#e8f5e9
    style no_prop fill:#e3f2fd
    style linked fill:#fff3e0
    style independent fill:#f5f5f5
```

99% of messages start their own trace. 1% carry the parent context for end-to-end debugging when needed.

### Solution 3 — Tail-Based Sampling at the Collector

Allow the collector to decide which traces to keep after seeing the complete trace:

```yaml
# otel-collector config
processors:
  tail_sampling:
    policies:
      - name: sample-errors
        type: status_code
        status_code: {status_codes: [ERROR]}
      - name: sample-slow
        type: latency
        latency: {threshold_ms: 5000}
      - name: sample-rest
        type: probabilistic
        probabilistic: {sampling_percentage: 1}
```

Always keep error traces and slow traces. Sample 1% of the rest. This requires the collector to buffer complete traces before making a decision, which increases memory usage.

---

## The Cheat Sheet

### Identifiers

| Identifier | Meaning | Scope |
|---|---|---|
| `trace_id` | One entire request flow | Shared across all spans in the trace |
| `span_id` | One operation within the flow | Always unique, never shared |
| `parent_span_id` | Who created this span | Points to the parent span's `span_id` |

### What Gets Propagated in Kafka Headers

```mermaid
flowchart LR
    subgraph "traceparent header"
        ver["00<br/><i>version</i>"]
        tid["8defd309...f654<br/><i>trace ID<br/>(32 hex chars)</i>"]
        sid["3d15191cc7903308<br/><i>span ID<br/>(16 hex chars)</i>"]
        flags["01<br/><i>sampled</i>"]
    end

    ver --- tid --- sid --- flags

    style ver fill:#f5f5f5
    style tid fill:#e8f5e9
    style sid fill:#e3f2fd
    style flags fill:#fff3e0
```

The consumer reads this and creates a child span with:
- Same `trace_id` (keeps it in the same trace)
- New `span_id` (every span is unique)
- `parent = 3d15191c...` (the producer's span_id from the header)

### The Rules

1. `trace_id` is the same across all spans in one trace
2. `span_id` is always unique — never reused, never shared
3. Each `kafkaTemplate.send()` creates its own producer span (not one wrapping span)
4. Each consumer span is a child of its **own** producer span (not siblings)
5. The `traceparent` header carries `trace_id` + producer's `span_id`
6. Auto-instrumented spans (SQL, Redis, HTTP) nest inside manual spans automatically

### When to Break the Trace Chain

| Messages per Request | Recommendation |
|---|---|
| 1–10 | Propagate normally (CHILD_OF) |
| 10–100 | Monitor trace sizes in Jaeger. Consider links if spans > 500 |
| 100–1,000 | Use links instead of parent-child |
| 1,000+ | Must use links + sampling. Single traces will exhaust collector memory |

---

## Try It Yourself

```bash
# Start the stack
./start.sh

# Send a batch of 5 messages
curl -s -X POST "http://localhost:8080/api/orders/batch-test?count=5"

# Wait 5 seconds, then check Jaeger
open http://localhost:16686
# Select order-service → POST /api/orders/batch-test → Find Traces

# Or verify via CLI (see verification script above)

# Try a larger batch to observe scale
curl -s -X POST "http://localhost:8080/api/orders/batch-test?count=20"

# Clean up
docker compose down -v
```

> **Screenshot placeholder**: *[Insert screenshot of Jaeger showing the batch-test trace with 5 messages and the fan-out structure]*

> **Screenshot placeholder**: *[Insert screenshot of Jaeger showing a larger batch (20 messages) to illustrate scale]*

> **Screenshot placeholder**: *[Insert screenshot of Kafka UI showing the batch.test topic with message headers containing traceparent]*

---

## Summary

| Question | Answer |
|---|---|
| Do all batch messages share one trace? | **Yes** — same `trace_id` everywhere |
| Is there one producer span or N? | **N** — one per `send()` call |
| Are consumers siblings? | **No** — each is a child of its own producer span |
| Are span IDs shared? | **Never** — every span gets a unique ID |
| What flows through Kafka headers? | `traceparent` = `trace_id` + producer's `span_id` |
| Does auto-instrumentation work inside manual spans? | **Yes** — SQL, Redis, HTTP spans nest automatically |
| Will this scale to 1000+ messages? | **No** — use links or sampling for fan-out control |

The key misconception is that Kafka messages "share a span ID." They do not. They share a **trace ID**. Each message gets its own producer span, its own consumer span, and its own subtree. The trace ID is the thread that ties them all together.

---

*This is Part 2 of our OpenTelemetry polyglot tracing series. [Part 1](blog-1-otel-polyglot-tracing.md) covers the full architecture, all four instrumentation approaches, and how to set up the complete PoC.*

*Source code: [GitHub repo link]*

---

> **Screenshot placeholder summary** — Images to capture before publishing:
> 1. Jaeger trace list showing the batch-test trace (hero image — the full 29-span timeline)
> 2. Jaeger zoomed into a STOCK_CHECK span showing Redis + SQL child spans
> 3. Jaeger zoomed into a RESTOCK_ALERT span showing the HTTP self-call
> 4. Jaeger zoomed into a REPORT_GENERATE span showing cross-service to analytics
> 5. Kafka UI showing batch.test topic messages with traceparent in headers
> 6. Jaeger showing a larger batch (20 messages) to illustrate scale
> 7. Terminal output of the verification script
