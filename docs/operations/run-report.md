# Agentic SDLC `run-report`

`run-report` is a local reporting helper. It lives outside `.agentic-sdlc/` because no
workflow depends on it:

```text
tools/run-report                             # the script
tools/config/model-pricing.example.json      # copy to tools/config/model-pricing.json and populate current rates
```

It reads run timing and step events from the CAO API. It does not infer token counts
or cost from terminal output. Add a `usage.json` sidecar only when the provider gives
authoritative usage data, and configure pricing separately when an estimate is useful.

## Run

```bash
./tools/run-report plan-PAY-1427-1
```

JSON:

```bash
./tools/run-report plan-PAY-1427-1 --json
```

Explicit telemetry:

```bash
./tools/run-report plan-PAY-1427-1 \
  --usage-file .agentic-sdlc/runtime/PAY-1427/plan-PAY-1427-1/usage.json \
  --pricing-file tools/config/model-pricing.json
```

The command defaults to `http://127.0.0.1:9889`. Use the following options when the
CAO server differs from the default:

```bash
./tools/run-report RUN_ID \
  --api-base http://127.0.0.1:9889 \
  --token "$CAO_API_TOKEN" \
  --timeout 15
```

`CAO_API_BASE_URL` and `CAO_API_TOKEN` provide the default API URL and bearer token.
The report never prints the token. `--repo` selects the repository used to discover
`.agentic-sdlc/runtime/**/usage.json`; `--output PATH` writes the report to a file;
`--json` selects machine-readable output.

## Data sources

Timing is read from CAO:

```text
GET /workflows/runs/{run_id}
GET /workflows/runs/{run_id}/events?after_seq=0
```

CAO already journals `started_at`, `finished_at`, and timestamped
`step.started` / `step.completed` / `step.failed` events.

CAO does not currently journal model token counts or monetary cost in the workflow
event schema. `run-report` therefore never guesses token usage from terminal text.

A provider-specific usage collector should create:

```text
.agentic-sdlc/runtime/<ticket>/<run-id>/usage.json
```

Example:

```json
{
  "schema_version": "1.0",
  "currency": "USD",
  "steps": {
    "context-normalize-v1": {
      "provider": "claude_code",
      "model": "provider-model-id",
      "tokens": {
        "input": 18420,
        "output": 2400,
        "cache_read": 10000,
        "cache_write": 1200
      }
    }
  }
}
```

If the provider exposes an authoritative monetary amount, use:

```json
{
  "reported_cost": 0.1234,
  "currency": "USD"
}
```

`reported_cost` takes precedence over token pricing.

## Pricing

The pricing file has this form:

```json
{
  "currency": "USD",
  "models": {
    "provider-model-id": {
      "rates_per_million": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.3,
        "cache_write": 3.75
      }
    }
  }
}
```

The numbers above illustrate the schema only. Populate the file from the provider's
current official pricing and version-control pricing changes deliberately.

If usage contains a token category for which no rate exists, that stage cost is
`N/A`. If any agent stage lacks trustworthy usage/pricing, the report shows only
the **known partial cost** and refuses to label it as the total.

This is intentional to avoid understated cost reporting.

## Recommended package structure

```text
tools/
├── run-report
└── config/
    └── model-pricing.json
.agentic-sdlc/
└── runtime/
    └── <ticket>/
        └── <run-id>/
            └── usage.json
```
