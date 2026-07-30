# OpenTelemetry Observability Runbook

This document explains how to set up OpenTelemetry (OTel) tracing with aesop's orchestration system, including integration with observability backends like Datadog, Honeycomb, and generic OTLP collectors.

## Overview

aesop's observability layer exports:
- **Spans**: discrete operations (orchestrator phases, agent lifecycles, fleet events)
- **Metrics**: numeric measurements (active agents, items by status, heartbeat freshness)

The sink (`tools/otel_sink.py`) reads from:
- **Heartbeats** (`state/heartbeats/*`): liveness signals with age tracking
- **Tracker** (`state/tracker.json`): items and their status
- **Orchestrator status** (`state/orchestrator-status.json`): phase and activity
- **Event store** (`state/tracker_events.db`): agent lifecycle events

## Quick Start: Dry-Run (No Infrastructure)

Test the exporter locally without any external service:

```bash
python tools/otel_sink.py --dry-run --state-dir ./state
```

Output example:
```
=== OpenTelemetry Span Tree (--dry-run) ===

Spans:
  - heartbeat.watchdog: 45.2ms [component=watchdog, age_seconds=45]
  - orchestrator.phase: 120.5ms [phase=fix, activity=running agents]
  - agent.agent-001: 5234.1ms [agent_id=agent-001, status=done]
  - tracker.items.total: 15
    - tracker.items.by_status (gauge): 5 {status=done}
    - tracker.items.by_status (gauge): 8 {status=in_progress}
    - tracker.items.by_status (gauge): 2 {status=ranked}

Metrics:
  - heartbeat.fresh.watchdog (gauge): 45 {component=watchdog}
  - heartbeat.fresh.monitor (gauge): 67 {component=monitor}
  - tracker.items.total (gauge): 15
  - tracker.items.by_status (gauge): 5 {status=done}
  - tracker.items.by_status (gauge): 8 {status=in_progress}
  - orchestrator.phase (gauge): 1 {phase=fix}

Total spans: 4
Total metrics: 6
```

## Spans Emitted

### Fleet & Wave Spans

| Span Name | Duration | Attributes | Use Case |
|-----------|----------|-----------|----------|
| `orchestrator.phase` | Phase start → end | `phase=fix\|ideation\|review`, `activity=...` | Track phase durations, phase transitions |

### Agent Spans

| Span Name | Duration | Attributes | Use Case |
|-----------|----------|-----------|----------|
| `agent.<id>` | Dispatch → completion | `agent_id=...`, `status=done\|stalled` | Agent execution timeline, failure diagnosis |

### Heartbeat Spans

| Span Name | Duration | Attributes | Use Case |
|-----------|----------|-----------|----------|
| `heartbeat.<name>` | Last beat → now | `component=watchdog\|monitor\|...`, `age_seconds=...` | Liveness tracking, detect stalls |

## Metrics Emitted

### Gauges

| Metric Name | Unit | Attributes | Meaning |
|-------------|------|-----------|---------|
| `heartbeat.fresh.*` | seconds | `component=watchdog\|monitor` | Age of each heartbeat (lower is fresher) |
| `tracker.items.total` | count | — | Total items in tracker |
| `tracker.items.by_status` | count | `status=done\|ranked\|in_progress\|accepted` | Items grouped by status |
| `orchestrator.phase` | binary (1) | `phase=fix\|ideation\|review` | Current orchestrator phase |

### Counters (Future)

- `gate.activations` — security/cost gates that fired
- `agent.errors` — agent failures
- `agent.retries` — retry events

## Setup: Datadog

### 1. Install the Datadog Agent

On your machine or CI environment:

```bash
# Linux/Mac
DD_AGENT_MAJOR_VERSION=7 bash -c "$(curl -L https://s3.amazonaws.com/dd-agent/scripts/install_agent.sh)"

# Or via package manager
apt-get install datadog-agent  # Debian/Ubuntu
brew install datadog/datadog-agent/datadog-agent  # Mac
```

### 2. Configure OTLP Receiver

Edit `/etc/datadog-agent/datadog.yaml` (or equivalent):

```yaml
otlp_config:
  receivers:
    grpc:
      endpoint: localhost:4317
```

Restart the agent:

```bash
sudo systemctl restart datadog-agent
# or on Mac:
# launchctl stop com.datadoghq.agent
# launchctl start com.datadoghq.agent
```

### 3. Configure aesop

Set the environment variable before running aesop:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
python tools/otel_sink.py --state-dir ./state
```

Or configure in `aesop.config.json`:

```json
{
  "observability": {
    "otlp_endpoint": "localhost:4317"
  }
}
```

### 4. Query in Datadog

Navigate to **APM > Traces** in Datadog:

- Search for spans: `service:aesop orchestrator.phase`
- View orchestrator phase transitions
- Drill into agent spans for failure details

Example dashboard:

```
Orchestrator Phase Duration (avg over 1h):
  - fix: 5m 30s
  - ideation: 2m 15s
  - review: 1m 45s

Active Agents (current):
  - status=done: 8
  - status=in_progress: 2
```

## Setup: Honeycomb

### 1. Get API Key

Log in to [Honeycomb](https://ui.honeycomb.io), navigate to **Account Settings > API Keys**, and generate a new key.

### 2. Configure Endpoint

Honeycomb uses gRPC OTLP at:

```
https://api.honeycomb.io:443
```

With the API key passed as a header (handled by the SDK).

### 3. Run aesop with Honeycomb

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io:443
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=${HONEYCOMB_API_KEY}"

python tools/otel_sink.py --state-dir ./state
```

Alternatively, create a `~/.otel/config.yaml`:

```yaml
exporters:
  otlp:
    endpoint: https://api.honeycomb.io:443
    headers:
      x-honeycomb-team: ${HONEYCOMB_API_KEY}
```

### 4. Query in Honeycomb

In the Honeycomb UI:

- Click **+ New Query**
- Select dataset **aesop**
- Chart `orchestrator.phase` by `phase` attribute
- Filter for `service=aesop`

Example: "Average orchestrator phase duration over the last week"

```
SELECT AVG(duration_ms)
WHERE service = 'aesop'
GROUP BY phase
LIMIT 10
```

## Setup: Generic OTLP Collector

For a local collector or custom backend, use any OTLP-compatible endpoint.

### Example: Jaeger (Docker)

```bash
docker run -d \
  -p 4317:4317 \
  -p 16686:16686 \
  jaegertracing/all-in-one:latest
```

Then configure aesop:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
python tools/otel_sink.py --state-dir ./state
```

Access the Jaeger UI at `http://localhost:16686`.

### Example: Grafana Loki (Logs) + Mimir (Metrics)

Use Grafana's OpenTelemetry Collector distribution:

```bash
wget https://github.com/open-telemetry/opentelemetry-collector/releases/download/v0.87.0/otelcontribcol_0.87.0_linux_amd64.tar.gz
tar -xzf otelcontribcol_0.87.0_linux_amd64.tar.gz
./otelcontribcol --config=config.yaml
```

With `config.yaml`:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: localhost:4317

exporters:
  prometheusremotewrite:
    endpoint: "http://mimir:9009/api/prom/push"
  loki:
    endpoint: "http://loki:3100/loki/api/v1/push"

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [loki]
    metrics:
      receivers: [otlp]
      exporters: [prometheusremotewrite]
```

## Dashboard Setup

### Recommended Panels

#### Phase Timeline

Track orchestrator phase progression over time:

```
Metric: orchestrator.phase (gauge)
Group by: phase
Time range: Last 24 hours
Chart type: Timeseries
```

#### Agent Success Rate

Monitor agent completion vs. stalls:

```
Metric: agent.* spans
Filter: status (done | stalled)
Group by: status
Time range: Current wave
Chart type: Pie chart
```

#### Tracker Item Flow

Watch items move through statuses (funnel):

```
Metrics: tracker.items.by_status (gauge)
Group by: status
Status order: ranked → accepted → in_progress → done
Chart type: Sankey/Funnel
```

#### Heartbeat Freshness

Alert if any component is stale:

```
Metric: heartbeat.fresh.* (gauge)
Alert: Any metric > 200 seconds
Severity: HIGH
```

## Troubleshooting

### No spans/metrics appearing

1. **Check --dry-run output:**
   ```bash
   python tools/otel_sink.py --dry-run --state-dir ./state
   ```
   If no spans/metrics print, state surfaces are empty or unreadable.

2. **Verify endpoint:**
   ```bash
   echo $OTEL_EXPORTER_OTLP_ENDPOINT
   # Should be set to your collector endpoint
   ```

3. **Check network connectivity:**
   ```bash
   nc -zv <host> <port>
   # Example: nc -zv localhost 4317
   ```

4. **Confirm state directory:**
   ```bash
   ls -la state/heartbeats/
   cat state/tracker.json | head -20
   ```

### SDK not installed

If you see:
```
WARNING: opentelemetry-sdk not installed. 
Install with: pip install 'opentelemetry-sdk'
```

Install the SDK (optional for --dry-run):

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
```

Or use `--dry-run` to test without the SDK:

```bash
python tools/otel_sink.py --dry-run
```

### Collector not receiving spans

1. Restart the collector:
   ```bash
   sudo systemctl restart datadog-agent  # or equivalent
   ```

2. Verify collector is listening:
   ```bash
   lsof -i :4317  # gRPC OTLP port
   ```

3. Check collector logs:
   ```bash
   # Datadog
   tail -f /var/log/datadog/agent.log

   # Jaeger
   docker logs <jaeger-container> | grep otlp
   ```

## Integration with CI/CD

### GitHub Actions

Add to `.github/workflows/test.yml`:

```yaml
- name: Export observability traces
  env:
    OTEL_EXPORTER_OTLP_ENDPOINT: ${{ secrets.OTEL_ENDPOINT }}
  run: |
    python tools/otel_sink.py --state-dir ./state || true
  # Note: || true to not fail the workflow on export errors
```

### Local Development

Export traces before committing:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
python tools/otel_sink.py --state-dir ./state

# Then commit only if happy with traces
git add .
git commit -m "feature: ..."
```

## Span/Metric Inventory

### Span Names

- `orchestrator.phase` — orchestrator phase execution (fix, ideation, review)
- `heartbeat.<name>` — liveness signal (watchdog, monitor, agents)
- `agent.<id>` — individual agent execution (dispatch → done/stalled)

### Metric Names

**Gauges:**
- `heartbeat.fresh.<name>` — seconds since last heartbeat
- `tracker.items.total` — total items in tracker
- `tracker.items.by_status` — items grouped by status
- `orchestrator.phase` — current phase (value=1 for active)

**Attributes:**
- `phase` — orchestrator phase name
- `status` — tracker item status or agent status
- `component` — heartbeat component name
- `agent_id` — unique agent identifier

## Example: Custom Dashboard in Datadog

```python
# This is a Datadog dashboard JSON (for reference)
{
  "title": "aesop Orchestration Dashboard",
  "widgets": [
    {
      "type": "timeseries",
      "query": "avg:orchestrator.phase{*}",
      "title": "Phase Timeline"
    },
    {
      "type": "query_value",
      "query": "sum:tracker.items.total{*}",
      "title": "Total Items"
    },
    {
      "type": "toplist",
      "query": "sum:tracker.items.by_status{*} by {status}",
      "title": "Items by Status"
    },
    {
      "type": "query_value",
      "query": "max:heartbeat.fresh.watchdog{*}",
      "title": "Watchdog Age (seconds)",
      "alerts": [{"threshold": 200, "severity": "HIGH"}]
    }
  ]
}
```

## References

- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [OTLP Specification](https://opentelemetry.io/docs/specs/otel/protocol/)
- [Datadog OTLP Support](https://docs.datadoghq.com/tracing/setup_overview/open_standards/otel_collector_datadog_exporter/)
- [Honeycomb OTLP Ingestion](https://docs.honeycomb.io/getting-data-in/otel-collector/)
- [Jaeger OTLP Receiver](https://github.com/jaegertracing/jaeger/blob/main/cmd/otelcontribcol/config.yaml)

---

**Last updated**: 2026-07-29
**Span inventory**: 3 span types (orchestrator.phase, heartbeat.*, agent.*)
**Metric inventory**: 4 metric types (heartbeat.fresh.*, tracker.items.*, orchestrator.phase)
