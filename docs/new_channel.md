# New SRE Channels Connection Guide

This document describes how to wire the four new shared channels introduced for Dev-1:

- `lib/channels/log.py` -> `LogChannel`
- `lib/channels/alert.py` -> `AlertChannel`
- `lib/channels/ontology.py` -> `OntologyChannel`
- `lib/channels/knowledge.py` -> `KnowledgeBaseChannel`

The contract references are from `AIDC-auto-SRE.md` (section 6.2) and `aidc-auto-sre-plan-multi-agents.md` (Agent A scope).

## 1. LogChannel

`LogChannel` is Loki-first and keeps compatibility methods for downstream callers.

Required dependencies:

- `loki`: object exposing `query(...)` and/or `query_range(...)`
- `k8s`: optional legacy alias (if passed, `LogChannel` treats it as Loki backend)
- `ssh`: optional legacy visibility only (no direct shell execution in this channel)
- `cube_studio`: optional

Example:

```python
from lib.channels.log import LogChannel

loki_backend = MyLokiAdapter(base_url="http://loki.monitoring.svc:3100")
log_channel = LogChannel(loki=loki_backend)

await log_channel.connect()
errors = await log_channel.search_pod_logs("ERROR", namespace="service", pod_selector="app=vllm")
node_logs = await log_channel.read_system_log("gpu-node-1", log_path="/var/log/syslog", tail=200)
raw = await log_channel.query_logs('{namespace="service",pod="vllm-0"}', limit=200)
await log_channel.disconnect()
```

Security defaults:

- `read_system_log` only allows fixed whitelist paths.
- `read_dmesg` rejects `filter_str` longer than 100 chars.
- selector values and regex inputs are sanitized before building LogQL.

## 2. AlertChannel

`AlertChannel` integrates with Alertmanager-compatible endpoints:

- `GET /api/v2/alerts`
- `POST /api/v2/silences`
- origin metrics via injected Prometheus-capable backend (`query_range(...)`)

You can pass either:

- `alertmanager_url` to auto-create an `httpx.AsyncClient`
- or an injected `client` with async `get/post`
- `metrics_backend` for Prometheus-origin metric queries

Example:

```python
from lib.channels.alert import AlertChannel
from lib.channels.prometheus import PrometheusChannel

metrics = PrometheusChannel(base_url="http://prometheus.monitoring.svc:9090")
alert_channel = AlertChannel(
    alertmanager_url="http://alertmanager.monitoring.svc:9093",
    metrics_backend=metrics,
)
await alert_channel.connect()

active = await alert_channel.get_active_alerts({"severity": "critical"})
origin = await alert_channel.get_origin_metrics("NodeDown", lookback="6h")
history = await alert_channel.get_alert_history("NodeDown", lookback="6h")
silence_id = await alert_channel.silence_alert(
    alert_id=active[0].alert_name,
    duration="2h",
    comment="silence during remediation",
)

await alert_channel.disconnect()
```

Output model:

- `get_active_alerts` / `get_alert_history` return `list[sre_agent.models.Alert]`.
- `get_origin_metrics` returns normalized metric points for downstream remediation tooling.

## 3. OntologyChannel

`OntologyChannel` is a facade over an injected ontology graph/store object.

Expected ontology methods:

- `find_entities(entity_type, filters)` or `query(entity_type, filters)`
- `get_blast_radius(entity_id)`
- `get_path(from_id, to_id)`
- optional `refresh_entity(entity_id)`

Example:

```python
from lib.channels.ontology import OntologyChannel

ontology_channel = OntologyChannel(ontology=my_ontology_graph)
await ontology_channel.connect()

nodes = await ontology_channel.query("node", {"status": "degraded"})
blast = await ontology_channel.get_blast_radius(nodes[0]["id"])
path = await ontology_channel.get_path("node:gpu-1-1", "service:vllm")
await ontology_channel.disconnect()
```

## 4. KnowledgeBaseChannel

`KnowledgeBaseChannel` wraps an injected knowledge store.
For Dify integration, use `DifyKnowledgeStoreAdapter` (same `search/search_runbook` channel API).

Expected store methods:

- `search(query, category=None, top_k=5)`
- `search_runbooks(symptom)` or `search_runbook(symptom)`

Example:

```python
from lib.channels.knowledge import DifyKnowledgeStoreAdapter, KnowledgeBaseChannel

store = DifyKnowledgeStoreAdapter(
    base_url="http://10.11.4.3:31984",
    api_key="YOUR_DIFY_API_KEY",
    default_dataset_id="default-dataset-id",
    runbook_dataset_id="runbook-dataset-id",
    api_prefix="/v1",
)
knowledge_channel = KnowledgeBaseChannel(store=store)
await knowledge_channel.connect()

chunks = await knowledge_channel.search("vllm p95 high", category="runbook", top_k=5)
runbooks = await knowledge_channel.search_runbook("latency spike")
await knowledge_channel.disconnect()
await store.aclose()
```

## 5. Channel Verification Commands

Run each dedicated test file:

```bash
python -m pytest lib/tests/test_log.py -q
python -m pytest lib/tests/test_alert.py -q
python -m pytest lib/tests/test_ontology.py -q
python -m pytest lib/tests/test_knowledge.py -q
```

## 6. Real-Environment Validation (Staging, Read-Only)

Real tests are embedded in existing files and marked with `@pytest.mark.real`.
They are gated and skipped unless `SRE_REAL_TEST=1`.
You can provide `SRE_*` settings either via shell env vars or a local JSON file.

### Local Config File (No Env Export Needed)

1. Copy [real_test.example.json](D:/dev/cube-studio/cube-studio/lib/tests/real_test.example.json) to `lib/tests/real_test.local.json`.
2. Fill in your staging endpoints/IDs.
3. Run pytest; real-test helper code auto-loads `SRE_*` values from `lib/tests/real_test.local.json`.

Notes:

- `lib/tests/real_test.local.json` is ignored by git.
- Exported shell env vars still override file values.
- You can use a custom file path with env var `SRE_REAL_CONFIG_FILE=<path>`.

### Real Test Env Contract

Common:

- `SRE_REAL_TEST=1`
- `SRE_HTTP_TIMEOUT_SEC=15`
- `SRE_ALLOW_WRITE_OPS=0` (default; write actions skipped)

Log/Loki:

- `SRE_LOKI_URL`
- `SRE_TEST_NAMESPACE`
- `SRE_TEST_POD`
- `SRE_TEST_NODE`
- Optional auth: `SRE_LOKI_TOKEN` or `SRE_LOKI_AUTH_HEADER`, `SRE_LOKI_TENANT_ID`

Alert/Prometheus:

- `SRE_ALERTMANAGER_URL`
- `SRE_PROMETHEUS_URL`
- `SRE_TEST_ALERT_NAME` (must exist in staging)
- Optional auth: `SRE_ALERTMANAGER_TOKEN` / `SRE_PROMETHEUS_TOKEN` (or `*_AUTH_HEADER`)

Ontology:

- `SRE_ONTOLOGY_URL`
- `SRE_ONTOLOGY_QUERY_PATH`
- `SRE_ONTOLOGY_BLAST_PATH`
- `SRE_ONTOLOGY_PATH_PATH`
- `SRE_TEST_ENTITY_TYPE`
- `SRE_TEST_FROM_ID`
- `SRE_TEST_TO_ID`
- Optional write path (only when `SRE_ALLOW_WRITE_OPS=1`): `SRE_ONTOLOGY_REFRESH_PATH`

Knowledge:

- `SRE_KB_URL`
- `SRE_KB_API_PREFIX` (default `/v1`)
- `SRE_KB_TOKEN` (Dify API key)
- `SRE_KB_DEFAULT_DATASET_ID`
- `SRE_KB_RUNBOOK_DATASET_ID`
- `SRE_TEST_KB_QUERY`
- `SRE_TEST_KB_SYMPTOM`
- Optional: `SRE_TEST_KB_CATEGORY`

### Real Test Commands

Smoke (log + alert):

```bash
python -m pytest -m real -q lib/tests/test_log.py lib/tests/test_alert.py
```

Full real run:

```bash
python -m pytest -m real -q lib/tests/test_log.py lib/tests/test_alert.py lib/tests/test_ontology.py lib/tests/test_knowledge.py
```

Using custom config file path:

```bash
set SRE_REAL_CONFIG_FILE=C:\temp\my_real_test.json
python -m pytest -m real -q lib/tests/test_log.py
```

Acceptance behavior:

- Missing required env vars => test is skipped with explicit reason.
- Env provided but endpoint/auth/query fails => test fails.
- Selector-based log checks (`query_logs`, `search_pod_logs`) fail on empty results.
